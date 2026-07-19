from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from super_scientist.providers.storage.database import create_database_engine

REVISION = "0003_progress_and_evidence_trails"

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

PROJECTION_0003_TABLES = {"progress_heads", "evidence_trail_heads"}

EXPECTED_PRIMARY_KEYS = {
    "progress_plans": ("plan_version_id",),
    "progress_subtasks": ("subtask_id",),
    "progress_events": ("event_id",),
    "run_budgets": ("budget_id",),
    "run_checkpoints": ("checkpoint_id",),
    "completion_decisions": ("completion_decision_id",),
    "evidence_trail_versions": ("trail_version_id",),
    "evidence_trail_nodes": ("node_id",),
    "evidence_trail_relations": ("relation_id",),
    "evidence_trail_checks": ("check_id",),
    "evidence_trail_assessments": ("assessment_id",),
    "report_sentence_bindings": ("binding_id",),
    "progress_heads": ("run_id",),
    "evidence_trail_heads": ("trail_id",),
}

EXPECTED_FOREIGN_KEYS = {
    "progress_plans": {
        (("run_id",), "research_runs", ("run_id",)),
    },
    "progress_subtasks": {
        (("plan_version_id",), "progress_plans", ("plan_version_id",)),
    },
    "progress_events": {
        (
            ("run_id", "plan_version_id"),
            "progress_plans",
            ("run_id", "plan_version_id"),
        ),
        (
            ("plan_version_id", "subtask_id"),
            "progress_subtasks",
            ("plan_version_id", "subtask_id"),
        ),
    },
    "run_budgets": {
        (
            ("run_id", "plan_version_id"),
            "progress_plans",
            ("run_id", "plan_version_id"),
        ),
    },
    "run_checkpoints": {
        (
            ("run_id", "plan_version_id"),
            "progress_plans",
            ("run_id", "plan_version_id"),
        ),
    },
    "completion_decisions": {
        (
            ("run_id", "plan_version_id"),
            "progress_plans",
            ("run_id", "plan_version_id"),
        ),
    },
    "progress_heads": {
        (
            ("run_id", "plan_version_id"),
            "progress_plans",
            ("run_id", "plan_version_id"),
        ),
        (
            ("plan_version_id", "last_event_id"),
            "progress_events",
            ("plan_version_id", "event_id"),
        ),
    },
    "evidence_trail_versions": {
        (("claim_version_id",), "claim_versions", ("claim_version_id",)),
    },
    "evidence_trail_nodes": {
        (("trail_version_id",), "evidence_trail_versions", ("trail_version_id",)),
        (("evidence_id",), "evidence_records", ("evidence_id",)),
    },
    "evidence_trail_relations": {
        (("trail_version_id",), "evidence_trail_versions", ("trail_version_id",)),
        (
            ("trail_version_id", "source_node_id"),
            "evidence_trail_nodes",
            ("trail_version_id", "node_id"),
        ),
        (
            ("trail_version_id", "target_node_id"),
            "evidence_trail_nodes",
            ("trail_version_id", "node_id"),
        ),
    },
    "evidence_trail_checks": {
        (("trail_version_id",), "evidence_trail_versions", ("trail_version_id",)),
    },
    "evidence_trail_assessments": {
        (("trail_version_id",), "evidence_trail_versions", ("trail_version_id",)),
    },
    "report_sentence_bindings": {
        (
            ("trail_version_id", "claim_version_id"),
            "evidence_trail_versions",
            ("trail_version_id", "claim_version_id"),
        ),
    },
    "evidence_trail_heads": {
        (
            ("trail_id", "trail_version_id", "version"),
            "evidence_trail_versions",
            ("trail_id", "trail_version_id", "version"),
        ),
    },
}


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'progress-trails.db').as_posix()}"


@pytest.mark.integration
def test_clean_upgrade_creates_progress_and_evidence_trail_storage(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)

    assert _table_names(database_url) >= AUTHORITATIVE_0003_TABLES | PROJECTION_0003_TABLES
    assert _revision(database_url) == REVISION


