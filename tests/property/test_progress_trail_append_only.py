from __future__ import annotations

import inspect as python_inspect
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, Engine, insert, select, text
from sqlalchemy.exc import IntegrityError

from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.providers.storage import domain_records, schema
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.domain_records import (
    EvidenceTrailHeadRepository,
    ProgressHeadRepository,
    _AppendOnlyRecordRepository,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError

AUTHORITATIVE_0003_TABLES = {
    "progress_plans",
    "progress_subtasks",
    "progress_events",
    "run_budgets",
    "run_checkpoints",
    "completion_decisions",
    "evidence_trail_versions",
    "evidence_trail_nodes",
    "evidence_trail_relations",
    "evidence_trail_checks",
    "evidence_trail_assessments",
    "report_sentence_bindings",
}

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


class _ProgressPlanStorageProbe(BaseModel):
    """Test-only probe for the private record engine; not a progress domain contract."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    plan_version_id: str
    run_id: str


class _EvidenceTrailVersionStorageProbe(BaseModel):
    """Test-only probe for typed private storage bindings, not a trail contract."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    trail_version_id: str
    trail_id: str
    claim_version_id: str
    version: object


@pytest.mark.property
@pytest.mark.parametrize("table_name", sorted(AUTHORITATIVE_0003_TABLES))
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_every_authoritative_0003_table_has_both_append_only_triggers(
    tmp_path: Path,
    table_name: str,
    operation: str,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'append-only.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    suffix = "no_update" if operation == "UPDATE" else "no_delete"
    try:
        with engine.connect() as connection:
            trigger = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = :name"),
                {"name": f"{table_name}_{suffix}"},
            ).scalar_one_or_none()
    finally:
        engine.dispose()

    assert trigger == f"{table_name}_{suffix}"


def test_runtime_schema_declares_only_fixed_0003_storage_tables() -> None:
    assert AUTHORITATIVE_0003_TABLES | {"progress_heads", "evidence_trail_heads"} <= set(
        schema.metadata.tables
    )


def test_public_surface_exposes_fixed_progress_record_and_head_repositories() -> None:
    expected = {
        "ProgressPlanRepository",
        "ProgressSubtaskRepository",
        "ProgressEventRepository",
        "RunBudgetRepository",
        "RunCheckpointRepository",
        "CompletionDecisionRepository",
        "ProgressHeadRepository",
        "EvidenceTrailHeadRepository",
    }
    assert expected <= set(domain_records.__all__)
    for repository_name in expected:
        repository_type = getattr(domain_records, repository_name)
        assert tuple(python_inspect.signature(repository_type).parameters) == ("connection",)

    trail_record_repositories = {
        "EvidenceTrailVersionRepository",
        "EvidenceTrailNodeRepository",
        "EvidenceTrailRelationRepository",
        "EvidenceTrailCheckRepository",
        "EvidenceTrailAssessmentRepository",
        "ReportSentenceBindingRepository",
    }
    assert trail_record_repositories <= set(domain_records.__all__)
    for repository_name in trail_record_repositories:
        repository_type = getattr(domain_records, repository_name)
        assert tuple(python_inspect.signature(repository_type).parameters) == ("connection",)


@pytest.mark.property
@pytest.mark.parametrize("table_name", sorted(AUTHORITATIVE_0003_TABLES))
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_every_authoritative_0003_row_rejects_update_and_delete(
    tmp_path: Path,
    table_name: str,
    operation: str,
) -> None:
    engine = _engine(tmp_path, f"{table_name}-{operation}.db")
    try:
        with engine.begin() as connection:
            _seed_authoritative_rows(connection)
            original_count = connection.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar_one()
            statement = (
                f"UPDATE {table_name} SET record_json = record_json"
                if operation == "UPDATE"
                else f"DELETE FROM {table_name}"
            )
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(statement))
            assert (
                connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
                == original_count
            )
    finally:
        engine.dispose()


def test_private_record_engine_round_trips_canonical_0003_storage(tmp_path: Path) -> None:
    repository, connection, engine = _progress_plan_repository(tmp_path)
    try:
        record = _ProgressPlanStorageProbe(plan_version_id="plan-1", run_id="run-1")
        repository.add(record.plan_version_id, record, NOW)

        stored_json, stored_hash, created_at = connection.execute(
            text(
                "SELECT record_json, content_hash, created_at FROM progress_plans "
                "WHERE plan_version_id = 'plan-1'"
            )
        ).one()
        assert repository.get(record.plan_version_id) == record
        assert stored_hash == sha256_hex(stored_json.encode("utf-8"))
        assert created_at == NOW.isoformat()
    finally:
        connection.close()
        engine.dispose()


