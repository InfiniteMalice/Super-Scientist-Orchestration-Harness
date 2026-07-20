from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.domain_records import HarnessMetricRepository
from super_scientist.providers.storage.protected_evaluation import (
    MetricValue,
    ProtectedAnswerReader,
    ProtectedCheckerResult,
    ProtectedEvaluationStore,
    ProtectedIntegrityAuditor,
    ProtectedResultGateway,
    create_protected_result_gateway,
)
from super_scientist.providers.storage.repositories import RepositorySet


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
            gateway = create_protected_result_gateway(connection)
            assert isinstance(gateway, ProtectedResultGateway)
            result = ProtectedCheckerResult(
                result_id="result-1",
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
            gateway.append_result(result)
            stored = HarnessMetricRepository(connection).get("result-1")
            assert stored is not None
            assert stored.expected_output_hash == "a" * 64
            assert "expected_output" not in type(stored).model_fields
            assert '"expected_output":' not in stored.model_dump_json().lower()
            assert "answer" not in stored.model_dump_json().lower()
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
        with pytest.raises(ValueError, match="unavailable"):
            store.answer_reader().read_expected_output("missing-task")

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


def _seed_campaign(connection: object) -> None:
    from sqlalchemy import Connection

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
