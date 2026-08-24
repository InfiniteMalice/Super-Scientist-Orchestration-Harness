from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from alembic import command
from super_scientist.providers.storage import schema
from super_scientist.providers.storage.database import create_database_engine

REVISION = "0007_governed_cognitive_procedures"
PREVIOUS_REVISION = "0006_handbook_and_harness_evaluation"

TABLE_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "capability_profiles": ("profile_id", ()),
    "cohort_plans": ("cohort_plan_id", ("request_id",)),
    "diversity_assessments": ("diversity_assessment_id", ("cohort_plan_id",)),
    "collaboration_sessions": ("session_id", ("cohort_plan_id",)),
    "peer_requests": ("request_id", ("session_id",)),
    "peer_contributions": ("contribution_id", ("session_id", "request_id")),
    "topology_events": ("event_id", ("session_id",)),
    "collaboration_terminations": ("session_id", ()),
    "procedure_compilations": ("compilation_id", ()),
    "method_direction_outcomes": ("outcome_id", ("compilation_id",)),
    "compiled_progress_plan_bindings": ("binding_id", ("compilation_id",)),
    "guidance_protocols": ("protocol_id", ()),
    "guidance_cells": ("cell_id", ("protocol_id",)),
    "model_harness_protocols": ("protocol_id", ()),
    "model_harness_cells": ("cell_id", ("protocol_id",)),
    "model_harness_analyses": ("protocol_id", ()),
    "harness_execution_traces": ("trace_id", ("protocol_id",)),
    "reward_assessments": ("assessment_id", ("trace_id", "observation_id")),
}

EXPECTED_0007_TABLES = set(TABLE_SPECS)
SHARED_COLUMNS = {
    "schema_version",
    "payload_json",
    "content_hash",
    "transaction_id",
    "governing_policy_hash",
    "created_at",
}
LEGACY_REVISIONS = (
    "0001_epistemic_kernel",
    "0002_governed_adaptation_foundation",
    "0003_progress_and_evidence_trails",
    "0004_behavioral_rules",
    "0005_hypotheses_and_representations",
    PREVIOUS_REVISION,
)


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{(tmp_path / 'migration-0007.db').as_posix()}"


