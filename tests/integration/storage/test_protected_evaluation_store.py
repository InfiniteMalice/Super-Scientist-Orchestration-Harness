from __future__ import annotations

import gc
import inspect
import json
import pickle
import sqlite3
import sys
import traceback
import weakref
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from multiprocessing.process import BaseProcess
from pathlib import Path, PurePath
from threading import Barrier
from types import ModuleType

import pytest
from pydantic import ValidationError
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.providers.storage import protected_evaluation as protected_evaluation_module
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import HarnessMetricRepository
from super_scientist.providers.storage.protected_evaluation import (
    MetricValue,
    ProtectedAnswerReader,
    ProtectedCapabilityError,
    ProtectedCheckerResult,
    ProtectedEvaluationStore,
    ProtectedIntegrityAuditor,
    ProtectedIntegrityReport,
    ProtectedResultGateway,
    create_protected_result_gateway,
)
from super_scientist.providers.storage.repositories import RepositorySet

_GRAPH_LEAF_TYPES = (
    str,
    bytes,
    bytearray,
    int,
    float,
    bool,
    type(None),
    Decimal,
    datetime,
    type,
    ModuleType,
)
_FORBIDDEN_CAPABILITY_TYPES = (
    ProtectedEvaluationStore,
    FileArtifactStore,
    HarnessMetricRepository,
    RepositorySet,
    Engine,
    Connection,
    PurePath,
)
_GRAPH_TERMINAL_TYPES = (
    *_GRAPH_LEAF_TYPES,
    ProtectedEvaluationStore,
    FileArtifactStore,
    Engine,
    Connection,
    PurePath,
)
_ROLE_OPERATIONS = {
    "add_expected_output",
    "answer_reader",
    "integrity_auditor",
    "read_expected_output",
    "verify_integrity",
    "append_result",
    "validate_result",
}


@pytest.mark.integration
def test_protected_answers_are_not_in_main_database(tmp_path: Path) -> None:
    main_path = tmp_path / "main.db"
    main_url = f"sqlite+pysqlite:///{main_path.as_posix()}"
    upgrade_database(main_url)
    secret = b"secret-answer-material"

    store = ProtectedEvaluationStore(tmp_path / "protected")
    try:
        receipt = store.add_expected_output("task-1", secret)
        assert receipt.expected_output_hash
        assert secret not in main_path.read_bytes()
        assert secret not in (tmp_path / "protected" / "protected.sqlite3").read_bytes()
        assert "protected_expected_outputs" not in main_path.read_bytes().decode(
            "utf-8", errors="ignore"
        )
    finally:
        store.close()


@pytest.mark.integration
def test_store_returns_role_specific_capabilities_and_detects_corruption(
    tmp_path: Path,
) -> None:
    protected_root = tmp_path / "protected"
    store = ProtectedEvaluationStore(protected_root)
    try:
        secret = b"held-out-answer"
        receipt = store.add_expected_output("task-1", secret)
        reader = store.answer_reader()
        auditor = store.integrity_auditor()

        assert isinstance(reader, ProtectedAnswerReader)
        assert isinstance(auditor, ProtectedIntegrityAuditor)
        assert reader.read_expected_output("task-1") == secret
        clean = auditor.verify_integrity()
        assert clean.valid is True
        assert clean.findings == ()
        assert secret not in clean.model_dump_json().encode()

        artifact = (
            protected_root
            / "artifacts"
            / "sha256"
            / receipt.expected_output_hash[:2]
            / receipt.expected_output_hash
        )
        artifact.write_bytes(b"corrupt")
        with pytest.raises(ProtectedCapabilityError) as read_error:
            reader.read_expected_output("task-1")
        assert read_error.value.code == "PROTECTED_OUTPUT_INTEGRITY_FAILURE"
        assert "path" not in str(read_error.value).lower()
        assert secret not in str(read_error.value).encode()
        corrupted = auditor.verify_integrity()
        assert corrupted.valid is False
        assert corrupted.findings[0].code == "PROTECTED_ARTIFACT_INTEGRITY_FAILURE"
        assert secret not in corrupted.model_dump_json().encode()
        assert "path" not in corrupted.model_dump_json().lower()
        assert "reference" not in corrupted.model_dump_json().lower()
    finally:
        store.close()


@pytest.mark.integration
def test_result_gateway_crosses_only_typed_hashes_and_aggregates(tmp_path: Path) -> None:
    main_url = f"sqlite+pysqlite:///{(tmp_path / 'main.db').as_posix()}"
    upgrade_database(main_url)
    engine = create_database_engine(main_url)
    try:
        with engine.begin() as connection:
            _seed_campaign(connection)
        with DatabaseUnitOfWork(engine) as unit_of_work:
            connection = unit_of_work.connection
            assert connection is not None
            gateway = create_protected_result_gateway(connection)
            try:
                assert isinstance(gateway, ProtectedResultGateway)
                gateway.append_result(_checker_result())
                stored = HarnessMetricRepository(connection).get("result-1")
                assert stored is not None
                assert stored.expected_output_hash == "a" * 64
                assert "expected_output" not in type(stored).model_fields
                assert '"expected_output":' not in stored.model_dump_json().lower()
                assert "answer" not in stored.model_dump_json().lower()
            finally:
                gateway.close()
        with engine.connect() as connection:
            stored = HarnessMetricRepository(connection).get("result-1")
            assert stored is not None
    finally:
        engine.dispose()


