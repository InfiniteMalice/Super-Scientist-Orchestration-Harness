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

REVISION = "0004_behavioral_rules"

AUTHORITATIVE_0004_TABLES = {
    "rule_incidents",
    "behavioral_rule_versions",
    "reviewer_assessments",
    "rule_consolidation_decisions",
    "rule_regression_cases",
}

REFERENCE_0004_TABLES = {
    "behavioral_rule_version_incidents",
    "reviewer_assessment_rule_versions",
    "reviewer_assessment_incidents",
    "rule_consolidation_assessments",
    "rule_consolidation_incidents",
    "rule_regression_case_incidents",
}

PROJECTION_0004_TABLES = {"behavioral_rule_heads"}

EXPECTED_PRIMARY_KEYS = {
    "rule_incidents": ("incident_id",),
    "behavioral_rule_versions": ("rule_version_id",),
    "reviewer_assessments": ("assessment_id",),
    "rule_consolidation_decisions": ("consolidation_decision_id",),
    "rule_regression_cases": ("regression_case_id",),
    "behavioral_rule_version_incidents": ("rule_version_id", "position"),
    "reviewer_assessment_rule_versions": ("assessment_id", "position"),
    "reviewer_assessment_incidents": ("assessment_id", "position"),
    "rule_consolidation_assessments": ("consolidation_decision_id", "position"),
    "rule_consolidation_incidents": ("consolidation_decision_id", "position"),
    "rule_regression_case_incidents": ("regression_case_id", "position"),
    "behavioral_rule_heads": ("rule_id",),
}

EXPECTED_FOREIGN_KEYS = {
    "behavioral_rule_version_incidents": {
        (("rule_version_id",), "behavioral_rule_versions", ("rule_version_id",)),
        (("incident_id",), "rule_incidents", ("incident_id",)),
    },
    "reviewer_assessment_rule_versions": {
        (("assessment_id",), "reviewer_assessments", ("assessment_id",)),
        (("rule_version_id",), "behavioral_rule_versions", ("rule_version_id",)),
    },
    "reviewer_assessment_incidents": {
        (("assessment_id",), "reviewer_assessments", ("assessment_id",)),
        (("incident_id",), "rule_incidents", ("incident_id",)),
    },
    "rule_consolidation_assessments": {
        (
            ("consolidation_decision_id",),
            "rule_consolidation_decisions",
            ("consolidation_decision_id",),
        ),
        (("assessment_id",), "reviewer_assessments", ("assessment_id",)),
    },
    "rule_consolidation_incidents": {
        (
            ("consolidation_decision_id",),
            "rule_consolidation_decisions",
            ("consolidation_decision_id",),
        ),
        (("incident_id",), "rule_incidents", ("incident_id",)),
    },
    "rule_regression_cases": {
        (("rule_version_id",), "behavioral_rule_versions", ("rule_version_id",)),
    },
    "rule_regression_case_incidents": {
        (("regression_case_id",), "rule_regression_cases", ("regression_case_id",)),
        (("incident_id",), "rule_incidents", ("incident_id",)),
    },
    "behavioral_rule_heads": {
        (
            ("rule_id", "rule_version_id", "semantic_version", "status"),
            "behavioral_rule_versions",
            ("rule_id", "rule_version_id", "semantic_version", "status"),
        ),
    },
}


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'behavioral-rules.db').as_posix()}"


@pytest.mark.integration
def test_clean_upgrade_creates_behavioral_rule_storage(database_url: str) -> None:
    upgrade_database(database_url)

    assert _table_names(database_url) >= (
        AUTHORITATIVE_0004_TABLES | REFERENCE_0004_TABLES | PROJECTION_0004_TABLES
    )
    assert _revision(database_url) == REVISION


@pytest.mark.integration
def test_genuine_0001_database_upgrades_to_0004_without_changing_legacy_rows(
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
                {"digest": "a" * 64, "created_at": _timestamp()},
            )
    finally:
        engine.dispose()

    _upgrade_to(database_url, REVISION)

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT evidence_id FROM evidence_records")).scalar_one()
                == "legacy-evidence"
            )
    finally:
        engine.dispose()
    assert _revision(database_url) == REVISION


