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
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions import models as transaction_models
from super_scientist.providers.storage import schema
from super_scientist.providers.storage.append_only import AppendOnlyRecordRepository
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
    "record_json",
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
VALID_RELATIONSHIPS = {
    "cohort_plans": {"request_id": "request-1"},
    "diversity_assessments": {"cohort_plan_id": "cohort-1"},
    "collaboration_sessions": {"cohort_plan_id": "cohort-1"},
    "peer_requests": {"session_id": "session-1"},
    "peer_contributions": {
        "session_id": "session-1",
        "request_id": "peer-request-1",
    },
    "topology_events": {"session_id": "session-1"},
    "method_direction_outcomes": {"compilation_id": "compilation-1"},
    "compiled_progress_plan_bindings": {"compilation_id": "compilation-1"},
    "guidance_cells": {"protocol_id": "guidance-protocol-1"},
    "model_harness_cells": {"protocol_id": "model-protocol-1"},
    "harness_execution_traces": {"protocol_id": "model-protocol-1"},
    "reward_assessments": {
        "trace_id": "trace-1",
        "observation_id": "observation-1",
    },
}
PARENT_SCOPED_PRIMARY_IDS = {
    "collaboration_terminations": "session-1",
    "model_harness_analyses": "model-protocol-1",
}


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
                for column_name in (identifier, *relationships, "transaction_id"):
                    assert columns[column_name]["type"].length == 200

            trigger_names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger' AND tbl_name IN "
                        f"({', '.join(repr(name) for name in TABLE_SPECS)})"
                    )
                )
            }
            assert trigger_names == {
                f"{table_name}_{operation}"
                for table_name in TABLE_SPECS
                for operation in ("no_update", "no_delete")
            }
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
                for foreign_key in inspector.get_foreign_keys(table_name):
                    if foreign_key["constrained_columns"] in (
                        ["transaction_id"],
                        ["governing_policy_hash"],
                    ):
                        assert foreign_key["options"] == {
                            "deferrable": True,
                            "initially": "DEFERRED",
                        }
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
                    connection.execute(text(f"UPDATE {table_name} SET record_json = record_json"))
                with (
                    connection.begin_nested(),
                    pytest.raises(IntegrityError, match="append-only table"),
                ):
                    connection.execute(text(f"DELETE FROM {table_name}"))
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0007_provenance_parents_may_be_inserted_after_child_in_same_transaction(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_record(
                connection,
                "capability_profiles",
                "profile_id",
                "profile-child-first",
                transaction_id="tx-child-first",
                governing_policy_hash="d" * 64,
            )
            _insert_shared_references(
                connection,
                transaction_id="tx-child-first",
                governing_policy_hash="d" * 64,
            )

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM capability_profiles "
                        "WHERE profile_id = 'profile-child-first'"
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("transaction_id", "governing_policy_hash"),
    (("missing-transaction", "b" * 64), ("tx-1", "e" * 64)),
)
def test_0007_unresolved_provenance_parent_fails_at_commit(
    database_url: str,
    transaction_id: str,
    governing_policy_hash: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            if transaction_id == "tx-1":
                _insert_transaction(connection, transaction_id)
            elif governing_policy_hash == "b" * 64:
                _insert_policy(connection, governing_policy_hash)
            _insert_record(
                connection,
                "capability_profiles",
                "profile_id",
                "profile-unresolved-parent",
                transaction_id=transaction_id,
                governing_policy_hash=governing_policy_hash,
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0007_record_json_decodes_through_public_append_only_repository_contract(
    database_url: str,
) -> None:
    from tests.unit.cognition.test_diversity import _profile
    from tests.unit.domain.test_strict_parsing import _actor

    _upgrade_to(database_url, REVISION)
    profile = _profile("peer-a", prompt_strategy="direct")
    proposal = transaction_models.RecordCapabilityProfile(
        proposal_id="profile-proposal",
        idempotency_key="profile-key",
        proposer=_actor(),
        profile=profile,
    )
    record_json = canonical_json_bytes(profile.model_dump(mode="json")).decode("utf-8")
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_shared_references(
                connection,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=profile.governing_policy_hash,
            )
            _insert_record(
                connection,
                "capability_profiles",
                "profile_id",
                profile.profile_id,
                record_json=record_json,
                content_hash=sha256_hex(record_json.encode("utf-8")),
                transaction_id=proposal.proposal_id,
                governing_policy_hash=profile.governing_policy_hash,
            )
            repository = AppendOnlyRecordRepository(
                connection,
                table=schema.capability_profiles,
                model_type=type(profile),
                identifier_field="profile_id",
            )
            assert repository.get(profile.profile_id) == proposal.profile
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0007_relationship_columns_are_sourced_from_actual_governed_proposals(
    database_url: str,
) -> None:
    from tests.unit.domain.test_strict_parsing import _governed_proposal_examples

    proposals = {
        type(proposal).__name__: proposal
        for proposal in _governed_proposal_examples(dict(vars(transaction_models)))
    }
    termination = proposals["RecordCollaborationTermination"]
    outcome = proposals["RecordMethodDirectionOutcome"]

    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_shared_references(connection)
            _insert_record(
                connection,
                "cohort_plans",
                "cohort_plan_id",
                "cohort-1",
                request_id="request-1",
            )
            _insert_record(
                connection,
                "collaboration_sessions",
                "session_id",
                termination.session_id,
                cohort_plan_id="cohort-1",
            )
            _insert_record(
                connection,
                "collaboration_terminations",
                "session_id",
                termination.session_id,
            )
            _insert_record(
                connection,
                "procedure_compilations",
                "compilation_id",
                outcome.compilation_id,
            )
            _insert_record(
                connection,
                "method_direction_outcomes",
                "outcome_id",
                outcome.outcome.outcome_id,
                compilation_id=outcome.compilation_id,
            )

            assert (
                connection.execute(
                    text("SELECT session_id FROM collaboration_terminations")
                ).scalar_one()
                == termination.session_id
            )
            assert (
                connection.execute(
                    text("SELECT compilation_id FROM method_direction_outcomes")
                ).scalar_one()
                == outcome.compilation_id
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0007_accepts_canonical_colon_delimited_domain_identifiers(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_shared_references(connection)
            _insert_record(
                connection,
                "cohort_plans",
                "cohort_plan_id",
                "request-1:plan",
                request_id="request-1",
            )
            _insert_record(
                connection,
                "diversity_assessments",
                "diversity_assessment_id",
                "request-1:plan:diversity",
                cohort_plan_id="request-1:plan",
            )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("table_name", tuple(TABLE_SPECS))
def test_0007_rejects_unsafe_identifier_storage_classes_for_every_identifier_column(
    database_url: str,
    table_name: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_shared_references(connection)
            _insert_parent_rows(connection, table_name)
            identifier, relationships = TABLE_SPECS[table_name]
            for column_name in (identifier, *relationships, "transaction_id"):
                for invalid in (
                    b"blob-identifier",
                    "",
                    "contains\x00nul",
                    "x" * 201,
                    "-leading-punctuation",
                    "contains/slash",
                ):
                    values = _valid_record_values(table_name)
                    values[column_name] = invalid
                    with (
                        connection.begin_nested(),
                        pytest.raises(
                            IntegrityError,
                            match=f"ck_{table_name}_{column_name}",
                        ),
                    ):
                        _insert_values(connection, table_name, values)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("table_name", tuple(TABLE_SPECS))
def test_0007_rejects_unsafe_hash_storage_classes_for_every_hash_column(
    database_url: str,
    table_name: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_shared_references(connection)
            _insert_parent_rows(connection, table_name)
            for column_name in ("content_hash", "governing_policy_hash"):
                for invalid in (
                    b"a" * 64,
                    "",
                    "a" * 63 + "\x00",
                    "a" * 65,
                    "A" * 64,
                    "g" * 64,
                    "é" * 64,
                ):
                    values = _valid_record_values(table_name)
                    values[column_name] = invalid
                    with (
                        connection.begin_nested(),
                        pytest.raises(
                            IntegrityError,
                            match=f"ck_{table_name}_{column_name}",
                        ),
                    ):
                        _insert_values(connection, table_name, values)
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


def _record_graph_rows() -> tuple[tuple[str, str, str, dict[str, str]], ...]:
    return (
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


def _insert_complete_record_graph(connection: Connection) -> None:
    for table_name, identifier, record_id, relationships in _record_graph_rows():
        _insert_record(connection, table_name, identifier, record_id, **relationships)


def _insert_parent_rows(connection: Connection, target_table: str) -> None:
    for table_name, identifier, record_id, relationships in _record_graph_rows():
        if table_name == target_table:
            return
        _insert_record(connection, table_name, identifier, record_id, **relationships)
    raise AssertionError(f"unknown table {target_table}")


def _valid_record_values(table_name: str) -> dict[str, object]:
    identifier, _ = TABLE_SPECS[table_name]
    return {
        identifier: PARENT_SCOPED_PRIMARY_IDS.get(table_name, "probe-record"),
        **VALID_RELATIONSHIPS.get(table_name, {}),
        "schema_version": 1,
        "record_json": "{}",
        "content_hash": "a" * 64,
        "transaction_id": "tx-1",
        "governing_policy_hash": "b" * 64,
        "created_at": "2026-08-23T00:00:00+00:00",
    }


def _insert_record(
    connection: Connection,
    table_name: str,
    identifier: str,
    record_id: str,
    record_json: str = "{}",
    content_hash: str = "a" * 64,
    transaction_id: str = "tx-1",
    governing_policy_hash: str = "b" * 64,
    **relationships: str,
) -> None:
    values: dict[str, object] = {
        identifier: record_id,
        **relationships,
        "schema_version": 1,
        "record_json": record_json,
        "content_hash": content_hash,
        "transaction_id": transaction_id,
        "governing_policy_hash": governing_policy_hash,
        "created_at": "2026-08-23T00:00:00+00:00",
    }
    _insert_values(connection, table_name, values)


def _insert_values(
    connection: Connection,
    table_name: str,
    values: dict[str, object],
) -> None:
    columns = ", ".join(values)
    parameters = ", ".join(f":{column}" for column in values)
    connection.execute(
        text(f"INSERT INTO {table_name} ({columns}) VALUES ({parameters})"),
        values,
    )


def _insert_policy(connection: Connection, governing_policy_hash: str) -> None:
    connection.execute(
        text(
            "INSERT INTO governance_policies (policy_hash, policy_json, created_at) "
            "VALUES (:policy_hash, '{}', :created_at)"
        ),
        {
            "policy_hash": governing_policy_hash,
            "created_at": "2026-08-23T00:00:00+00:00",
        },
    )


def _insert_shared_references(
    connection: Connection,
    *,
    transaction_id: str = "tx-1",
    governing_policy_hash: str = "b" * 64,
) -> None:
    _insert_policy(connection, governing_policy_hash)
    _insert_transaction(connection, transaction_id)


def _insert_transaction(connection: Connection, transaction_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO transactions "
            "(proposal_id, idempotency_key, intent_fingerprint, proposal_hash, "
            "proposal_json, decision_json, created_at) VALUES "
            "(:transaction_id, :idempotency_key, NULL, :proposal_hash, '{}', '{}', :created_at)"
        ),
        {
            "transaction_id": transaction_id,
            "idempotency_key": f"key-{transaction_id}",
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