@pytest.mark.integration
def test_result_gateway_append_rolls_back_with_supplied_database_uow(tmp_path: Path) -> None:
    main_url = f"sqlite+pysqlite:///{(tmp_path / 'main.db').as_posix()}"
    upgrade_database(main_url)
    engine = create_database_engine(main_url)
    try:
        with engine.begin() as connection:
            _seed_campaign(connection)

        with (
            pytest.raises(RuntimeError, match="force coordinator rollback"),
            DatabaseUnitOfWork(engine) as unit_of_work,
        ):
            connection = unit_of_work.connection
            assert connection is not None
            gateway = create_protected_result_gateway(connection)
            try:
                gateway.append_result(_checker_result())
                assert HarnessMetricRepository(connection).get("result-1") is not None
            finally:
                gateway.close()
            raise RuntimeError("force coordinator rollback")

        with engine.connect() as connection:
            assert HarnessMetricRepository(connection).get("result-1") is None
    finally:
        engine.dispose()


def test_result_gateway_schema_cannot_carry_answer_material() -> None:
    fields = ProtectedCheckerResult.model_fields
    assert "expected_output" not in fields
    assert "answer_bytes" not in fields
    assert "answer_reference" not in fields
    payload = {
        "result_id": "result-1",
        "campaign_id": "campaign-1",
        "task_id": "task-1",
        "expected_output_hash": "a" * 64,
        "candidate_output_hash": "b" * 64,
        "checker_id": "checker-1",
        "checker_version": "checker-v1",
        "outcome": AssessmentOutcome.PASSED,
        "metric_values": ({"metric_id": "correctness", "value": Decimal("1.0")},),
        "evaluated_at": datetime(2026, 7, 20, tzinfo=UTC),
        "answer_bytes": b"secret",
    }
    with pytest.raises(ValidationError):
        ProtectedCheckerResult.model_validate(payload)


@pytest.mark.integration
def test_result_validator_rejects_answer_bearing_subclass_without_exception_leak(
    tmp_path: Path,
) -> None:
    secret = b"validator-held-out-answer"
    protected_path = str(tmp_path / "protected" / "answers.bin")
    result = _AnswerBearingProtectedCheckerResult(
        **_checker_result().model_dump(mode="python"),
        answer_bytes=secret,
        answer_path=protected_path,
    )
    validator = protected_evaluation_module.create_protected_result_validator()
    try:
        with pytest.raises(ProtectedCapabilityError) as captured:
            validator.validate_result(result)
        _assert_fixed_non_leaking_result_error(
            captured.value,
            sensitive_values=(secret.decode(), protected_path),
        )
    finally:
        validator.close()


@pytest.mark.integration
def test_result_gateway_rejects_malformed_dto_without_exception_leak(tmp_path: Path) -> None:
    secret = b"gateway-held-out-answer"
    protected_path = str(tmp_path / "protected" / "answers.bin")
    malformed = _MalformedProtectedCheckerResult(secret, protected_path)
    main_url = f"sqlite+pysqlite:///{(tmp_path / 'main.db').as_posix()}"
    upgrade_database(main_url)
    engine = create_database_engine(main_url)
    try:
        with DatabaseUnitOfWork(engine) as unit_of_work:
            connection = unit_of_work.connection
            assert connection is not None
            gateway = create_protected_result_gateway(connection)
            try:
                with pytest.raises(ProtectedCapabilityError) as captured:
                    gateway.append_result(malformed)  # type: ignore[arg-type]
                _assert_fixed_non_leaking_result_error(
                    captured.value,
                    sensitive_values=(secret.decode(), protected_path),
                )
            finally:
                gateway.close()
    finally:
        engine.dispose()


def test_result_gateway_requires_an_open_active_sqlalchemy_transaction(
    tmp_path: Path,
) -> None:
    memory_engine = create_database_engine("sqlite+pysqlite:///:memory:")
    try:
        with memory_engine.connect() as connection:
            with pytest.raises(ValueError, match="active transaction"):
                create_protected_result_gateway(connection)
            with connection.begin():
                gateway = create_protected_result_gateway(connection)
                gateway.close()
    finally:
        memory_engine.dispose()

    file_engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'closed.db').as_posix()}"
    )
    connection = file_engine.connect()
    connection.close()
    try:
        with pytest.raises(TypeError, match="open SQLAlchemy connection"):
            create_protected_result_gateway(connection)
    finally:
        file_engine.dispose()


@pytest.mark.integration
def test_protected_store_is_absent_from_repository_set(tmp_path: Path) -> None:
    main_url = f"sqlite+pysqlite:///{(tmp_path / 'main.db').as_posix()}"
    upgrade_database(main_url)
    engine = create_database_engine(main_url)
    try:
        with engine.connect() as connection:
            repositories = RepositorySet(connection)
            assert not hasattr(repositories, "protected")
            assert not hasattr(repositories, "expected_outputs")
            object_values = tuple(vars(repositories).values())
            assert not any(isinstance(value, ProtectedEvaluationStore) for value in object_values)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_protected_expected_output_identity_is_append_only_and_replay_safe(tmp_path: Path) -> None:
    protected_root = tmp_path / "protected"
    store = ProtectedEvaluationStore(protected_root)
    try:
        first = store.add_expected_output("task-1", b"first")
        replay = store.add_expected_output("task-1", b"first")
        assert replay == first
        with pytest.raises(ValueError, match="already bound"):
            store.add_expected_output("task-1", b"changed")
        with pytest.raises(TypeError, match="must be bytes"):
            store.add_expected_output("task-2", bytearray(b"not-bytes"))  # type: ignore[arg-type]
        with pytest.raises(ProtectedCapabilityError, match="unavailable") as unavailable:
            store.answer_reader().read_expected_output("missing-task")
        assert unavailable.value.code == "PROTECTED_OUTPUT_UNAVAILABLE"

        protected_url = f"sqlite+pysqlite:///{(protected_root / 'protected.sqlite3').as_posix()}"
        protected_engine = create_database_engine(protected_url)
        try:
            with (
                protected_engine.begin() as connection,
                pytest.raises(IntegrityError, match="append-only table"),
            ):
                connection.exec_driver_sql(
                    "UPDATE protected_expected_outputs SET size_bytes = size_bytes"
                )
        finally:
            protected_engine.dispose()
    finally:
        store.close()

    store.close()
    with pytest.raises(RuntimeError, match="closed"):
        store.answer_reader()