def test_private_record_engine_rejects_identifier_mismatch_before_insert(tmp_path: Path) -> None:
    repository, connection, engine = _progress_plan_repository(tmp_path)
    try:
        record = _ProgressPlanStorageProbe(plan_version_id="plan-1", run_id="run-1")
        with pytest.raises(StorageIntegrityError, match="plan_version_id"):
            repository.add("different-plan", record, NOW)
    finally:
        connection.close()
        engine.dispose()


def test_private_record_engine_rejects_noncanonical_json_with_matching_hash(
    tmp_path: Path,
) -> None:
    repository, connection, engine = _progress_plan_repository(tmp_path)
    record_json = '{"run_id": "run-1", "plan_version_id": "plan-1"}'
    try:
        with engine.begin() as writer:
            writer.execute(
                insert(schema.progress_plans).values(
                    plan_version_id="plan-1",
                    run_id="run-1",
                    record_json=record_json,
                    content_hash=sha256_hex(record_json.encode("utf-8")),
                    created_at=NOW.isoformat(),
                )
            )

        with pytest.raises(StorageIntegrityError, match="record_json must be canonical"):
            repository.get("plan-1")
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("record_json", "content_hash", "created_at", "expected_detail"),
    [
        ("{not json", "a" * 64, NOW.isoformat(), "invalid record JSON"),
        (
            '{"plan_version_id":"other-plan","run_id":"run-1"}',
            None,
            NOW.isoformat(),
            "plan_version_id",
        ),
        (
            '{"plan_version_id":"plan-1","run_id":"run-2"}',
            None,
            NOW.isoformat(),
            "run_id",
        ),
        (
            '{"plan_version_id":"plan-1","run_id":"run-1"}',
            "a" * 64,
            NOW.isoformat(),
            "content_hash",
        ),
        (
            '{"plan_version_id":"plan-1","run_id":"run-1"}',
            None,
            "2026-07-18T12:00:00+01:00",
            "created_at",
        ),
    ],
    ids=[
        "corrupt-json",
        "identifier-mismatch",
        "relationship-mismatch",
        "hash-mismatch",
        "non-utc-created-at",
    ],
)
def test_private_record_engine_rejects_corrupt_0003_storage(
    tmp_path: Path,
    record_json: str,
    content_hash: str | None,
    created_at: str,
    expected_detail: str,
) -> None:
    repository, connection, engine = _progress_plan_repository(tmp_path)
    try:
        with engine.begin() as writer:
            _insert_research_run(writer, "run-2")
            writer.execute(
                insert(schema.progress_plans).values(
                    plan_version_id="plan-1",
                    run_id="run-1",
                    record_json=record_json,
                    content_hash=(
                        sha256_hex(record_json.encode("utf-8"))
                        if content_hash is None
                        else content_hash
                    ),
                    created_at=created_at,
                )
            )

        with pytest.raises(StorageIntegrityError, match=expected_detail):
            repository.get("plan-1")
    finally:
        connection.close()
        engine.dispose()


def test_private_integer_relationship_binding_round_trips_exact_integer(
    tmp_path: Path,
) -> None:
    repository, connection, engine = _evidence_trail_version_repository(tmp_path)
    record = _EvidenceTrailVersionStorageProbe(
        trail_version_id="trail-version-1",
        trail_id="trail-1",
        claim_version_id="claim-1:1",
        version=1,
    )
    try:
        repository.add(record.trail_version_id, record, NOW)

        stored_version = connection.execute(
            select(schema.evidence_trail_versions.c.version).where(
                schema.evidence_trail_versions.c.trail_version_id == record.trail_version_id
            )
        ).scalar_one()
        assert type(stored_version) is int
        assert stored_version == 1
        assert repository.get(record.trail_version_id) == record
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize("invalid_version", [True, "1"], ids=["bool", "string"])
def test_private_integer_relationship_binding_rejects_bool_and_string_writes(
    tmp_path: Path,
    invalid_version: object,
) -> None:
    repository, connection, engine = _evidence_trail_version_repository(tmp_path)
    record = _EvidenceTrailVersionStorageProbe(
        trail_version_id="trail-version-1",
        trail_id="trail-1",
        claim_version_id="claim-1:1",
        version=invalid_version,
    )
    try:
        with pytest.raises(StorageIntegrityError, match="version must be an integer"):
            repository.add(record.trail_version_id, record, NOW)
    finally:
        connection.close()
        engine.dispose()