@pytest.mark.integration
def test_clean_upgrade_creates_exact_0007_tables_with_domain_keys_and_relationship_indexes(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) >= EXPECTED_0007_TABLES
            for table_name, (identifier, relationships) in TABLE_SPECS.items():
                columns = {column["name"]: column for column in inspector.get_columns(table_name)}
                assert set(columns) == {identifier, *relationships, *SHARED_COLUMNS}
                assert inspector.get_pk_constraint(table_name)["constrained_columns"] == [
                    identifier
                ]
                indexed_columns = {
                    tuple(index["column_names"]) for index in inspector.get_indexes(table_name)
                }
                expected_indexes = {(relationship,) for relationship in relationships} | {
                    ("transaction_id",),
                    ("governing_policy_hash",),
                }
                assert indexed_columns >= expected_indexes
                assert all(columns[column]["nullable"] is False for column in columns)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_every_0007_table_foreign_keys_transaction_and_governing_policy(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            for table_name in TABLE_SPECS:
                foreign_keys = {
                    (
                        tuple(foreign_key["constrained_columns"]),
                        foreign_key["referred_table"],
                        tuple(foreign_key["referred_columns"]),
                    )
                    for foreign_key in inspector.get_foreign_keys(table_name)
                }
                assert (("transaction_id",), "transactions", ("proposal_id",)) in foreign_keys
                assert (
                    ("governing_policy_hash",),
                    "governance_policies",
                    ("policy_hash",),
                ) in foreign_keys
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0007_enforces_parent_relationships_and_scoped_peer_request_identity(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_shared_references(connection)
            with pytest.raises(IntegrityError):
                _insert_record(
                    connection,
                    "diversity_assessments",
                    "diversity_assessment_id",
                    "assessment-missing-parent",
                    cohort_plan_id="missing-plan",
                )

            _insert_record(connection, "capability_profiles", "profile_id", "profile-1")
            _insert_record(
                connection,
                "cohort_plans",
                "cohort_plan_id",
                "cohort-1",
                request_id="cohort-request-1",
            )
            _insert_record(
                connection,
                "collaboration_sessions",
                "session_id",
                "session-1",
                cohort_plan_id="cohort-1",
            )
            _insert_record(
                connection,
                "collaboration_sessions",
                "session_id",
                "session-2",
                cohort_plan_id="cohort-1",
            )
            _insert_record(
                connection,
                "peer_requests",
                "request_id",
                "peer-request-1",
                session_id="session-1",
            )
            with pytest.raises(IntegrityError):
                _insert_record(
                    connection,
                    "peer_contributions",
                    "contribution_id",
                    "contribution-cross-session",
                    session_id="session-2",
                    request_id="peer-request-1",
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_every_0007_table_rejects_update_and_delete(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_shared_references(connection)
            _insert_complete_record_graph(connection)
            for table_name in TABLE_SPECS:
                with (
                    connection.begin_nested(),
                    pytest.raises(IntegrityError, match="append-only table"),
                ):
                    connection.execute(text(f"UPDATE {table_name} SET payload_json = payload_json"))
                with (
                    connection.begin_nested(),
                    pytest.raises(IntegrityError, match="append-only table"),
                ):
                    connection.execute(text(f"DELETE FROM {table_name}"))
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("legacy_revision", LEGACY_REVISIONS)
def test_every_released_migration_revision_upgrades_to_0007(
    database_url: str,
    legacy_revision: str,
) -> None:
    _upgrade_to(database_url, legacy_revision)
    _upgrade_to(database_url, REVISION)

    assert _revision(database_url) == REVISION
    assert _table_names(database_url) >= EXPECTED_0007_TABLES


@pytest.mark.integration
def test_0006_rows_survive_0007_upgrade_byte_for_byte(database_url: str) -> None:
    _upgrade_to(database_url, PREVIOUS_REVISION)
    legacy = {
        "campaign_id": "legacy-campaign",
        "version": 1,
        "record_json": '{"legacy":true}',
        "content_hash": "a" * 64,
        "created_at": "2026-08-23T00:00:00+00:00",
    }
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO harness_campaigns "
                    "(campaign_id, version, record_json, content_hash, created_at) "
                    "VALUES (:campaign_id, :version, :record_json, :content_hash, :created_at)"
                ),
                legacy,
            )
        _upgrade_to(database_url, REVISION)
        with engine.connect() as connection:
            actual = dict(
                connection.execute(
                    text(
                        "SELECT campaign_id, version, record_json, content_hash, created_at "
                        "FROM harness_campaigns WHERE campaign_id = :campaign_id"
                    ),
                    {"campaign_id": legacy["campaign_id"]},
                )
                .mappings()
                .one()
            )
        assert actual == legacy
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0007_downgrade_removes_only_0007_objects_and_reupgrade_restores_exact_schema(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    expected_schema = _schema_fingerprint(database_url)
    prior_tables = _table_names(database_url) - EXPECTED_0007_TABLES

    _downgrade_to(database_url, PREVIOUS_REVISION)
    assert _revision(database_url) == PREVIOUS_REVISION
    assert _table_names(database_url) == prior_tables

    _upgrade_to(database_url, REVISION)
    assert _schema_fingerprint(database_url) == expected_schema


@pytest.mark.integration
def test_runtime_metadata_exactly_matches_0007(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            revision_metadata = MetaData()
            for table_name in sorted(
                set(inspect(connection).get_table_names()) - {"alembic_version"}
            ):
                schema.metadata.tables[table_name].to_metadata(revision_metadata)
            assert compare_metadata(MigrationContext.configure(connection), revision_metadata) == []
    finally:
        engine.dispose()


def _insert_complete_record_graph(connection: Connection) -> None:
    rows = (
        ("capability_profiles", "profile_id", "profile-1", {}),
        ("cohort_plans", "cohort_plan_id", "cohort-1", {"request_id": "request-1"}),
        (
            "diversity_assessments",
            "diversity_assessment_id",
            "diversity-1",
            {"cohort_plan_id": "cohort-1"},
        ),
        (
            "collaboration_sessions",
            "session_id",
            "session-1",
            {"cohort_plan_id": "cohort-1"},
        ),
        ("peer_requests", "request_id", "peer-request-1", {"session_id": "session-1"}),
        (
            "peer_contributions",
            "contribution_id",
            "contribution-1",
            {"session_id": "session-1", "request_id": "peer-request-1"},
        ),
        ("topology_events", "event_id", "event-1", {"session_id": "session-1"}),
        ("collaboration_terminations", "session_id", "session-1", {}),
        ("procedure_compilations", "compilation_id", "compilation-1", {}),
        (
            "method_direction_outcomes",
            "outcome_id",
            "outcome-1",
            {"compilation_id": "compilation-1"},
        ),
        (
            "compiled_progress_plan_bindings",
            "binding_id",
            "binding-1",
            {"compilation_id": "compilation-1"},
        ),
        ("guidance_protocols", "protocol_id", "guidance-protocol-1", {}),
        (
            "guidance_cells",
            "cell_id",
            "guidance-cell-1",
            {"protocol_id": "guidance-protocol-1"},
        ),
        ("model_harness_protocols", "protocol_id", "model-protocol-1", {}),
        (
            "model_harness_cells",
            "cell_id",
            "model-cell-1",
            {"protocol_id": "model-protocol-1"},
        ),
        ("model_harness_analyses", "protocol_id", "model-protocol-1", {}),
        (
            "harness_execution_traces",
            "trace_id",
            "trace-1",
            {"protocol_id": "model-protocol-1"},
        ),
        (
            "reward_assessments",
            "assessment_id",
            "assessment-1",
            {"trace_id": "trace-1", "observation_id": "observation-1"},
        ),
    )
    for table_name, identifier, record_id, relationships in rows:
        _insert_record(connection, table_name, identifier, record_id, **relationships)


def _insert_record(
    connection: Connection,
    table_name: str,
    identifier: str,
    record_id: str,
    **relationships: str,
) -> None:
    values: dict[str, object] = {
        identifier: record_id,
        **relationships,
        "schema_version": 1,
        "payload_json": "{}",
        "content_hash": "a" * 64,
        "transaction_id": "tx-1",
        "governing_policy_hash": "b" * 64,
        "created_at": "2026-08-23T00:00:00+00:00",
    }
    columns = ", ".join(values)
    parameters = ", ".join(f":{column}" for column in values)
    connection.execute(
        text(f"INSERT INTO {table_name} ({columns}) VALUES ({parameters})"),
        values,
    )


def _insert_shared_references(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO governance_policies (policy_hash, policy_json, created_at) "
            "VALUES (:policy_hash, '{}', :created_at)"
        ),
        {
            "policy_hash": "b" * 64,
            "created_at": "2026-08-23T00:00:00+00:00",
        },
    )
    connection.execute(
        text(
            "INSERT INTO transactions "
            "(proposal_id, idempotency_key, intent_fingerprint, proposal_hash, "
            "proposal_json, decision_json, created_at) VALUES "
            "('tx-1', 'idempotency-1', NULL, :proposal_hash, '{}', '{}', :created_at)"
        ),
        {
            "proposal_hash": "c" * 64,
            "created_at": "2026-08-23T00:00:00+00:00",
        },
    )


def _schema_fingerprint(database_url: str) -> dict[str, Any]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            return {
                table_name: {
                    "columns": tuple(
                        (
                            column["name"],
                            str(column["type"]),
                            column["nullable"],
                            column.get("default"),
                        )
                        for column in inspector.get_columns(table_name)
                    ),
                    "pk": tuple(inspector.get_pk_constraint(table_name)["constrained_columns"]),
                    "fks": tuple(
                        sorted(
                            (
                                tuple(foreign_key["constrained_columns"]),
                                foreign_key["referred_table"],
                                tuple(foreign_key["referred_columns"]),
                            )
                            for foreign_key in inspector.get_foreign_keys(table_name)
                        )
                    ),
                    "indexes": tuple(
                        sorted(
                            (
                                index["name"],
                                tuple(index["column_names"]),
                                index["unique"],
                            )
                            for index in inspector.get_indexes(table_name)
                        )
                    ),
                    "checks": tuple(
                        sorted(
                            (constraint["name"], constraint["sqltext"])
                            for constraint in inspector.get_check_constraints(table_name)
                        )
                    ),
                }
                for table_name in sorted(inspector.get_table_names())
                if table_name != "alembic_version"
            }
    finally:
        engine.dispose()


def _table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def _revision(database_url: str) -> str:
    engine = create_engine(database_url)
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