def test_protected_contracts_reject_duplicate_and_nonfinite_metrics() -> None:
    with pytest.raises(ValidationError):
        MetricValue(metric_id="score", value=Decimal("Infinity"))
    duplicate = MetricValue(metric_id="score", value=Decimal("1"))
    payload = {
        "result_id": "result-1",
        "campaign_id": "campaign-1",
        "task_id": "task-1",
        "expected_output_hash": "a" * 64,
        "candidate_output_hash": "b" * 64,
        "checker_id": "checker-1",
        "checker_version": "checker-v1",
        "outcome": AssessmentOutcome.PASSED,
        "metric_values": (duplicate, duplicate),
        "evaluated_at": datetime(2026, 7, 20, tzinfo=UTC),
    }
    with pytest.raises(ValidationError):
        ProtectedCheckerResult.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "request_id": "request-1",
            "ok": True,
            "payload": None,
            "error_code": "UNEXPECTED_ERROR",
            "error_message": "unexpected error",
        },
        {
            "request_id": "request-1",
            "ok": False,
            "payload": None,
            "error_code": None,
            "error_message": None,
        },
    ),
)
def test_worker_response_contract_requires_exact_error_shape(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="exactly match failure status"):
        protected_evaluation_module._WorkerResponse.model_validate(payload)


def test_capability_response_timeout_is_typed_and_close_terminates_worker() -> None:
    transport = _TimeoutTransport()
    process = _StubbornProcess()
    capability = protected_evaluation_module._AnswerReaderCapability(transport, process)

    with pytest.raises(ProtectedCapabilityError) as captured:
        capability.read_expected_output("task-1")
    assert captured.value.code == "CAPABILITY_WORKER_UNAVAILABLE"
    sent_after_timeout = len(transport.sent_frames)

    with pytest.raises(ProtectedCapabilityError) as poisoned:
        capability.read_expected_output("task-1")
    assert poisoned.value.code == "CAPABILITY_CHANNEL_UNUSABLE"
    assert len(transport.sent_frames) == sent_after_timeout

    capability.close()
    assert transport.closed is True
    assert process.terminated is True
    assert process.closed is True
    assert transport.poll_timeouts
    assert all(timeout > 0 for timeout in transport.poll_timeouts)


def test_protocol_desynchronization_permanently_poisons_capability_channel() -> None:
    transport = _MismatchedResponseTransport()
    process = _StubbornProcess()
    capability = protected_evaluation_module._AnswerReaderCapability(transport, process)

    with pytest.raises(ProtectedCapabilityError) as mismatch:
        capability.read_expected_output("task-1")
    assert mismatch.value.code == "INVALID_WORKER_RESPONSE"
    sent_after_mismatch = len(transport.sent_frames)

    with pytest.raises(ProtectedCapabilityError) as poisoned:
        capability.read_expected_output("task-1")
    assert poisoned.value.code == "CAPABILITY_CHANNEL_UNUSABLE"
    assert len(transport.sent_frames) == sent_after_mismatch
    capability.close()


@pytest.mark.parametrize("role", ("reader", "auditor", "validator"))
def test_role_payload_decode_failure_atomically_poisons_capability_channel(role: str) -> None:
    sensitive_value = f"{role}-worker-payload-secret"
    if role == "reader":
        malformed_payload: object = {"base64": f"not-base64-{sensitive_value}"}
        valid_payload: object = {"base64": "c2VjcmV0"}
        capability_type = protected_evaluation_module._AnswerReaderCapability
    elif role == "auditor":
        malformed_payload = {
            "valid": True,
            "checked_outputs": 0,
            "findings": [],
            "answer_path": sensitive_value,
        }
        valid_payload = ProtectedIntegrityReport(
            valid=True,
            checked_outputs=0,
            findings=(),
        ).model_dump(mode="json")
        capability_type = protected_evaluation_module._IntegrityAuditorCapability
    else:
        malformed_payload = _checker_result().model_dump(mode="json")
        malformed_payload["answer_bytes"] = sensitive_value
        valid_payload = _checker_result().model_dump(mode="json")
        capability_type = protected_evaluation_module._ProtectedResultValidatorCapability

    transport = _SequencedPayloadTransport((malformed_payload, valid_payload))
    process = _StubbornProcess()
    capability = capability_type(transport, process)
    if role == "reader":
        invoke = partial(capability.read_expected_output, "task-1")
    elif role == "auditor":
        invoke = capability.verify_integrity
    else:
        invoke = partial(capability.validate_result, _checker_result())

    try:
        with pytest.raises(ProtectedCapabilityError) as first:
            invoke()
        assert first.value.code == "INVALID_WORKER_RESPONSE"
        sent_after_invalid_response = len(transport.sent_frames)

        with pytest.raises(ProtectedCapabilityError) as poisoned:
            invoke()
        assert poisoned.value.code == "CAPABILITY_CHANNEL_UNUSABLE"
        assert len(transport.sent_frames) == sent_after_invalid_response
        _assert_fixed_non_leaking_worker_response_error(first.value, sensitive_value)
    finally:
        capability.close()


@pytest.mark.integration
def test_role_worker_initialization_failure_is_typed_and_non_leaking(tmp_path: Path) -> None:
    invalid_root = tmp_path / "root-is-a-file"
    invalid_root.write_text("not a protected directory", encoding="utf-8")

    with pytest.raises(ProtectedCapabilityError) as captured:
        protected_evaluation_module._start_answer_reader_capability(invalid_root)

    assert captured.value.code == "CAPABILITY_WORKER_INITIALIZATION_FAILED"
    assert str(invalid_root) not in str(captured.value)