def test_private_integer_relationship_binding_rejects_boolean_stored_value(
    tmp_path: Path,
) -> None:
    repository, connection, engine = _evidence_trail_version_repository(tmp_path)
    record_json = canonical_json_bytes(
        {
            "trail_version_id": "trail-version-1",
            "trail_id": "trail-1",
            "claim_version_id": "claim-1:1",
            "version": True,
        }
    ).decode("utf-8")
    try:
        # SQLite normalizes bound booleans to integer storage, so exercise the raw
        # decoder mapping directly to preserve the bool-versus-int corruption case.
        with pytest.raises(StorageIntegrityError, match="version must be an integer"):
            repository._decode_row(
                {
                    "trail_version_id": "trail-version-1",
                    "trail_id": "trail-1",
                    "claim_version_id": "claim-1:1",
                    "version": True,
                    "record_json": record_json,
                    "content_hash": sha256_hex(record_json.encode("utf-8")),
                    "created_at": NOW.isoformat(),
                }
            )
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("stored_version", "record_version", "expected_detail"),
    [
        ("not-an-integer", "not-an-integer", "version must be an integer"),
        (2, 1, "version does not match record_json"),
    ],
    ids=["string", "mismatch"],
)
def test_private_integer_relationship_binding_rejects_corrupt_stored_versions(
    tmp_path: Path,
    stored_version: object,
    record_version: object,
    expected_detail: str,
) -> None:
    repository, connection, engine = _evidence_trail_version_repository(tmp_path)
    record_json = canonical_json_bytes(
        {
            "trail_version_id": "trail-version-1",
            "trail_id": "trail-1",
            "claim_version_id": "claim-1:1",
            "version": record_version,
        }
    ).decode("utf-8")
    try:
        with engine.begin() as writer:
            writer.execute(
                insert(schema.evidence_trail_versions).values(
                    trail_version_id="trail-version-1",
                    trail_id="trail-1",
                    claim_version_id="claim-1:1",
                    version=stored_version,
                    record_json=record_json,
                    content_hash=sha256_hex(record_json.encode("utf-8")),
                    created_at=NOW.isoformat(),
                )
            )

        with pytest.raises(StorageIntegrityError, match=expected_detail):
            repository.get("trail-version-1")
    finally:
        connection.close()
        engine.dispose()


def test_progress_head_repository_round_trips_and_updates_projection(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "progress-head.db")
    connection = engine.connect()
    repository = ProgressHeadRepository(connection)
    try:
        with engine.begin() as writer:
            _seed_progress(writer)

        assert repository.get("run-1") is None
        repository.set("run-1", "plan-1", "event-1")
        assert repository.get("run-1") == ("plan-1", "event-1")
        repository.set("run-1", "plan-1", "event-2")
        assert repository.get("run-1") == ("plan-1", "event-2")
        assert repository.list_all() == (("run-1", "plan-1", "event-2"),)
    finally:
        connection.close()
        engine.dispose()


def test_progress_head_repository_rejects_cross_run_and_cross_plan_events(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "progress-head-coherence.db")
    connection = engine.connect()
    repository = ProgressHeadRepository(connection)
    try:
        with engine.begin() as writer:
            _seed_progress(writer)
            _seed_second_progress(writer)

        with pytest.raises(StorageIntegrityError, match="plan_version_id"):
            repository.set("run-1", "plan-2", "event-3")
        with pytest.raises(StorageIntegrityError, match="last_event_id"):
            repository.set("run-1", "plan-1", "event-3")
    finally:
        connection.close()
        engine.dispose()