@pytest.mark.integration
def test_genuine_0001_database_upgrades_to_0003_without_changing_legacy_rows(
    database_url: str,
) -> None:
    _upgrade_to(database_url, "0001_epistemic_kernel")
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO evidence_records "
                    "(evidence_id, content_hash, record_json, created_at) "
                    "VALUES ('legacy-evidence', :digest, '{}', :created_at)"
                ),
                {"digest": "a" * 64, "created_at": "2026-07-11T00:00:00+00:00"},
            )
    finally:
        engine.dispose()

    _upgrade_to(database_url, REVISION)

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT evidence_id FROM evidence_records")
            ).scalar_one() == "legacy-evidence"
    finally:
        engine.dispose()
    assert _table_names(database_url) >= AUTHORITATIVE_0003_TABLES | PROJECTION_0003_TABLES
    assert _revision(database_url) == REVISION


@pytest.mark.integration
def test_0002_database_upgrades_to_0003_without_changing_foundation_rows(
    database_url: str,
) -> None:
    _upgrade_to(database_url, "0002_governed_adaptation_foundation")
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_research_run(connection, "existing-run")
    finally:
        engine.dispose()

    _upgrade_to(database_url, REVISION)

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT run_id FROM research_runs")
            ).scalar_one() == "existing-run"
    finally:
        engine.dispose()
    assert _revision(database_url) == REVISION