def test_protected_root_and_database_shapes_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"pathlib\.Path"):
        ProtectedEvaluationStore(str(tmp_path / "protected"))  # type: ignore[arg-type]

    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises((FileExistsError, ValueError, OSError)):
        ProtectedEvaluationStore(root_file)

    protected_root = tmp_path / "protected-with-directory-db"
    protected_root.mkdir()
    (protected_root / "protected.sqlite3").mkdir()
    with pytest.raises(ValueError, match="regular file"):
        ProtectedEvaluationStore(protected_root)

    protected_root = tmp_path / "protected-with-file-artifact-root"
    protected_root.mkdir()
    artifact_root = protected_root / "artifacts"
    artifact_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(FileExistsError):
        ProtectedEvaluationStore(protected_root)
    artifact_root.unlink()
    recovered = ProtectedEvaluationStore(protected_root)
    recovered.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case_name", "mutation_sql"),
    (
        (
            "invalid-hash",
            "UPDATE protected_expected_outputs "
            "SET expected_output_hash = 'not-a-valid-hash' WHERE task_id = 'task-1'",
        ),
        (
            "invalid-size",
            "UPDATE protected_expected_outputs SET size_bytes = -1 WHERE task_id = 'task-1'",
        ),
    ),
)
def test_corrupt_protected_metadata_fails_closed_without_leaking(
    tmp_path: Path,
    case_name: str,
    mutation_sql: str,
) -> None:
    protected_root = tmp_path / case_name
    secret = b"metadata-corruption-secret"
    store = ProtectedEvaluationStore(protected_root)
    try:
        store.add_expected_output("task-1", secret)
        with (
            closing(sqlite3.connect(protected_root / "protected.sqlite3")) as connection,
            connection,
        ):
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("DROP TRIGGER protected_expected_outputs_no_update")
            connection.execute(mutation_sql)

        auditor = store.integrity_auditor()
        report = auditor.verify_integrity()
        assert report.valid is False
        assert report.findings[0].code == "PROTECTED_ARTIFACT_INTEGRITY_FAILURE"
        assert secret not in report.model_dump_json().encode()

        reader = store.answer_reader()
        with pytest.raises(ProtectedCapabilityError) as captured:
            reader.read_expected_output("task-1")
        assert captured.value.code == "PROTECTED_OUTPUT_INTEGRITY_FAILURE"
        assert secret not in str(captured.value).encode()
    finally:
        store.close()


@pytest.mark.integration
def test_structurally_corrupt_protected_database_has_fixed_non_leaking_failures(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    protected_root = tmp_path / "protected"
    secret = b"structural-database-secret"
    store = ProtectedEvaluationStore(protected_root)
    try:
        store.add_expected_output("task-1", secret)
        reader = store.answer_reader()
        auditor = store.integrity_auditor()
        with (
            closing(sqlite3.connect(protected_root / "protected.sqlite3")) as connection,
            connection,
        ):
            connection.execute("DROP TABLE protected_expected_outputs")

        with pytest.raises(ProtectedCapabilityError) as read_failure:
            reader.read_expected_output("task-1")
        audit_failure: ProtectedCapabilityError | None = None
        report = None
        try:
            report = auditor.verify_integrity()
        except ProtectedCapabilityError as error:
            audit_failure = error

        captured = capfd.readouterr()
        leaked = f"{captured.out}\n{captured.err}"
        assert "Traceback" not in leaked
        assert str(protected_root) not in leaked
        assert secret.decode() not in leaked
        assert "no such table" not in leaked.lower()
        assert read_failure.value.code == "PROTECTED_STORE_INTEGRITY_FAILURE"
        assert str(protected_root) not in str(read_failure.value)
        assert audit_failure is None
        assert report is not None
        assert report.valid is False
        assert report.checked_outputs == 0
        assert report.findings[0].code == "PROTECTED_STORE_INTEGRITY_FAILURE"
        assert str(protected_root) not in report.model_dump_json()
        assert secret not in report.model_dump_json().encode()
    finally:
        store.close()


@pytest.mark.integration
@pytest.mark.parametrize("artifact_state", ("missing", "non-regular"))
def test_unavailable_protected_artifact_is_non_leaking_error_and_finding(
    tmp_path: Path,
    artifact_state: str,
) -> None:
    protected_root = tmp_path / artifact_state
    secret = b"unavailable-artifact-secret"
    store = ProtectedEvaluationStore(protected_root)
    try:
        receipt = store.add_expected_output("task-1", secret)
        reader = store.answer_reader()
        auditor = store.integrity_auditor()
        artifact = (
            protected_root
            / "artifacts"
            / "sha256"
            / receipt.expected_output_hash[:2]
            / receipt.expected_output_hash
        )
        artifact.unlink()
        if artifact_state == "non-regular":
            artifact.mkdir()

        with pytest.raises(ProtectedCapabilityError) as read_failure:
            reader.read_expected_output("task-1")
        assert read_failure.value.code == "PROTECTED_OUTPUT_INTEGRITY_FAILURE"
        assert str(protected_root) not in str(read_failure.value)
        assert secret not in str(read_failure.value).encode()

        report = auditor.verify_integrity()
        assert report.valid is False
        assert report.findings[0].code == "PROTECTED_ARTIFACT_INTEGRITY_FAILURE"
        assert str(protected_root) not in report.model_dump_json()
        assert secret not in report.model_dump_json().encode()
    finally:
        store.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("role", "factory_name", "allowed_operation"),
    (
        ("answer reader", "answer_reader", "read_expected_output"),
        ("integrity auditor", "integrity_auditor", "verify_integrity"),
    ),
)
def test_protected_role_capability_graphs_have_only_their_intended_authority(
    tmp_path: Path,
    role: str,
    factory_name: str,
    allowed_operation: str,
) -> None:
    protected_root = tmp_path / role.replace(" ", "-")
    store = ProtectedEvaluationStore(protected_root)
    try:
        capability = getattr(store, factory_name)()
        violations = _capability_graph_violations(
            capability,
            {allowed_operation},
            forbidden_references={
                str(protected_root.resolve()),
                protected_root.resolve().as_posix(),
            },
        )
        assert not violations, f"{role} excess authority:\n" + "\n".join(violations)
    finally:
        store.close()
    worker_processes = tuple(
        value for _, value in _walk_capability_graph(capability) if isinstance(value, BaseProcess)
    )
    assert len(worker_processes) == 1
    with pytest.raises(ValueError, match=r"process.*closed"):
        worker_processes[0].is_alive()
    with pytest.raises(ProtectedCapabilityError) as closed:
        if allowed_operation == "read_expected_output":
            capability.read_expected_output("task-1")
        else:
            capability.verify_integrity()
    assert closed.value.code == "CAPABILITY_CLOSED"