def test_evidence_trail_head_repository_round_trips_and_updates_projection(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "trail-head.db")
    connection = engine.connect()
    repository = EvidenceTrailHeadRepository(connection)
    try:
        with engine.begin() as writer:
            _seed_trail_versions(writer)

        assert repository.get("trail-1") is None
        repository.set("trail-1", "trail-version-1", 1)
        assert repository.get("trail-1") == ("trail-version-1", 1)
        repository.set("trail-1", "trail-version-2", 2)
        assert repository.get("trail-1") == ("trail-version-2", 2)
        assert repository.list_all() == (("trail-1", "trail-version-2", 2),)
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize("invalid_version", [True, "1"], ids=["bool", "string"])
def test_evidence_trail_head_repository_rejects_non_integer_version_without_mutating_head(
    tmp_path: Path,
    invalid_version: object,
) -> None:
    engine = _engine(tmp_path, "trail-head-invalid-version.db")
    connection = engine.connect()
    repository = EvidenceTrailHeadRepository(connection)
    try:
        with engine.begin() as writer:
            _seed_trail_versions(writer)

        repository.set("trail-1", "trail-version-1", 1)
        repository.set("trail-1", "trail-version-2", 2)
        with pytest.raises(StorageIntegrityError, match="version must be an integer"):
            repository.set("trail-1", "trail-version-1", invalid_version)
        assert repository.get("trail-1") == ("trail-version-2", 2)
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("trail_id", "trail_version_id", "version"),
    [
        ("other-trail", "trail-version-1", 1),
        ("trail-1", "trail-version-1", 2),
        ("trail-1", "missing-version", 1),
    ],
)
def test_evidence_trail_head_repository_rejects_incoherent_identity(
    tmp_path: Path,
    trail_id: str,
    trail_version_id: str,
    version: int,
) -> None:
    engine = _engine(tmp_path, "trail-head-coherence.db")
    connection = engine.connect()
    repository = EvidenceTrailHeadRepository(connection)
    try:
        with engine.begin() as writer:
            _seed_trail_versions(writer)

        with pytest.raises(StorageIntegrityError, match="does not match"):
            repository.set(trail_id, trail_version_id, version)
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize("projection", ["progress", "trail"])
def test_head_repositories_reject_dangling_rows_in_corrupt_storage(
    tmp_path: Path,
    projection: str,
) -> None:
    database_path = tmp_path / f"dangling-{projection}.db"
    engine = _engine_for_path(database_path)
    try:
        with engine.begin() as connection:
            if projection == "progress":
                _seed_progress(connection)
            else:
                _seed_trail_versions(connection)
    finally:
        engine.dispose()

    with sqlite3.connect(database_path) as raw_connection:
        if projection == "progress":
            raw_connection.execute(
                "INSERT INTO progress_heads (run_id, plan_version_id, last_event_id) "
                "VALUES ('run-1', 'plan-1', 'missing-event')"
            )
        else:
            raw_connection.execute(
                "INSERT INTO evidence_trail_heads (trail_id, trail_version_id, version) "
                "VALUES ('trail-1', 'missing-version', 1)"
            )

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    connection = engine.connect()
    try:
        if projection == "progress":
            repository = ProgressHeadRepository(connection)
            with pytest.raises(StorageIntegrityError, match="incoherent event"):
                repository.get("run-1")
        else:
            repository = EvidenceTrailHeadRepository(connection)
            with pytest.raises(StorageIntegrityError, match="incoherent version"):
                repository.get("trail-1")
    finally:
        connection.close()
        engine.dispose()


def _engine(tmp_path: Path, name: str) -> Engine:
    return _engine_for_path(tmp_path / name)


def _engine_for_path(database_path: Path) -> Engine:
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    return create_database_engine(database_url)


def _progress_plan_repository(
    tmp_path: Path,
) -> tuple[_AppendOnlyRecordRepository[_ProgressPlanStorageProbe], Connection, Engine]:
    engine = _engine(tmp_path, "raw-progress-plan.db")
    with engine.begin() as writer:
        _insert_research_run(writer, "run-1")
    connection = engine.connect()
    return (
        _AppendOnlyRecordRepository(
            connection,
            table=schema.progress_plans,
            model_type=_ProgressPlanStorageProbe,
            identifier_field="plan_version_id",
            relationship_fields={"run_id": "run_id"},
        ),
        connection,
        engine,
    )


def _evidence_trail_version_repository(
    tmp_path: Path,
) -> tuple[_AppendOnlyRecordRepository[_EvidenceTrailVersionStorageProbe], Connection, Engine]:
    engine = _engine(tmp_path, "raw-evidence-trail-version.db")
    with engine.begin() as writer:
        _insert_claim_version(
            writer,
            claim_version_id="claim-1:1",
            claim_id="claim-1",
            version=1,
        )
    connection = engine.connect()
    return (
        _AppendOnlyRecordRepository(
            connection,
            table=schema.evidence_trail_versions,
            model_type=_EvidenceTrailVersionStorageProbe,
            identifier_field="trail_version_id",
            relationship_fields={
                "trail_id": "trail_id",
                "claim_version_id": "claim_version_id",
                "version": "version",
            },
            relationship_types={"version": int},
        ),
        connection,
        engine,
    )


def _record_values(**relationships: object) -> dict[str, object]:
    return {
        **relationships,
        "record_json": "{}",
        "content_hash": "a" * 64,
        "created_at": NOW.isoformat(),
    }


def _insert_research_run(connection: Connection, run_id: str) -> None:
    connection.execute(insert(schema.research_runs).values(**_record_values(run_id=run_id)))