@pytest.mark.integration
def test_0003_downgrade_removes_only_0003_objects_and_restores_0002(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    _downgrade_to(database_url, "0002_governed_adaptation_foundation")

    names = _table_names(database_url)
    assert not ((AUTHORITATIVE_0003_TABLES | PROJECTION_0003_TABLES) & names)
    assert names >= {"research_runs", "evaluator_versions", "claim_versions"}
    assert _revision(database_url) == "0002_governed_adaptation_foundation"


@pytest.mark.integration
def test_0003_declares_stable_primary_keys_and_canonical_record_columns(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            for table_name, expected_primary_key in EXPECTED_PRIMARY_KEYS.items():
                assert tuple(inspector.get_pk_constraint(table_name)["constrained_columns"]) == (
                    expected_primary_key
                )
            for table_name in AUTHORITATIVE_0003_TABLES:
                columns = {column["name"]: column for column in inspector.get_columns(table_name)}
                assert {"record_json", "content_hash", "created_at"} <= columns.keys()
                assert all(columns[name]["nullable"] is False for name in columns)
                check_sql = " ".join(
                    str(check["sqltext"]) for check in inspector.get_check_constraints(table_name)
                )
                assert "length(content_hash) = 64" in check_sql
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0003_declares_all_relationship_foreign_keys(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            for table_name, expected in EXPECTED_FOREIGN_KEYS.items():
                actual = {
                    (
                        tuple(foreign_key["constrained_columns"]),
                        str(foreign_key["referred_table"]),
                        tuple(foreign_key["referred_columns"]),
                    )
                    for foreign_key in inspector.get_foreign_keys(table_name)
                }
                assert actual == expected
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0003_materializes_required_composite_uniqueness(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            expected = {
                "progress_subtasks": ("plan_version_id", "subtask_id"),
                "evidence_trail_nodes": ("trail_version_id", "node_id"),
                "evidence_trail_relations": ("trail_version_id", "relation_id"),
            }
            for table_name, columns in expected.items():
                unique_sets = {
                    tuple(constraint["column_names"])
                    for constraint in inspector.get_unique_constraints(table_name)
                }
                assert columns in unique_sets
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0003_rejects_orphan_progress_and_trail_records(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                _insert_record(
                    connection,
                    "progress_events",
                    "event_id, run_id, plan_version_id, subtask_id",
                    "'event-orphan', 'missing-run', 'missing-plan', 'missing-subtask'",
                )
            with pytest.raises(IntegrityError):
                _insert_record(
                    connection,
                    "evidence_trail_nodes",
                    "node_id, trail_version_id, evidence_id",
                    "'node-orphan', 'missing-trail-version', 'missing-evidence'",
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0003_rejects_cross_plan_progress_relationships(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _seed_progress(connection)
            _insert_research_run(connection, "run-2")
            _insert_record(
                connection,
                "progress_plans",
                "plan_version_id, run_id",
                "'plan-2', 'run-2'",
            )
            _insert_record(
                connection,
                "progress_subtasks",
                "subtask_id, plan_version_id",
                "'subtask-2', 'plan-2'",
            )
            _insert_record(
                connection,
                "progress_events",
                "event_id, run_id, plan_version_id, subtask_id",
                "'event-2', 'run-2', 'plan-2', 'subtask-2'",
            )
            with pytest.raises(IntegrityError):
                _insert_record(
                    connection,
                    "progress_events",
                    "event_id, run_id, plan_version_id, subtask_id",
                    "'event-cross-plan', 'run-1', 'plan-1', 'subtask-2'",
                )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO progress_heads (run_id, plan_version_id, last_event_id) "
                        "VALUES ('run-1', 'plan-1', 'event-2')"
                    )
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0003_rejects_cross_trail_graph_and_projection_relationships(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _seed_trails(connection)
            with pytest.raises(IntegrityError):
                _insert_record(
                    connection,
                    "evidence_trail_relations",
                    "relation_id, trail_version_id, source_node_id, target_node_id",
                    "'relation-cross-trail', 'trail-version-1', 'node-1', 'node-2'",
                )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO evidence_trail_heads "
                        "(trail_id, trail_version_id, version) "
                        "VALUES ('trail-1', 'trail-version-2', 1)"
                    )
                )
            with pytest.raises(IntegrityError):
                _insert_record(
                    connection,
                    "report_sentence_bindings",
                    "binding_id, trail_version_id, claim_version_id",
                    "'binding-cross-claim', 'trail-version-1', 'claim-2:1'",
                )
    finally:
        engine.dispose()


def _table_names(database_url: str) -> set[str]:
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def _revision(database_url: str) -> str:
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


def _upgrade_to(database_url: str, revision: str) -> None:
    command.upgrade(_alembic_config(database_url), revision)


def _downgrade_to(database_url: str, revision: str) -> None:
    command.downgrade(_alembic_config(database_url), revision)


def _alembic_config(database_url: str) -> Config:
    repository_root = Path(__file__).resolve().parents[3]
    config = Config()
    config.set_main_option("script_location", str(repository_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _insert_research_run(connection: Connection, run_id: str) -> None:
    _insert_record(connection, "research_runs", "run_id", f"'{run_id}'")


def _insert_record(
    connection: Connection,
    table_name: str,
    relationship_columns: str,
    relationship_values: str,
) -> None:
    connection.execute(
        text(
            f"INSERT INTO {table_name} "
            f"({relationship_columns}, record_json, content_hash, created_at) "
            f"VALUES ({relationship_values}, '{{}}', :digest, :created_at)"
        ),
        {"digest": "a" * 64, "created_at": "2026-07-18T00:00:00+00:00"},
    )


def _insert_claim_version(connection: Connection, claim_id: str, version_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO claim_versions "
            "(claim_version_id, claim_id, version, status, record_json, content_hash, created_at) "
            "VALUES (:version_id, :claim_id, 1, 'PROPOSED', '{}', :digest, :created_at)"
        ),
        {
            "version_id": version_id,
            "claim_id": claim_id,
            "digest": "a" * 64,
            "created_at": "2026-07-18T00:00:00+00:00",
        },
    )


def _insert_evidence(connection: Connection, evidence_id: str) -> None:
    _insert_record(connection, "evidence_records", "evidence_id", f"'{evidence_id}'")


def _seed_progress(connection: Connection) -> None:
    _insert_research_run(connection, "run-1")
    _insert_record(
        connection,
        "progress_plans",
        "plan_version_id, run_id",
        "'plan-1', 'run-1'",
    )
    _insert_record(
        connection,
        "progress_subtasks",
        "subtask_id, plan_version_id",
        "'subtask-1', 'plan-1'",
    )
    _insert_record(
        connection,
        "progress_events",
        "event_id, run_id, plan_version_id, subtask_id",
        "'event-1', 'run-1', 'plan-1', 'subtask-1'",
    )


def _seed_trails(connection: Connection) -> None:
    _insert_claim_version(connection, "claim-1", "claim-1:1")
    _insert_claim_version(connection, "claim-2", "claim-2:1")
    _insert_evidence(connection, "evidence-1")
    _insert_evidence(connection, "evidence-2")
    _insert_record(
        connection,
        "evidence_trail_versions",
        "trail_version_id, trail_id, claim_version_id, version",
        "'trail-version-1', 'trail-1', 'claim-1:1', 1",
    )
    _insert_record(
        connection,
        "evidence_trail_versions",
        "trail_version_id, trail_id, claim_version_id, version",
        "'trail-version-2', 'trail-2', 'claim-2:1', 1",
    )
    _insert_record(
        connection,
        "evidence_trail_nodes",
        "node_id, trail_version_id, evidence_id",
        "'node-1', 'trail-version-1', 'evidence-1'",
    )
    _insert_record(
        connection,
        "evidence_trail_nodes",
        "node_id, trail_version_id, evidence_id",
        "'node-2', 'trail-version-2', 'evidence-2'",
    )