@pytest.mark.integration
def test_store_does_not_strongly_retain_a_closed_role_capability(tmp_path: Path) -> None:
    store = ProtectedEvaluationStore(tmp_path / "protected")
    try:
        reader = store.answer_reader()
        reference = weakref.ref(reader)
        reader.close()
        del reader
        gc.collect()
        assert reference() is None
    finally:
        store.close()


@pytest.mark.integration
def test_concurrent_close_is_idempotent_and_releases_process_state(tmp_path: Path) -> None:
    store = ProtectedEvaluationStore(tmp_path / "protected")
    try:
        reader = store.answer_reader()
        worker_processes = tuple(
            value for _, value in _walk_capability_graph(reader) if isinstance(value, BaseProcess)
        )
        assert len(worker_processes) == 1

        with ThreadPoolExecutor(max_workers=32) as executor:
            tuple(executor.map(lambda _: reader.close(), range(32)))

        with pytest.raises(ValueError, match=r"process.*closed"):
            worker_processes[0].is_alive()
    finally:
        store.close()


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle-count regression")
def test_repeated_role_capabilities_do_not_leak_windows_handles(tmp_path: Path) -> None:
    store = ProtectedEvaluationStore(tmp_path / "protected")
    try:
        store.add_expected_output("task-1", b"handle-regression-secret")
        baseline = _windows_process_handle_count()
        for _ in range(12):
            reader = store.answer_reader()
            assert reader.read_expected_output("task-1") == b"handle-regression-secret"
            reader.close()
        del reader
        gc.collect()
        assert _windows_process_handle_count() <= baseline + 4
    finally:
        store.close()


@pytest.mark.integration
def test_evaluator_result_validator_graph_has_no_database_or_gateway_authority(
    tmp_path: Path,
) -> None:
    main_path = tmp_path / "main.db"
    main_url = f"sqlite+pysqlite:///{main_path.as_posix()}"
    upgrade_database(main_url)
    validator = protected_evaluation_module.create_protected_result_validator()
    try:
        assert validator.validate_result(_checker_result()) == _checker_result()
        violations = _capability_graph_violations(
            validator,
            {"validate_result"},
            forbidden_references={
                str(main_path.resolve()),
                main_path.resolve().as_posix(),
                main_url,
            },
        )
        assert not violations, "result validator excess authority:\n" + "\n".join(violations)
        worker_processes = tuple(
            value
            for _, value in _walk_capability_graph(validator)
            if isinstance(value, BaseProcess)
        )
        assert len(worker_processes) == 1
    finally:
        validator.close()
    with pytest.raises(ValueError, match=r"process.*closed"):
        worker_processes[0].is_alive()


@pytest.mark.integration
def test_coordinator_gateway_transparently_owns_the_supplied_uow_authority(
    tmp_path: Path,
) -> None:
    main_url = f"sqlite+pysqlite:///{(tmp_path / 'main.db').as_posix()}"
    upgrade_database(main_url)
    engine = create_database_engine(main_url)
    try:
        with DatabaseUnitOfWork(engine) as unit_of_work:
            connection = unit_of_work.connection
            assert connection is not None
            gateway = create_protected_result_gateway(connection)
            try:
                graph = tuple(_walk_capability_graph(gateway))
                assert any(value is connection for _, value in graph)
                assert any(isinstance(value, HarnessMetricRepository) for _, value in graph)
                assert not any(isinstance(value, BaseProcess) for _, value in graph)
                assert not callable(getattr(gateway, "validate_result", None))
            finally:
                gateway.close()
            with pytest.raises(ProtectedCapabilityError) as closed:
                gateway.append_result(_checker_result())
            assert closed.value.code == "CAPABILITY_CLOSED"
    finally:
        engine.dispose()


