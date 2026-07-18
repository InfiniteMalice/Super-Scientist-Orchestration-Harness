from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from super_scientist.providers.storage.database import (
    create_database_engine,
    upgrade_database,
)

AUTHORITATIVE_0002_TABLES = {
    "research_runs",
    "research_run_events",
    "configuration_versions",
    "self_improvement_measurements",
    "evaluator_audits",
    "evaluator_versions",
    "evaluator_succession_decisions",
    "evaluator_collapse_records",
}

EXPECTED_FOREIGN_KEYS = {
    "research_run_events": {"run_id": ("research_runs", "run_id")},
    "self_improvement_measurements": {
        "run_id": ("research_runs", "run_id"),
        "evaluator_audit_id": ("evaluator_audits", "evaluator_audit_id"),
    },
    "evaluator_succession_decisions": {
        "predecessor_evaluator_version_id": ("evaluator_versions", "evaluator_version_id"),
        "candidate_evaluator_version_id": ("evaluator_versions", "evaluator_version_id"),
        "evaluator_audit_id": ("evaluator_audits", "evaluator_audit_id"),
    },
    "evaluator_collapse_records": {
        "evaluator_version_id": ("evaluator_versions", "evaluator_version_id"),
    },
    "research_run_heads": {"run_event_id": ("research_run_events", "run_event_id")},
    "evaluator_heads": {"evaluator_version_id": ("evaluator_versions", "evaluator_version_id")},
}


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'adaptation.db').as_posix()}"


@pytest.mark.integration
def test_clean_upgrade_creates_governed_adaptation_foundation(database_url: str) -> None:
    upgrade_database(database_url)

    table_names = _table_names(database_url)
    assert table_names >= AUTHORITATIVE_0002_TABLES
    assert table_names >= {"research_run_heads", "evaluator_heads"}
    assert _revision(database_url) == "0002_governed_adaptation_foundation"


@pytest.mark.integration
def test_0001_upgrades_to_0002_with_append_only_triggers(database_url: str) -> None:
    _upgrade_to(database_url, "0001_epistemic_kernel")
    _upgrade_to(database_url, "0002_governed_adaptation_foundation")

    assert _table_names(database_url) >= AUTHORITATIVE_0002_TABLES
    for table_name in AUTHORITATIVE_0002_TABLES:
        _assert_update_and_delete_raise_append_only(database_url, table_name)


@pytest.mark.integration
def test_0002_downgrades_to_0001(database_url: str) -> None:
    _upgrade_to(database_url, "0002_governed_adaptation_foundation")
    _downgrade_to(database_url, "0001_epistemic_kernel")

    assert not (AUTHORITATIVE_0002_TABLES & _table_names(database_url))
    assert _revision(database_url) == "0001_epistemic_kernel"


@pytest.mark.integration
def test_new_foreign_keys_are_declared_and_enforced(database_url: str) -> None:
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            for table_name, expected in EXPECTED_FOREIGN_KEYS.items():
                actual = {
                    foreign_key["constrained_columns"][0]: (
                        foreign_key["referred_table"],
                        foreign_key["referred_columns"][0],
                    )
                    for foreign_key in inspector.get_foreign_keys(table_name)
                }
                assert actual == expected

        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO research_run_events "
                    "(run_event_id, run_id, record_json, content_hash, created_at) "
                    "VALUES ('event-1', 'missing-run', '{}', :digest, :created_at)"
                ),
                {"digest": "a" * 64, "created_at": "2026-07-18T00:00:00+00:00"},
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_sqlite_foreign_keys_are_enabled_on_every_fresh_engine_connection(
    database_url: str,
) -> None:
    upgrade_database(database_url)

    first_engine = create_database_engine(database_url)
    second_engine = create_database_engine(database_url)
    try:
        with first_engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        with second_engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        first_engine.dispose()
        second_engine.dispose()


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


def _assert_update_and_delete_raise_append_only(database_url: str, table_name: str) -> None:
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_authoritative_row(connection, table_name)
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(f"UPDATE {table_name} SET record_json = record_json"))
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(f"DELETE FROM {table_name}"))
    finally:
        engine.dispose()


def _insert_authoritative_row(connection: Connection, table_name: str) -> None:
    timestamp = "2026-07-18T00:00:00+00:00"
    digest = "a" * 64
    connection.execute(
        text(
            "INSERT OR IGNORE INTO research_runs "
            "(run_id, record_json, content_hash, created_at) "
            "VALUES ('run-1', '{}', :digest, :created_at)"
        ),
        {"digest": digest, "created_at": timestamp},
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO evaluator_audits "
            "(evaluator_audit_id, record_json, content_hash, created_at) "
            "VALUES ('audit-1', '{}', :digest, :created_at)"
        ),
        {"digest": digest, "created_at": timestamp},
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO evaluator_versions "
            "(evaluator_version_id, record_json, content_hash, created_at) "
            "VALUES ('evaluator-1', '{}', :digest, :created_at), "
            "('evaluator-2', '{}', :digest, :created_at)"
        ),
        {"digest": digest, "created_at": timestamp},
    )
    inserts = {
        "research_runs": (
            "INSERT INTO research_runs (run_id, record_json, content_hash, created_at) "
            "VALUES ('run-target', '{}', :digest, :created_at)"
        ),
        "research_run_events": (
            "INSERT INTO research_run_events "
            "(run_event_id, run_id, record_json, content_hash, created_at) "
            "VALUES ('event-target', 'run-1', '{}', :digest, :created_at)"
        ),
        "configuration_versions": (
            "INSERT INTO configuration_versions "
            "(configuration_version_id, record_json, content_hash, created_at) "
            "VALUES ('configuration-target', '{}', :digest, :created_at)"
        ),
        "self_improvement_measurements": (
            "INSERT INTO self_improvement_measurements "
            "(measurement_id, run_id, evaluator_audit_id, record_json, content_hash, created_at) "
            "VALUES ('measurement-target', 'run-1', 'audit-1', '{}', :digest, :created_at)"
        ),
        "evaluator_audits": (
            "INSERT INTO evaluator_audits "
            "(evaluator_audit_id, record_json, content_hash, created_at) "
            "VALUES ('audit-target', '{}', :digest, :created_at)"
        ),
        "evaluator_versions": (
            "INSERT INTO evaluator_versions "
            "(evaluator_version_id, record_json, content_hash, created_at) "
            "VALUES ('evaluator-target', '{}', :digest, :created_at)"
        ),
        "evaluator_succession_decisions": (
            "INSERT INTO evaluator_succession_decisions "
            "(evaluator_succession_decision_id, predecessor_evaluator_version_id, "
            "candidate_evaluator_version_id, evaluator_audit_id, record_json, content_hash, "
            "created_at) "
            "VALUES ('succession-target', 'evaluator-1', 'evaluator-2', 'audit-1', '{}', "
            ":digest, :created_at)"
        ),
        "evaluator_collapse_records": (
            "INSERT INTO evaluator_collapse_records "
            "(evaluator_collapse_record_id, evaluator_version_id, record_json, content_hash, "
            "created_at) "
            "VALUES ('collapse-target', 'evaluator-1', '{}', :digest, :created_at)"
        ),
    }
    connection.execute(text(inserts[table_name]), {"digest": digest, "created_at": timestamp})
