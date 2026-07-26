from __future__ import annotations

import base64
import stat
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from multiprocessing import get_context
from multiprocessing.process import BaseProcess
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol, Self, runtime_checkable
from weakref import WeakSet

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)
from sqlalchemy import (
    CheckConstraint,
    Column,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    insert,
    select,
)
from sqlalchemy.engine import Connection as SqlConnection
from sqlalchemy.exc import SQLAlchemyError

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.harness_eval.models import MetricValue, ProtectedCheckerResult
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import create_database_engine
from super_scientist.providers.storage.domain_records import (
    HarnessCampaignRepository,
    HarnessMetricRecord,
    HarnessMetricRepository,
    MetricValueRecord,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError

_PROTECTED_MEDIA_TYPE = "application/octet-stream"
_STABLE_IDENTIFIER_ADAPTER: TypeAdapter[StableIdentifier] = TypeAdapter(StableIdentifier)
_SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)
_WORKER_JOIN_TIMEOUT_SECONDS = 5.0
_WORKER_RESPONSE_TIMEOUT_SECONDS = 10.0
_MAX_WORKER_MESSAGE_BYTES = 64 * 1024 * 1024

_protected_metadata = MetaData()
_protected_expected_outputs = Table(
    "protected_expected_outputs",
    _protected_metadata,
    Column("task_id", String(160), primary_key=True),
    Column("expected_output_hash", String(64), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    CheckConstraint(
        "length(expected_output_hash) = 64 AND expected_output_hash NOT GLOB '*[^0-9a-f]*'",
        name="ck_protected_expected_output_hash",
    ),
    CheckConstraint("size_bytes >= 0", name="ck_protected_expected_output_size"),
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


class ProtectedCapabilityError(ValueError):
    """A typed, non-leaking failure returned by a role-scoped worker."""

    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = _STABLE_IDENTIFIER_ADAPTER.validate_python(code)
        super().__init__(message)


class _WorkerRequest(_StrictFrozenModel):
    request_id: StableIdentifier
    operation: StableIdentifier
    payload: object | None


class _WorkerResponse(_StrictFrozenModel):
    request_id: StableIdentifier
    ok: bool
    payload: object | None
    error_code: StableIdentifier | None
    error_message: NonBlankText | None

    @model_validator(mode="after")
    def require_exact_error_shape(self) -> Self:
        if self.ok and (self.error_code is not None or self.error_message is not None):
            raise ValueError("worker response error fields must exactly match failure status")
        if not self.ok and (self.error_code is None or self.error_message is None):
            raise ValueError("worker response error fields must exactly match failure status")
        return self


class ProtectedExpectedOutputReceipt(_StrictFrozenModel):
    task_id: StableIdentifier
    expected_output_hash: Sha256Hex
    size_bytes: int = Field(ge=0)


class ProtectedIntegrityFinding(_StrictFrozenModel):
    task_id: StableIdentifier
    expected_output_hash: Sha256Hex
    code: StableIdentifier


class ProtectedIntegrityReport(_StrictFrozenModel):
    valid: bool
    checked_outputs: int = Field(ge=0)
    findings: tuple[ProtectedIntegrityFinding, ...]


class _ProtectedOutputUnavailable(ValueError):
    pass


@runtime_checkable
class ProtectedAnswerReader(Protocol):
    def read_expected_output(self, task_id: str) -> bytes: ...

    def close(self) -> None: ...


@runtime_checkable
class ProtectedIntegrityAuditor(Protocol):
    def verify_integrity(self) -> ProtectedIntegrityReport: ...

    def close(self) -> None: ...


@runtime_checkable
class ProtectedResultValidator(Protocol):
    def validate_result(self, result: ProtectedCheckerResult) -> ProtectedCheckerResult: ...

    def close(self) -> None: ...


@runtime_checkable
class ProtectedResultGateway(Protocol):
    def append_result(self, result: ProtectedCheckerResult) -> None: ...

    def close(self) -> None: ...


class _ProcessTransport(Protocol):
    def send_bytes(self, value: bytes) -> None: ...

    def recv_bytes(self, maxlength: int | None = None) -> bytes: ...

    def poll(self, timeout: float = 0.0) -> bool: ...

    def close(self) -> None: ...


class ProtectedEvaluationStore:
    """Own protected metadata and bytes outside the ordinary repository graph."""

    __slots__ = ("_artifacts", "_engine", "_lock", "_role_capabilities", "_root")

    def __init__(self, protected_root: Path) -> None:
        root = _prepare_private_root(protected_root)
        database_path = root / "protected.sqlite3"
        _require_regular_or_missing(database_path)
        engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
        try:
            _protected_metadata.create_all(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TRIGGER IF NOT EXISTS protected_expected_outputs_no_update "
                    "BEFORE UPDATE ON protected_expected_outputs "
                    "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
                )
                connection.exec_driver_sql(
                    "CREATE TRIGGER IF NOT EXISTS protected_expected_outputs_no_delete "
                    "BEFORE DELETE ON protected_expected_outputs "
                    "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
                )
            artifacts = FileArtifactStore(root / "artifacts")
        except BaseException:
            engine.dispose()
            raise
        self._engine: Engine | None = engine
        self._artifacts: FileArtifactStore | None = artifacts
        self._lock = RLock()
        self._root = root
        self._role_capabilities: WeakSet[_ProcessCapability] = WeakSet()

    def add_expected_output(
        self,
        task_id: str,
        expected_output: bytes,
    ) -> ProtectedExpectedOutputReceipt:
        validated_task_id = _STABLE_IDENTIFIER_ADAPTER.validate_python(task_id)
        if not isinstance(expected_output, bytes):
            raise TypeError("protected expected output must be bytes")
        digest = sha256_hex(expected_output)
        engine, artifacts = self._resources()
        with engine.begin() as connection:
            existing = (
                connection.execute(
                    select(_protected_expected_outputs).where(
                        _protected_expected_outputs.c.task_id == validated_task_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                stored_hash, stored_size = _validated_expected_output_row(dict(existing))
                if stored_hash != digest or stored_size != len(expected_output):
                    raise ValueError("protected expected output identity is already bound")
                artifacts.read(_artifact_ref(stored_hash, stored_size))
                return ProtectedExpectedOutputReceipt(
                    task_id=validated_task_id,
                    expected_output_hash=stored_hash,
                    size_bytes=stored_size,
                )
            artifact = artifacts.put(expected_output, _PROTECTED_MEDIA_TYPE)
            connection.execute(
                insert(_protected_expected_outputs).values(
                    task_id=validated_task_id,
                    expected_output_hash=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
        return ProtectedExpectedOutputReceipt(
            task_id=validated_task_id,
            expected_output_hash=digest,
            size_bytes=len(expected_output),
        )

    def answer_reader(self) -> ProtectedAnswerReader:
        with self._lock:
            self._resources()
            capability = _start_answer_reader_capability(self._root)
            self._role_capabilities.add(capability)
            return capability

    def integrity_auditor(self) -> ProtectedIntegrityAuditor:
        with self._lock:
            self._resources()
            capability = _start_integrity_auditor_capability(self._root)
            self._role_capabilities.add(capability)
            return capability

    def close(self) -> None:
        with self._lock:
            capabilities = tuple(self._role_capabilities)
            self._role_capabilities.clear()
            engine = self._engine
            self._engine = None
            self._artifacts = None
        for capability in capabilities:
            capability.close()
        if engine is not None:
            engine.dispose()

    def _resources(self) -> tuple[Engine, FileArtifactStore]:
        if self._engine is None or self._artifacts is None:
            raise RuntimeError("protected evaluation store is closed")
        return self._engine, self._artifacts


class _ProcessCapability:
    __slots__ = (
        "__weakref__",
        "_channel_usable",
        "_closed",
        "_lock",
        "_process",
        "_request_number",
        "_transport",
    )

    def __init__(self, transport: _ProcessTransport, process: BaseProcess) -> None:
        self._transport = transport
        self._process = process
        self._lock = RLock()
        self._channel_usable = True
        self._closed = False
        self._request_number = 0
        try:
            self._receive_response("worker-startup")
        except ProtectedCapabilityError:
            self._channel_usable = False
            raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                if self._channel_usable and self._process.is_alive():
                    try:
                        self._exchange("CLOSE", None)
                    except (EOFError, OSError, ProtectedCapabilityError):
                        self._channel_usable = False
            finally:
                self._closed = True
                self._channel_usable = False
                with suppress(OSError):
                    self._transport.close()
                self._process.join(_WORKER_JOIN_TIMEOUT_SECONDS)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(_WORKER_JOIN_TIMEOUT_SECONDS)
                self._process.close()

    def _request[ResponseT](
        self,
        operation: str,
        payload: object | None,
        decoder: Callable[[object | None], ResponseT],
    ) -> ResponseT:
        with self._lock:
            if self._closed:
                raise ProtectedCapabilityError(
                    "CAPABILITY_CLOSED",
                    "protected capability is closed",
                )
            if not self._channel_usable:
                raise ProtectedCapabilityError(
                    "CAPABILITY_CHANNEL_UNUSABLE",
                    "protected capability channel is unusable",
                )
            try:
                response_payload = self._exchange(operation, payload)
                return decoder(response_payload)
            except ProtectedCapabilityError as error:
                if error.code in {"CAPABILITY_WORKER_UNAVAILABLE", "INVALID_WORKER_RESPONSE"}:
                    self._channel_usable = False
                raise
            except (EOFError, OSError) as error:
                self._channel_usable = False
                raise ProtectedCapabilityError(
                    "CAPABILITY_WORKER_UNAVAILABLE",
                    "protected capability worker is unavailable",
                ) from error

    def _exchange(self, operation: str, payload: object | None) -> object | None:
        self._request_number += 1
        request_id = f"request-{self._request_number}"
        request = _WorkerRequest(
            request_id=request_id,
            operation=operation,
            payload=payload,
        )
        self._transport.send_bytes(request.model_dump_json().encode("utf-8"))
        return self._receive_response(request_id)

    def _receive_response(self, request_id: str) -> object | None:
        try:
            response_ready = self._transport.poll(_WORKER_RESPONSE_TIMEOUT_SECONDS)
        except (EOFError, OSError) as error:
            raise ProtectedCapabilityError(
                "CAPABILITY_WORKER_UNAVAILABLE",
                "protected capability worker is unavailable",
            ) from error
        if not response_ready:
            raise ProtectedCapabilityError(
                "CAPABILITY_WORKER_UNAVAILABLE",
                "protected capability worker is unavailable",
            )
        try:
            raw_response = self._transport.recv_bytes(_MAX_WORKER_MESSAGE_BYTES)
        except (EOFError, OSError) as error:
            raise ProtectedCapabilityError(
                "CAPABILITY_WORKER_UNAVAILABLE",
                "protected capability worker is unavailable",
            ) from error
        response: _WorkerResponse | None = None
        with suppress(TypeError, ValueError):
            response = _WorkerResponse.model_validate_json(raw_response)
        if response is None:
            raise ProtectedCapabilityError(
                "INVALID_WORKER_RESPONSE",
                "protected capability worker returned an invalid response",
            ) from None
        if response.request_id != request_id:
            raise ProtectedCapabilityError(
                "INVALID_WORKER_RESPONSE",
                "protected capability worker returned an invalid response",
            )
        if not response.ok:
            if response.error_code is None or response.error_message is None:
                raise ProtectedCapabilityError(
                    "INVALID_WORKER_RESPONSE",
                    "protected capability worker returned an invalid response",
                )
            raise ProtectedCapabilityError(response.error_code, response.error_message)
        return response.payload


class _AnswerReaderCapability(_ProcessCapability):
    def read_expected_output(self, task_id: str) -> bytes:
        validated_task_id = _STABLE_IDENTIFIER_ADAPTER.validate_python(task_id)
        return self._request(
            "READ_EXPECTED_OUTPUT",
            validated_task_id,
            _decode_expected_output_payload,
        )


class _IntegrityAuditorCapability(_ProcessCapability):
    def verify_integrity(self) -> ProtectedIntegrityReport:
        return self._request("VERIFY_INTEGRITY", None, _decode_integrity_report_payload)


class _ProtectedResultValidatorCapability(_ProcessCapability):
    def validate_result(self, result: ProtectedCheckerResult) -> ProtectedCheckerResult:
        validated = _require_exact_checker_result(result)
        return self._request(
            "VALIDATE_RESULT",
            validated.model_dump(mode="json"),
            _decode_checker_result_payload,
        )


class _CoordinatorProtectedResultGateway:
    __slots__ = ("_campaigns", "_closed", "_connection", "_lock", "_metrics")

    def __init__(self, connection: SqlConnection) -> None:
        self._connection = connection
        self._campaigns = HarnessCampaignRepository(connection)
        self._metrics = HarnessMetricRepository(connection)
        self._lock = RLock()
        self._closed = False

    def append_result(self, result: ProtectedCheckerResult) -> None:
        with self._lock:
            if self._closed:
                raise ProtectedCapabilityError(
                    "CAPABILITY_CLOSED",
                    "protected capability is closed",
                )
            validated = _require_exact_checker_result(result)
            if self._connection.closed or not self._connection.in_transaction():
                raise ProtectedCapabilityError(
                    "RESULT_APPEND_REJECTED",
                    "protected checker result append was rejected",
                )
            try:
                if self._campaigns.get(validated.campaign_id) is None:
                    raise ProtectedCapabilityError(
                        "REFERENCED_CAMPAIGN_UNAVAILABLE",
                        "referenced campaign state is unavailable",
                    )
                record = _harness_metric_record(validated)
                self._metrics.add(record.result_id, record, record.evaluated_at)
            except ProtectedCapabilityError:
                raise
            except (SQLAlchemyError, StorageIntegrityError, TypeError, ValueError):
                pass
            else:
                return
            raise ProtectedCapabilityError(
                "RESULT_APPEND_REJECTED",
                "protected checker result append was rejected",
            ) from None

    def close(self) -> None:
        with self._lock:
            self._closed = True


def _require_exact_checker_result(result: object) -> ProtectedCheckerResult:
    if type(result) is not ProtectedCheckerResult:
        raise ProtectedCapabilityError(
            "INVALID_CHECKER_RESULT",
            "protected checker result is invalid",
        ) from None
    validated: ProtectedCheckerResult | None = None
    with suppress(TypeError, ValueError):
        validated = ProtectedCheckerResult.model_validate(result)
    if validated is None:
        raise ProtectedCapabilityError(
            "INVALID_CHECKER_RESULT",
            "protected checker result is invalid",
        ) from None
    return validated


def _decode_expected_output_payload(payload: object | None) -> bytes:
    decoded: bytes | None = None
    if (
        isinstance(payload, dict)
        and set(payload) == {"base64"}
        and isinstance(payload.get("base64"), str)
    ):
        with suppress(ValueError):
            decoded = base64.b64decode(payload["base64"], validate=True)
    if decoded is None:
        raise _invalid_worker_response_error() from None
    return decoded


def _decode_integrity_report_payload(payload: object | None) -> ProtectedIntegrityReport:
    decoded: ProtectedIntegrityReport | None = None
    with suppress(TypeError, ValueError):
        decoded = ProtectedIntegrityReport.model_validate_json(canonical_json_bytes(payload))
    if decoded is None:
        raise _invalid_worker_response_error() from None
    return decoded


def _decode_checker_result_payload(payload: object | None) -> ProtectedCheckerResult:
    decoded: ProtectedCheckerResult | None = None
    with suppress(TypeError, ValueError):
        decoded = ProtectedCheckerResult.model_validate_json(canonical_json_bytes(payload))
    if decoded is None:
        raise _invalid_worker_response_error() from None
    return decoded


def _invalid_worker_response_error() -> ProtectedCapabilityError:
    return ProtectedCapabilityError(
        "INVALID_WORKER_RESPONSE",
        "protected capability worker returned an invalid response",
    )


def create_protected_result_gateway(connection: SqlConnection) -> ProtectedResultGateway:
    """Create the coordinator adapter over the caller's active main-DB transaction."""

    if not isinstance(connection, SqlConnection) or connection.closed:
        raise TypeError("result gateway requires an open SQLAlchemy connection")
    if not connection.in_transaction():
        raise ValueError("result gateway requires an active transaction")
    return _CoordinatorProtectedResultGateway(connection)


def create_protected_result_validator() -> ProtectedResultValidator:
    """Create the evaluator-facing worker that returns only a validated result DTO."""

    return _start_result_validator_capability()


def _start_answer_reader_capability(protected_root: Path) -> _AnswerReaderCapability:
    return _spawn_process_capability(
        _run_protected_role_worker,
        ("reader", str(protected_root)),
        _AnswerReaderCapability,
    )


def _start_integrity_auditor_capability(
    protected_root: Path,
) -> _IntegrityAuditorCapability:
    return _spawn_process_capability(
        _run_protected_role_worker,
        ("auditor", str(protected_root)),
        _IntegrityAuditorCapability,
    )


def _start_result_validator_capability() -> _ProtectedResultValidatorCapability:
    return _spawn_process_capability(
        _run_result_validation_worker,
        (),
        _ProtectedResultValidatorCapability,
    )


def _spawn_process_capability[CapabilityT: _ProcessCapability](
    target: Callable[..., None],
    target_arguments: tuple[object, ...],
    capability_type: type[CapabilityT],
) -> CapabilityT:
    context = get_context("spawn")
    parent_transport, worker_transport = context.Pipe(duplex=True)
    process = context.Process(
        target=target,
        args=(*target_arguments, worker_transport),
        daemon=True,
    )
    try:
        process.start()
    except BaseException:
        parent_transport.close()
        worker_transport.close()
        process.close()
        raise
    worker_transport.close()
    try:
        return capability_type(parent_transport, process)
    except BaseException:
        parent_transport.close()
        process.join(_WORKER_JOIN_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(_WORKER_JOIN_TIMEOUT_SECONDS)
        process.close()
        raise


def _run_protected_role_worker(
    role: Literal["reader", "auditor"],
    protected_root: str,
    transport: _ProcessTransport,
) -> None:
    engine: Engine | None = None
    try:
        root = _prepare_private_root(Path(protected_root))
        database_path = root / "protected.sqlite3"
        _require_regular_or_missing(database_path)
        engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
        artifacts = FileArtifactStore(root / "artifacts")
    except BaseException:
        _send_worker_error(
            transport,
            "worker-startup",
            "CAPABILITY_WORKER_INITIALIZATION_FAILED",
            "protected capability worker failed to initialize",
        )
        transport.close()
        return
    _send_worker_success(transport, "worker-startup", None)
    try:
        while True:
            request = _receive_worker_request(transport)
            if request is None:
                return
            if request.operation == "CLOSE":
                _send_worker_success(transport, request.request_id, None)
                return
            if role == "reader" and request.operation == "READ_EXPECTED_OUTPUT":
                if not isinstance(request.payload, str):
                    _send_invalid_worker_request(transport, request.request_id)
                    continue
                try:
                    output = _read_expected_output(engine, artifacts, request.payload)
                except _ProtectedOutputUnavailable:
                    _send_worker_error(
                        transport,
                        request.request_id,
                        "PROTECTED_OUTPUT_UNAVAILABLE",
                        "protected expected output is unavailable",
                    )
                except (SQLAlchemyError, OSError, StorageIntegrityError):
                    _send_worker_error(
                        transport,
                        request.request_id,
                        "PROTECTED_STORE_INTEGRITY_FAILURE",
                        "protected store failed integrity verification",
                    )
                except (TypeError, ValueError):
                    _send_worker_error(
                        transport,
                        request.request_id,
                        "PROTECTED_OUTPUT_INTEGRITY_FAILURE",
                        "protected expected output failed integrity verification",
                    )
                else:
                    _send_worker_success(transport, request.request_id, output)
                continue
            if role == "auditor" and request.operation == "VERIFY_INTEGRITY":
                if request.payload is not None:
                    _send_invalid_worker_request(transport, request.request_id)
                    continue
                try:
                    report = _verify_integrity(engine, artifacts)
                except (SQLAlchemyError, OSError, StorageIntegrityError, TypeError, ValueError):
                    report = _protected_store_integrity_report()
                _send_worker_success(transport, request.request_id, report)
                continue
            _send_worker_error(
                transport,
                request.request_id,
                "UNAUTHORIZED_OPERATION",
                "operation is not allowed for this protected capability",
            )
    finally:
        engine.dispose()
        transport.close()


def _run_result_validation_worker(transport: _ProcessTransport) -> None:
    _send_worker_success(transport, "worker-startup", None)
    try:
        while True:
            request = _receive_worker_request(transport)
            if request is None:
                return
            if request.operation == "CLOSE":
                _send_worker_success(transport, request.request_id, None)
                return
            if request.operation != "VALIDATE_RESULT":
                _send_worker_error(
                    transport,
                    request.request_id,
                    "UNAUTHORIZED_OPERATION",
                    "operation is not allowed for the protected result validator",
                )
                continue
            try:
                result = ProtectedCheckerResult.model_validate_json(
                    canonical_json_bytes(request.payload)
                )
            except (TypeError, ValueError):
                _send_worker_error(
                    transport,
                    request.request_id,
                    "INVALID_CHECKER_RESULT",
                    "protected checker result is invalid",
                )
                continue
            _send_worker_success(transport, request.request_id, result)
    finally:
        transport.close()


def _receive_worker_request(transport: _ProcessTransport) -> _WorkerRequest | None:
    while True:
        try:
            raw_request = transport.recv_bytes(_MAX_WORKER_MESSAGE_BYTES)
        except (EOFError, OSError):
            return None
        try:
            return _WorkerRequest.model_validate_json(raw_request)
        except (TypeError, ValueError):
            _send_invalid_worker_request(transport, "invalid-request")


def _send_invalid_worker_request(
    transport: _ProcessTransport,
    request_id: str,
) -> None:
    _send_worker_error(
        transport,
        request_id,
        "INVALID_REQUEST",
        "protected capability request is invalid",
    )


def _send_worker_success(
    transport: _ProcessTransport,
    request_id: str,
    payload: object | None,
) -> None:
    wire_payload: object | None
    if isinstance(payload, bytes):
        wire_payload = {"base64": base64.b64encode(payload).decode("ascii")}
    elif isinstance(payload, BaseModel):
        wire_payload = payload.model_dump(mode="json")
    else:
        wire_payload = payload
    transport.send_bytes(
        _WorkerResponse(
            request_id=request_id,
            ok=True,
            payload=wire_payload,
            error_code=None,
            error_message=None,
        )
        .model_dump_json()
        .encode("utf-8")
    )


def _send_worker_error(
    transport: _ProcessTransport,
    request_id: str,
    error_code: str,
    error_message: str,
) -> None:
    transport.send_bytes(
        _WorkerResponse(
            request_id=request_id,
            ok=False,
            payload=None,
            error_code=error_code,
            error_message=error_message,
        )
        .model_dump_json()
        .encode("utf-8")
    )


def _harness_metric_record(result: ProtectedCheckerResult) -> HarnessMetricRecord:
    return HarnessMetricRecord(
        result_id=result.result_id,
        campaign_id=result.campaign_id,
        task_id=result.task_id,
        expected_output_hash=result.expected_output_hash,
        candidate_output_hash=result.candidate_output_hash,
        checker_id=result.checker_id,
        checker_version=result.checker_version,
        outcome=result.outcome,
        metric_values=tuple(
            MetricValueRecord(metric_id=item.metric_id, value=item.value)
            for item in result.metric_values
        ),
        evaluated_at=result.evaluated_at,
    )


def _read_expected_output(
    engine: Engine,
    artifacts: FileArtifactStore,
    task_id: str,
) -> bytes:
    validated_task_id = _STABLE_IDENTIFIER_ADAPTER.validate_python(task_id)
    with engine.connect() as connection:
        row = (
            connection.execute(
                select(_protected_expected_outputs).where(
                    _protected_expected_outputs.c.task_id == validated_task_id
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise _ProtectedOutputUnavailable("protected expected output is unavailable")
    try:
        content_hash, size_bytes = _validated_expected_output_row(dict(row))
        return artifacts.read(_artifact_ref(content_hash, size_bytes))
    except (OSError, TypeError, ValueError):
        raise ValueError("protected expected output failed integrity verification") from None


def _verify_integrity(
    engine: Engine,
    artifacts: FileArtifactStore,
) -> ProtectedIntegrityReport:
    with engine.connect() as connection:
        rows = tuple(
            dict(row)
            for row in connection.execute(
                select(_protected_expected_outputs).order_by(_protected_expected_outputs.c.task_id)
            ).mappings()
        )
    findings: list[ProtectedIntegrityFinding] = []
    for row in rows:
        task_id = row.get("task_id")
        raw_hash = row.get("expected_output_hash")
        safe_task_id = task_id if isinstance(task_id, str) and task_id.strip() else "invalid-task"
        safe_hash = raw_hash if isinstance(raw_hash, str) else "0" * 64
        validated_task_id = "invalid-task"
        try:
            validated_task_id = _STABLE_IDENTIFIER_ADAPTER.validate_python(safe_task_id)
            content_hash, size_bytes = _validated_expected_output_row(row)
            artifacts.read(_artifact_ref(content_hash, size_bytes))
        except (OSError, TypeError, ValueError):
            try:
                finding_hash = _SHA256_ADAPTER.validate_python(safe_hash)
            except ValueError:
                finding_hash = "0" * 64
            findings.append(
                ProtectedIntegrityFinding(
                    task_id=validated_task_id,
                    expected_output_hash=finding_hash,
                    code="PROTECTED_ARTIFACT_INTEGRITY_FAILURE",
                )
            )
    return ProtectedIntegrityReport(
        valid=not findings,
        checked_outputs=len(rows),
        findings=tuple(findings),
    )


def _protected_store_integrity_report() -> ProtectedIntegrityReport:
    return ProtectedIntegrityReport(
        valid=False,
        checked_outputs=0,
        findings=(
            ProtectedIntegrityFinding(
                task_id="protected-store",
                expected_output_hash="0" * 64,
                code="PROTECTED_STORE_INTEGRITY_FAILURE",
            ),
        ),
    )


def _validated_expected_output_row(row: dict[str, object]) -> tuple[str, int]:
    raw_hash = row.get("expected_output_hash")
    raw_size = row.get("size_bytes")
    try:
        content_hash = _SHA256_ADAPTER.validate_python(raw_hash)
    except ValueError as error:
        raise ValueError("protected metadata failed integrity verification") from error
    if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 0:
        raise ValueError("protected metadata failed integrity verification")
    return content_hash, raw_size


def _artifact_ref(content_hash: str, size_bytes: int) -> ArtifactRef:
    return ArtifactRef(
        sha256=content_hash,
        size_bytes=size_bytes,
        media_type=_PROTECTED_MEDIA_TYPE,
        relative_path=f"sha256/{content_hash[:2]}/{content_hash}",
    )


def _prepare_private_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise TypeError("protected root must be a pathlib.Path")
    absolute = root.absolute()
    _assert_no_link_or_reparse(absolute)
    absolute.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(absolute)
    resolved = absolute.resolve()
    if not resolved.is_dir():
        raise ValueError("protected root must be a directory")
    return resolved


def _require_regular_or_missing(path: Path) -> None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(entry.st_mode):
        raise ValueError("protected database path must be a regular file")


def _assert_no_link_or_reparse(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            entry = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(entry.st_mode) or (
            getattr(entry, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError("protected namespace contains a symlink or reparse point")


__all__ = [
    "MetricValue",
    "ProtectedAnswerReader",
    "ProtectedCapabilityError",
    "ProtectedCheckerResult",
    "ProtectedEvaluationStore",
    "ProtectedExpectedOutputReceipt",
    "ProtectedIntegrityAuditor",
    "ProtectedIntegrityFinding",
    "ProtectedIntegrityReport",
    "ProtectedResultGateway",
    "ProtectedResultValidator",
    "create_protected_result_gateway",
    "create_protected_result_validator",
]