@pytest.mark.integration
def test_role_capability_reports_worker_loss_with_a_typed_safe_error(tmp_path: Path) -> None:
    secret = b"worker-loss-secret"
    store = ProtectedEvaluationStore(tmp_path / "protected")
    try:
        store.add_expected_output("task-1", secret)
        reader = store.answer_reader()
        worker_processes = tuple(
            value for _, value in _walk_capability_graph(reader) if isinstance(value, BaseProcess)
        )
        assert len(worker_processes) == 1
        worker_processes[0].terminate()
        worker_processes[0].join(5)

        with pytest.raises(ProtectedCapabilityError) as captured:
            reader.read_expected_output("task-1")
        assert captured.value.code == "CAPABILITY_WORKER_UNAVAILABLE"
        assert secret not in str(captured.value).encode()
        reader.close()
        reader.close()
    finally:
        store.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("factory_name", "forbidden_operation", "payload"),
    (
        ("answer_reader", "VERIFY_INTEGRITY", None),
        ("integrity_auditor", "READ_EXPECTED_OUTPUT", "task-1"),
    ),
)
def test_protected_worker_role_allowlists_reject_cross_role_messages(
    tmp_path: Path,
    factory_name: str,
    forbidden_operation: str,
    payload: object,
) -> None:
    secret = b"role-isolated-answer"
    store = ProtectedEvaluationStore(tmp_path / factory_name)
    try:
        store.add_expected_output("task-1", secret)
        capability = getattr(store, factory_name)()
        response = _send_raw_worker_operation(
            capability,
            operation=forbidden_operation,
            payload=payload,
        )
        assert response["ok"] is False
        assert response["error_code"] == "UNAUTHORIZED_OPERATION"
        assert secret not in repr(response).encode()
        if factory_name == "answer_reader":
            assert capability.read_expected_output("task-1") == secret
        else:
            assert capability.verify_integrity().valid is True
    finally:
        store.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("factory_name", "operation", "invalid_payload"),
    (
        ("answer_reader", "READ_EXPECTED_OUTPUT", None),
        ("integrity_auditor", "VERIFY_INTEGRITY", "unexpected-payload"),
    ),
)
def test_protected_worker_allowed_operations_reject_invalid_payloads(
    tmp_path: Path,
    factory_name: str,
    operation: str,
    invalid_payload: object,
) -> None:
    secret = b"invalid-payload-secret"
    store = ProtectedEvaluationStore(tmp_path / factory_name)
    try:
        store.add_expected_output("task-1", secret)
        capability = getattr(store, factory_name)()
        response = _send_raw_worker_operation(
            capability,
            operation=operation,
            payload=invalid_payload,
        )
        assert response["ok"] is False
        assert response["error_code"] == "INVALID_REQUEST"
        assert secret not in repr(response).encode()
        if factory_name == "answer_reader":
            assert capability.read_expected_output("task-1") == secret
        else:
            assert capability.verify_integrity().valid is True
    finally:
        store.close()


@pytest.mark.integration
def test_shared_answer_reader_serializes_32_concurrent_requests(tmp_path: Path) -> None:
    secret = b"shared-reader-secret"
    store = ProtectedEvaluationStore(tmp_path / "protected")
    try:
        store.add_expected_output("task-1", secret)
        reader = store.answer_reader()
        barrier = Barrier(32)

        def read_once(_: int) -> bytes:
            barrier.wait()
            return reader.read_expected_output("task-1")

        with ThreadPoolExecutor(max_workers=32) as executor:
            outputs = tuple(executor.map(read_once, range(32)))
        assert outputs == (secret,) * 32
    finally:
        store.close()


@pytest.mark.integration
def test_coordinator_gateway_serializes_32_concurrent_uow_appends(tmp_path: Path) -> None:
    main_url = f"sqlite+pysqlite:///{(tmp_path / 'main.db').as_posix()}"
    upgrade_database(main_url)
    engine = create_database_engine(main_url)
    try:
        with engine.begin() as connection:
            _seed_campaign(connection)

        with DatabaseUnitOfWork(engine) as unit_of_work:
            connection = unit_of_work.connection
            assert connection is not None
            gateway = create_protected_result_gateway(connection)
            barrier = Barrier(32)

            def append_once(index: int) -> None:
                barrier.wait()
                gateway.append_result(_checker_result(result_id=f"result-{index}"))

            try:
                with ThreadPoolExecutor(max_workers=32) as executor:
                    tuple(executor.map(append_once, range(32)))
                repository = HarnessMetricRepository(connection)
                assert all(repository.get(f"result-{index}") is not None for index in range(32))
            finally:
                gateway.close()

        with engine.connect() as connection:
            repository = HarnessMetricRepository(connection)
            assert all(repository.get(f"result-{index}") is not None for index in range(32))
    finally:
        engine.dispose()


@pytest.mark.integration
def test_gateway_can_append_a_campaign_created_in_the_same_database_uow(
    tmp_path: Path,
) -> None:
    main_url = f"sqlite+pysqlite:///{(tmp_path / 'main.db').as_posix()}"
    upgrade_database(main_url)
    engine = create_database_engine(main_url)
    try:
        with DatabaseUnitOfWork(engine) as unit_of_work:
            connection = unit_of_work.connection
            assert connection is not None
            _seed_campaign(connection)
            gateway = create_protected_result_gateway(connection)
            try:
                gateway.append_result(_checker_result())
                assert HarnessMetricRepository(connection).get("result-1") is not None
            finally:
                gateway.close()
        with engine.connect() as connection:
            assert HarnessMetricRepository(connection).get("result-1") is not None
    finally:
        engine.dispose()


@pytest.mark.integration
def test_coordinator_gateway_duplicate_rejection_is_typed_and_non_leaking(
    tmp_path: Path,
) -> None:
    main_url = f"sqlite+pysqlite:///{(tmp_path / 'main.db').as_posix()}"
    upgrade_database(main_url)
    engine = create_database_engine(main_url)
    try:
        with engine.begin() as connection:
            _seed_campaign(connection)
        with DatabaseUnitOfWork(engine) as unit_of_work:
            connection = unit_of_work.connection
            assert connection is not None
            gateway = create_protected_result_gateway(connection)
            try:
                gateway.append_result(_checker_result())
                with pytest.raises(ProtectedCapabilityError) as duplicate:
                    gateway.append_result(_checker_result())
                assert duplicate.value.code == "RESULT_APPEND_REJECTED"
                assert "result-1" not in str(duplicate.value)
                gateway.append_result(_checker_result(result_id="result-2"))
            finally:
                gateway.close()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_result_validator_worker_rejects_invalid_and_cross_role_messages() -> None:
    validator = protected_evaluation_module.create_protected_result_validator()
    try:
        invalid = _send_raw_worker_operation(
            validator,
            operation="VALIDATE_RESULT",
            payload={"answer": "must-not-cross-worker-boundary"},
        )
        assert invalid["ok"] is False
        assert invalid["error_code"] == "INVALID_CHECKER_RESULT"
        assert "must-not-cross" not in repr(invalid)

        response = _send_raw_worker_operation(
            validator,
            operation="READ_EXPECTED_OUTPUT",
            payload="task-1",
        )
        assert response["ok"] is False
        assert response["error_code"] == "UNAUTHORIZED_OPERATION"
        assert validator.validate_result(_checker_result()) == _checker_result()
    finally:
        validator.close()
        validator.close()