@pytest.mark.integration
def test_0003_database_upgrades_to_0004_without_changing_trail_rows(
    database_url: str,
) -> None:
    _upgrade_to(database_url, "0003_progress_and_evidence_trails")
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_record(connection, "research_runs", "run_id", "'existing-run'")
    finally:
        engine.dispose()

    _upgrade_to(database_url, REVISION)

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT run_id FROM research_runs")).scalar_one()
                == "existing-run"
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0004_downgrade_removes_only_rule_storage_and_restores_0003(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    _downgrade_to(database_url, "0003_progress_and_evidence_trails")

    names = _table_names(database_url)
    assert not (
        (AUTHORITATIVE_0004_TABLES | REFERENCE_0004_TABLES | PROJECTION_0004_TABLES) & names
    )
    assert names >= {"research_runs", "progress_plans", "evidence_trail_versions"}
    assert _revision(database_url) == "0003_progress_and_evidence_trails"


@pytest.mark.integration
def test_0004_declares_keys_hash_checks_and_normalized_reference_positions(
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
            for table_name in AUTHORITATIVE_0004_TABLES:
                columns = {column["name"]: column for column in inspector.get_columns(table_name)}
                assert {"record_json", "content_hash", "created_at"} <= columns.keys()
                assert all(columns[name]["nullable"] is False for name in columns)
                checks = " ".join(
                    str(check["sqltext"]) for check in inspector.get_check_constraints(table_name)
                )
                assert "length(content_hash) = 64" in checks
            for table_name in REFERENCE_0004_TABLES:
                checks = " ".join(
                    str(check["sqltext"]) for check in inspector.get_check_constraints(table_name)
                )
                assert "position >= 0" in checks
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0004_declares_all_reference_and_head_foreign_keys(database_url: str) -> None:
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
def test_0004_enforces_unique_rule_semantic_versions_and_assessment_ids(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_rule_version(connection, "rule-1-v1", "rule-1", "1.0.0", "PROPOSED")
            with pytest.raises(IntegrityError):
                _insert_rule_version(
                    connection,
                    "rule-1-v1-copy",
                    "rule-1",
                    "1.0.0",
                    "UNDER_REVIEW",
                )
            _insert_record(connection, "reviewer_assessments", "assessment_id", "'assessment-1'")
            with pytest.raises(IntegrityError):
                _insert_record(
                    connection,
                    "reviewer_assessments",
                    "assessment_id",
                    "'assessment-1'",
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0004_rejects_orphan_incident_assessment_and_regression_references(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _seed_rule_history(connection)
            orphan_statements = (
                "INSERT INTO behavioral_rule_version_incidents "
                "(rule_version_id, position, incident_id) "
                "VALUES ('rule-1-v1', 1, 'missing-incident')",
                "INSERT INTO rule_consolidation_assessments "
                "(consolidation_decision_id, position, assessment_id) "
                "VALUES ('decision-1', 1, 'missing-assessment')",
                "INSERT INTO rule_consolidation_incidents "
                "(consolidation_decision_id, position, incident_id) "
                "VALUES ('decision-1', 1, 'missing-incident')",
                "INSERT INTO rule_regression_case_incidents "
                "(regression_case_id, position, incident_id) "
                "VALUES ('regression-1', 1, 'missing-incident')",
            )
            for statement in orphan_statements:
                with pytest.raises(IntegrityError):
                    connection.execute(text(statement))
    finally:
        engine.dispose()


@pytest.mark.integration
def test_rule_head_requires_exact_immutable_version_identity(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_rule_version(connection, "rule-1-v1", "rule-1", "1.0.0", "ACTIVE")
            connection.execute(
                text(
                    "INSERT INTO behavioral_rule_heads "
                    "(rule_id, rule_version_id, semantic_version, status) "
                    "VALUES ('rule-1', 'rule-1-v1', '1.0.0', 'ACTIVE')"
                )
            )
            for values in (
                "'rule-2', 'missing-version', '1.0.0', 'ACTIVE'",
                "'rule-2', 'rule-1-v1', '1.0.1', 'ACTIVE'",
                "'rule-2', 'rule-1-v1', '1.0.0', 'PROPOSED'",
            ):
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "INSERT INTO behavioral_rule_heads "
                            "(rule_id, rule_version_id, semantic_version, status) "
                            f"VALUES ({values})"
                        )
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


def _timestamp() -> str:
    return "2026-07-19T00:00:00+00:00"


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
        {"digest": "a" * 64, "created_at": _timestamp()},
    )


def _insert_rule_version(
    connection: Connection,
    rule_version_id: str,
    rule_id: str,
    semantic_version: str,
    status: str,
) -> None:
    _insert_record(
        connection,
        "behavioral_rule_versions",
        "rule_version_id, rule_id, semantic_version, status",
        f"'{rule_version_id}', '{rule_id}', '{semantic_version}', '{status}'",
    )


def _seed_rule_history(connection: Connection) -> None:
    _insert_record(connection, "rule_incidents", "incident_id", "'incident-1'")
    _insert_rule_version(connection, "rule-1-v1", "rule-1", "1.0.0", "ACTIVE")
    connection.execute(
        text(
            "INSERT INTO behavioral_rule_version_incidents "
            "(rule_version_id, position, incident_id) "
            "VALUES ('rule-1-v1', 0, 'incident-1')"
        )
    )
    _insert_record(connection, "reviewer_assessments", "assessment_id", "'assessment-1'")
    _insert_record(
        connection,
        "rule_consolidation_decisions",
        "consolidation_decision_id",
        "'decision-1'",
    )
    _insert_record(
        connection,
        "rule_regression_cases",
        "regression_case_id, rule_version_id",
        "'regression-1', 'rule-1-v1'",
    )