def _insert_claim_version(
    connection: Connection,
    *,
    claim_version_id: str,
    claim_id: str,
    version: int,
) -> None:
    connection.execute(
        insert(schema.claim_versions).values(
            claim_version_id=claim_version_id,
            claim_id=claim_id,
            version=version,
            status="PROPOSED",
            record_json="{}",
            content_hash="a" * 64,
            created_at=NOW.isoformat(),
        )
    )


def _seed_progress(connection: Connection) -> None:
    _insert_research_run(connection, "run-1")
    connection.execute(
        insert(schema.progress_plans).values(
            **_record_values(plan_version_id="plan-1", run_id="run-1")
        )
    )
    connection.execute(
        insert(schema.progress_subtasks).values(
            **_record_values(subtask_id="subtask-1", plan_version_id="plan-1")
        )
    )
    for event_id in ("event-1", "event-2"):
        connection.execute(
            insert(schema.progress_events).values(
                **_record_values(
                    event_id=event_id,
                    run_id="run-1",
                    plan_version_id="plan-1",
                    subtask_id="subtask-1",
                )
            )
        )


def _seed_second_progress(connection: Connection) -> None:
    _insert_research_run(connection, "run-2")
    connection.execute(
        insert(schema.progress_plans).values(
            **_record_values(plan_version_id="plan-2", run_id="run-2")
        )
    )
    connection.execute(
        insert(schema.progress_subtasks).values(
            **_record_values(subtask_id="subtask-2", plan_version_id="plan-2")
        )
    )
    connection.execute(
        insert(schema.progress_events).values(
            **_record_values(
                event_id="event-3",
                run_id="run-2",
                plan_version_id="plan-2",
                subtask_id="subtask-2",
            )
        )
    )


def _seed_trail_versions(connection: Connection) -> None:
    _insert_claim_version(
        connection,
        claim_version_id="claim-1:1",
        claim_id="claim-1",
        version=1,
    )
    _insert_claim_version(
        connection,
        claim_version_id="claim-1:2",
        claim_id="claim-1",
        version=2,
    )
    for version in (1, 2):
        connection.execute(
            insert(schema.evidence_trail_versions).values(
                **_record_values(
                    trail_version_id=f"trail-version-{version}",
                    trail_id="trail-1",
                    claim_version_id=f"claim-1:{version}",
                    version=version,
                )
            )
        )


def _seed_authoritative_rows(connection: Connection) -> None:
    _seed_progress(connection)
    connection.execute(
        insert(schema.run_budgets).values(
            **_record_values(budget_id="budget-1", run_id="run-1", plan_version_id="plan-1")
        )
    )
    connection.execute(
        insert(schema.run_checkpoints).values(
            **_record_values(
                checkpoint_id="checkpoint-1",
                run_id="run-1",
                plan_version_id="plan-1",
            )
        )
    )
    connection.execute(
        insert(schema.completion_decisions).values(
            **_record_values(
                completion_decision_id="completion-1",
                run_id="run-1",
                plan_version_id="plan-1",
            )
        )
    )
    _insert_claim_version(
        connection,
        claim_version_id="claim-1:1",
        claim_id="claim-1",
        version=1,
    )
    connection.execute(
        insert(schema.evidence_records).values(**_record_values(evidence_id="evidence-1"))
    )
    connection.execute(
        insert(schema.evidence_trail_versions).values(
            **_record_values(
                trail_version_id="trail-version-1",
                trail_id="trail-1",
                claim_version_id="claim-1:1",
                version=1,
            )
        )
    )
    connection.execute(
        insert(schema.evidence_trail_nodes).values(
            **_record_values(
                node_id="node-1",
                trail_version_id="trail-version-1",
                evidence_id="evidence-1",
            )
        )
    )
    connection.execute(
        insert(schema.evidence_trail_relations).values(
            **_record_values(
                relation_id="relation-1",
                trail_version_id="trail-version-1",
                source_node_id="node-1",
                target_node_id="node-1",
            )
        )
    )
    connection.execute(
        insert(schema.evidence_trail_checks).values(
            **_record_values(check_id="check-1", trail_version_id="trail-version-1")
        )
    )
    connection.execute(
        insert(schema.evidence_trail_assessments).values(
            **_record_values(assessment_id="assessment-1", trail_version_id="trail-version-1")
        )
    )
    connection.execute(
        insert(schema.report_sentence_bindings).values(
            **_record_values(
                binding_id="binding-1",
                trail_version_id="trail-version-1",
                claim_version_id="claim-1:1",
            )
        )
    )