@pytest.mark.integration
def test_worker_transport_rejects_pickle_without_executing_it(tmp_path: Path) -> None:
    marker = tmp_path / "pickle-executed"
    store = ProtectedEvaluationStore(tmp_path / "protected")
    try:
        store.add_expected_output("task-1", b"never-unpickle-role-messages")
        reader = store.answer_reader()
        transport = _worker_transport(reader)
        transport.send_bytes(pickle.dumps(_WorkerPickleProbe(str(marker))))
        response = _decode_raw_worker_response(transport.recv_bytes())

        assert marker.exists() is False
        assert response["ok"] is False
        assert response["error_code"] == "INVALID_REQUEST"
        assert reader.read_expected_output("task-1") == b"never-unpickle-role-messages"
    finally:
        store.close()


class _WorkerPickleProbe:
    def __init__(self, marker: str) -> None:
        self._marker = marker

    def __reduce__(self) -> tuple[object, tuple[str]]:
        return (_execute_pickle_probe, (self._marker,))


def _execute_pickle_probe(marker: str) -> dict[str, object]:
    Path(marker).touch()
    return {
        "request_id": "pickle-probe",
        "operation": "READ_EXPECTED_OUTPUT",
        "payload": "task-1",
    }


class _AnswerBearingProtectedCheckerResult(ProtectedCheckerResult):
    answer_bytes: bytes
    answer_path: str


class _MalformedProtectedCheckerResult:
    def __init__(self, secret: bytes, protected_path: str) -> None:
        self._secret = secret
        self._protected_path = protected_path

    def model_dump(self, *, mode: str) -> dict[str, object]:
        payload = _checker_result().model_dump(mode=mode)
        payload["answer_bytes"] = self._secret
        payload["answer_path"] = self._protected_path
        return payload


def _assert_fixed_non_leaking_result_error(
    error: ProtectedCapabilityError,
    *,
    sensitive_values: tuple[str, ...],
) -> None:
    formatted_chain = "".join(
        traceback.format_exception(
            type(error),
            error,
            error.__traceback__,
            chain=True,
        )
    )
    evidence = "\n".join(
        (
            str(error),
            repr(error),
            repr(error.args),
            repr(error.__cause__),
            formatted_chain,
        )
    )
    for sensitive_value in sensitive_values:
        assert sensitive_value not in evidence
    assert error.code == "INVALID_CHECKER_RESULT"
    assert error.args == ("protected checker result is invalid",)
    assert error.__cause__ is None
    assert error.__context__ is None


def _assert_fixed_non_leaking_worker_response_error(
    error: ProtectedCapabilityError,
    sensitive_value: str,
) -> None:
    formatted_chain = "".join(
        traceback.format_exception(
            type(error),
            error,
            error.__traceback__,
            chain=True,
        )
    )
    assert sensitive_value not in formatted_chain
    assert sensitive_value not in repr(error)
    assert sensitive_value not in repr(error.args)
    assert error.code == "INVALID_WORKER_RESPONSE"
    assert error.args == ("protected capability worker returned an invalid response",)
    assert error.__cause__ is None
    assert error.__context__ is None


class _TimeoutTransport:
    def __init__(self) -> None:
        self._responses = [
            json.dumps(
                {
                    "request_id": "worker-startup",
                    "ok": True,
                    "payload": None,
                    "error_code": None,
                    "error_message": None,
                }
            ).encode("utf-8")
        ]
        self.closed = False
        self.poll_timeouts: list[float] = []
        self.sent_frames: list[bytes] = []

    def send_bytes(self, value: bytes) -> None:
        assert value
        self.sent_frames.append(value)

    def recv_bytes(self, maxlength: int | None = None) -> bytes:
        assert maxlength is not None
        if not self._responses:
            raise EOFError
        return self._responses.pop(0)

    def poll(self, timeout: float = 0.0) -> bool:
        self.poll_timeouts.append(timeout)
        return bool(self._responses)

    def close(self) -> None:
        self.closed = True


class _MismatchedResponseTransport(_TimeoutTransport):
    def __init__(self) -> None:
        super().__init__()
        self._responses.append(
            json.dumps(
                {
                    "request_id": "different-request",
                    "ok": True,
                    "payload": {"base64": "c2VjcmV0"},
                    "error_code": None,
                    "error_message": None,
                }
            ).encode("utf-8")
        )


