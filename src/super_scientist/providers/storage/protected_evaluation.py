from __future__ import annotations

import stat
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
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
from sqlalchemy.engine import Connection

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    sha256_hex,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import create_database_engine
from super_scientist.providers.storage.domain_records import (
    HarnessMetricRecord,
    HarnessMetricRepository,
    MetricValueRecord,
)

_PROTECTED_MEDIA_TYPE = "application/octet-stream"
_STABLE_IDENTIFIER_ADAPTER: TypeAdapter[StableIdentifier] = TypeAdapter(StableIdentifier)
_SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)

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
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class MetricValue(_StrictFrozenModel):
    metric_id: StableIdentifier
    value: Decimal

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("metric value must be finite")
        return value


class ProtectedCheckerResult(_StrictFrozenModel):
    result_id: StableIdentifier
    campaign_id: StableIdentifier
    task_id: StableIdentifier
    expected_output_hash: Sha256Hex
    candidate_output_hash: Sha256Hex
    checker_id: StableIdentifier
    checker_version: StableIdentifier
    outcome: AssessmentOutcome
    metric_values: tuple[MetricValue, ...]
    evaluated_at: UtcTimestamp

    @field_validator("metric_values")
    @classmethod
    def require_unique_metric_ids(
        cls,
        value: tuple[MetricValue, ...],
    ) -> tuple[MetricValue, ...]:
        metric_ids = tuple(item.metric_id for item in value)
        if len(set(metric_ids)) != len(metric_ids):
            raise ValueError("metric_values must have unique metric identifiers")
        return value


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


@runtime_checkable
class ProtectedAnswerReader(Protocol):
    def read_expected_output(self, task_id: str) -> bytes: ...


@runtime_checkable
class ProtectedIntegrityAuditor(Protocol):
    def verify_integrity(self) -> ProtectedIntegrityReport: ...


@runtime_checkable
class ProtectedResultGateway(Protocol):
    def append_result(self, result: ProtectedCheckerResult) -> None: ...


class ProtectedEvaluationStore:
    """Own protected metadata and bytes outside the ordinary repository graph."""

    __slots__ = ("_artifacts", "_engine")

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
        self._resources()
        return _AnswerReaderCapability(self._read_expected_output)

    def integrity_auditor(self) -> ProtectedIntegrityAuditor:
        self._resources()
        return _IntegrityAuditorCapability(self._verify_integrity)

    def close(self) -> None:
        engine = self._engine
        self._engine = None
        self._artifacts = None
        if engine is not None:
            engine.dispose()

    def _resources(self) -> tuple[Engine, FileArtifactStore]:
        if self._engine is None or self._artifacts is None:
            raise RuntimeError("protected evaluation store is closed")
        return self._engine, self._artifacts

    def _read_expected_output(self, task_id: str) -> bytes:
        validated_task_id = _STABLE_IDENTIFIER_ADAPTER.validate_python(task_id)
        engine, artifacts = self._resources()
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
            raise ValueError("protected expected output is unavailable")
        content_hash, size_bytes = _validated_expected_output_row(dict(row))
        try:
            return artifacts.read(_artifact_ref(content_hash, size_bytes))
        except ValueError:
            raise ValueError("protected expected output failed integrity verification") from None

    def _verify_integrity(self) -> ProtectedIntegrityReport:
        engine, artifacts = self._resources()
        with engine.connect() as connection:
            rows = tuple(
                dict(row)
                for row in connection.execute(
                    select(_protected_expected_outputs).order_by(
                        _protected_expected_outputs.c.task_id
                    )
                ).mappings()
            )
        findings: list[ProtectedIntegrityFinding] = []
        for row in rows:
            task_id = row.get("task_id")
            raw_hash = row.get("expected_output_hash")
            safe_task_id = (
                task_id if isinstance(task_id, str) and task_id.strip() else "invalid-task"
            )
            safe_hash = raw_hash if isinstance(raw_hash, str) else "0" * 64
            validated_task_id = "invalid-task"
            try:
                validated_task_id = _STABLE_IDENTIFIER_ADAPTER.validate_python(safe_task_id)
                content_hash, size_bytes = _validated_expected_output_row(row)
                artifacts.read(_artifact_ref(content_hash, size_bytes))
            except (TypeError, ValueError):
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


class _AnswerReaderCapability:
    __slots__ = ("_reader",)

    def __init__(self, reader: Callable[[str], bytes]) -> None:
        self._reader = reader

    def read_expected_output(self, task_id: str) -> bytes:
        return self._reader(task_id)


class _IntegrityAuditorCapability:
    __slots__ = ("_auditor",)

    def __init__(self, auditor: Callable[[], ProtectedIntegrityReport]) -> None:
        self._auditor = auditor

    def verify_integrity(self) -> ProtectedIntegrityReport:
        return self._auditor()


class _MainDatabaseProtectedResultGateway:
    __slots__ = ("_repository",)

    def __init__(self, connection: Connection) -> None:
        self._repository = HarnessMetricRepository(connection)

    def append_result(self, result: ProtectedCheckerResult) -> None:
        validated = ProtectedCheckerResult.model_validate(result.model_dump(mode="python"))
        record = HarnessMetricRecord(
            result_id=validated.result_id,
            campaign_id=validated.campaign_id,
            task_id=validated.task_id,
            expected_output_hash=validated.expected_output_hash,
            candidate_output_hash=validated.candidate_output_hash,
            checker_id=validated.checker_id,
            checker_version=validated.checker_version,
            outcome=validated.outcome,
            metric_values=tuple(
                MetricValueRecord(metric_id=item.metric_id, value=item.value)
                for item in validated.metric_values
            ),
            evaluated_at=validated.evaluated_at,
        )
        self._repository.add(record.result_id, record, record.evaluated_at)


def create_protected_result_gateway(connection: Connection) -> ProtectedResultGateway:
    """Create the sole cross-store shape; it owns only an ordinary main-DB repository."""

    return _MainDatabaseProtectedResultGateway(connection)


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
    "ProtectedCheckerResult",
    "ProtectedEvaluationStore",
    "ProtectedExpectedOutputReceipt",
    "ProtectedIntegrityAuditor",
    "ProtectedIntegrityFinding",
    "ProtectedIntegrityReport",
    "ProtectedResultGateway",
    "create_protected_result_gateway",
]