class _SequencedPayloadTransport(_TimeoutTransport):
    def __init__(self, payloads: tuple[object, ...]) -> None:
        super().__init__()
        self._responses.extend(
            json.dumps(
                {
                    "request_id": f"request-{request_number}",
                    "ok": True,
                    "payload": payload,
                    "error_code": None,
                    "error_message": None,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            for request_number, payload in enumerate(payloads, start=1)
        )


class _StubbornProcess:
    def __init__(self) -> None:
        self.closed = False
        self.terminated = False

    def is_alive(self) -> bool:
        return not self.terminated

    def join(self, timeout: float | None = None) -> None:
        assert timeout is not None

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True


def _send_raw_worker_operation(
    capability: object,
    *,
    operation: str,
    payload: object,
) -> dict[str, object]:
    transport = _worker_transport(capability)
    transport.send_bytes(
        json.dumps(
            {
                "request_id": "adversarial-request",
                "operation": operation,
                "payload": payload,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return _decode_raw_worker_response(transport.recv_bytes())


def _worker_transport(capability: object) -> object:
    transports = tuple(
        (path, value)
        for path, value in _walk_capability_graph(capability)
        if type(value).__module__ == "multiprocessing.connection"
        and all(callable(getattr(value, name, None)) for name in ("send", "recv", "close"))
    )
    assert len(transports) == 1, f"expected one role transport, found {transports!r}"
    return transports[0][1]


def _decode_raw_worker_response(raw_response: bytes) -> dict[str, object]:
    decoded = json.loads(raw_response)
    assert isinstance(decoded, dict)
    return decoded


def _windows_process_handle_count() -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    get_process_handle_count = kernel32.GetProcessHandleCount
    get_process_handle_count.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_process_handle_count.restype = wintypes.BOOL
    count = wintypes.DWORD()
    if not get_process_handle_count(get_current_process(), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    return count.value


def _capability_graph_violations(
    capability: object,
    allowed_operations: set[str],
    forbidden_references: set[str] | None = None,
) -> tuple[str, ...]:
    forbidden_references = forbidden_references or set()
    violations: list[str] = []
    for path, value in _walk_capability_graph(capability):
        if isinstance(value, str):
            for reference in forbidden_references:
                if reference and reference in value:
                    violations.append(f"{path} exposes a configured storage reference")
        if isinstance(value, _FORBIDDEN_CAPABILITY_TYPES):
            violations.append(f"{path} exposes {type(value).__module__}.{type(value).__qualname__}")
            continue
        exposed_operations = {
            operation for operation in _ROLE_OPERATIONS if callable(getattr(value, operation, None))
        }
        unexpected_operations = exposed_operations - allowed_operations
        for operation in sorted(unexpected_operations):
            violations.append(f"{path} exposes unintended operation {operation}")
        if all(callable(getattr(value, name, None)) for name in ("add", "get", "list_all")):
            violations.append(f"{path} exposes an unrestricted repository")
    return tuple(dict.fromkeys(violations))


def _walk_capability_graph(root: object) -> Iterator[tuple[str, object]]:
    pending: list[tuple[str, object]] = [("root", root)]
    visited: set[int] = set()
    while pending:
        path, value = pending.pop()
        identity = id(value)
        if identity in visited:
            continue
        visited.add(identity)
        yield path, value
        if isinstance(value, _GRAPH_TERMINAL_TYPES):
            continue
        if isinstance(value, Mapping):
            pending.extend((f"{path}[{key!r}]", nested) for key, nested in value.items())
        elif isinstance(value, (tuple, list, set, frozenset)):
            pending.extend((f"{path}[{index}]", nested) for index, nested in enumerate(value))
        if isinstance(value, partial):
            pending.append((f"{path}.func", value.func))
            pending.extend(
                (f"{path}.args[{index}]", nested) for index, nested in enumerate(value.args)
            )
            if value.keywords:
                pending.append((f"{path}.keywords", value.keywords))
        bound_self = getattr(value, "__self__", None)
        if bound_self is not None:
            pending.append((f"{path}.__self__", bound_self))
        if inspect.isfunction(value):
            closure = value.__closure__ or ()
            for index, cell in enumerate(closure):
                try:
                    nested = cell.cell_contents
                except ValueError:
                    continue
                pending.append((f"{path}.__closure__[{index}]", nested))
            defaults = value.__defaults__ or ()
            pending.extend(
                (f"{path}.__defaults__[{index}]", nested) for index, nested in enumerate(defaults)
            )
            if value.__kwdefaults__:
                pending.append((f"{path}.__kwdefaults__", value.__kwdefaults__))
        try:
            attributes = vars(value)
        except TypeError:
            attributes = {}
        pending.extend(
            (f"{path}.{name}", nested)
            for name, nested in attributes.items()
            if name not in {"__dict__", "__weakref__"}
        )
        for owner in type(value).__mro__:
            slots = owner.__dict__.get("__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            for slot in slots:
                if slot in {"__dict__", "__weakref__"}:
                    continue
                try:
                    nested = getattr(value, slot)
                except (AttributeError, TypeError):
                    continue
                pending.append((f"{path}.{slot}", nested))


def _seed_campaign(connection: object) -> None:
    from super_scientist.providers.storage.domain_records import (
        HarnessCampaignRecord,
        HarnessCampaignRepository,
        HarnessVariant,
    )

    assert isinstance(connection, Connection)
    campaign = HarnessCampaignRecord(
        campaign_id="campaign-1",
        version=1,
        variants=(HarnessVariant.EVOLVED_HARNESS,),
        model_id="model-1",
        model_version="model-v1",
        adapter_id=None,
        created_by="human-1",
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
        governing_policy_hash="c" * 64,
    )
    HarnessCampaignRepository(connection).add(
        campaign.campaign_id,
        campaign,
        campaign.created_at,
    )


def _checker_result(*, result_id: str = "result-1") -> ProtectedCheckerResult:
    return ProtectedCheckerResult(
        result_id=result_id,
        campaign_id="campaign-1",
        task_id="task-1",
        expected_output_hash="a" * 64,
        candidate_output_hash="b" * 64,
        checker_id="checker-1",
        checker_version="checker-v1",
        outcome=AssessmentOutcome.PASSED,
        metric_values=(MetricValue(metric_id="correctness", value=Decimal("1.0")),),
        evaluated_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
