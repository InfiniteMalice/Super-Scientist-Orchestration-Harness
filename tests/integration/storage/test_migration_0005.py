from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from super_scientist.providers.storage import schema
from super_scientist.providers.storage.database import create_database_engine

REVISION = "0005_hypotheses_and_representations"
PREVIOUS_REVISION = "0004_behavioral_rules"

AUTHORITATIVE_0005_TABLES = {
    "primitive_versions",
    "primitive_evaluations",
    "hypothesis_versions",
    "executable_model_specs",
    "verification_mechanism_specs",
    "verification_results",
    "simulation_results",
    "counterexample_records",
    "hypothesis_revisions",
    "hypothesis_admission_decisions",
}

REFERENCE_0005_TABLES = {
    "primitive_version_predecessors",
    "primitive_version_dependencies",
    "primitive_version_measurements",
    "primitive_evaluation_verification_results",
    "primitive_evaluation_evidence",
    "hypothesis_version_primitives",
    "hypothesis_version_evidence",
    "verification_result_simulations",
    "counterexample_simulations",
    "counterexample_verification_results",
    "counterexample_evidence",
    "hypothesis_revision_verification_results",
    "hypothesis_revision_counterexamples",
    "hypothesis_admission_models",
    "hypothesis_admission_verification_results",
    "hypothesis_admission_counterexamples",
    "hypothesis_admission_revisions",
}

PROJECTION_0005_TABLES = {"primitive_heads", "hypothesis_heads"}

EXPECTED_PRIMARY_KEYS = {
    "primitive_versions": ("primitive_version_id",),
    "primitive_evaluations": ("primitive_evaluation_id",),
    "hypothesis_versions": ("hypothesis_version_id",),
    "executable_model_specs": ("model_spec_id",),
    "verification_mechanism_specs": ("mechanism_spec_id",),
    "verification_results": ("verification_result_id",),
    "simulation_results": ("simulation_result_id",),
    "counterexample_records": ("counterexample_id",),
    "hypothesis_revisions": ("revision_id",),
    "hypothesis_admission_decisions": ("admission_decision_id",),
    "primitive_heads": ("primitive_id",),
    "hypothesis_heads": ("hypothesis_id",),
}

REFERENCE_ENDPOINTS = {
    "primitive_version_predecessors": (
        "primitive_version_id",
        "primitive_versions",
        "predecessor_primitive_version_id",
        "primitive_versions",
    ),
    "primitive_version_dependencies": (
        "primitive_version_id",
        "primitive_versions",
        "dependency_primitive_version_id",
        "primitive_versions",
    ),
    "primitive_version_measurements": (
        "primitive_version_id",
        "primitive_versions",
        "measurement_id",
        "self_improvement_measurements",
    ),
    "primitive_evaluation_verification_results": (
        "primitive_evaluation_id",
        "primitive_evaluations",
        "verification_result_id",
        "verification_results",
    ),
    "primitive_evaluation_evidence": (
        "primitive_evaluation_id",
        "primitive_evaluations",
        "evidence_id",
        "evidence_records",
    ),
    "hypothesis_version_primitives": (
        "hypothesis_version_id",
        "hypothesis_versions",
        "primitive_version_id",
        "primitive_versions",
    ),
    "hypothesis_version_evidence": (
        "hypothesis_version_id",
        "hypothesis_versions",
        "evidence_id",
        "evidence_records",
    ),
    "verification_result_simulations": (
        "verification_result_id",
        "verification_results",
        "simulation_result_id",
        "simulation_results",
    ),
    "counterexample_simulations": (
        "counterexample_id",
        "counterexample_records",
        "simulation_result_id",
        "simulation_results",
    ),
    "counterexample_verification_results": (
        "counterexample_id",
        "counterexample_records",
        "verification_result_id",
        "verification_results",
    ),
    "counterexample_evidence": (
        "counterexample_id",
        "counterexample_records",
        "evidence_id",
        "evidence_records",
    ),
    "hypothesis_revision_verification_results": (
        "revision_id",
        "hypothesis_revisions",
        "verification_result_id",
        "verification_results",
    ),
    "hypothesis_revision_counterexamples": (
        "revision_id",
        "hypothesis_revisions",
        "counterexample_id",
        "counterexample_records",
    ),
    "hypothesis_admission_models": (
        "admission_decision_id",
        "hypothesis_admission_decisions",
        "model_spec_id",
        "executable_model_specs",
    ),
    "hypothesis_admission_verification_results": (
        "admission_decision_id",
        "hypothesis_admission_decisions",
        "verification_result_id",
        "verification_results",
    ),
    "hypothesis_admission_counterexamples": (
        "admission_decision_id",
        "hypothesis_admission_decisions",
        "counterexample_id",
        "counterexample_records",
    ),
    "hypothesis_admission_revisions": (
        "admission_decision_id",
        "hypothesis_admission_decisions",
        "revision_id",
        "hypothesis_revisions",
    ),
}

SCOPED_REFERENCE_FOREIGN_KEYS = {
    "verification_result_simulations": (
        (
            (
                "verification_result_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
            "verification_results",
            (
                "verification_result_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
        ),
        (
            (
                "simulation_result_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
            "simulation_results",
            (
                "simulation_result_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "execution_mode",
            ),
        ),
    ),
    "counterexample_simulations": (
        (
            (
                "counterexample_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
            "counterexample_records",
            (
                "counterexample_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
        ),
        (
            (
                "simulation_result_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
            "simulation_results",
            (
                "simulation_result_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "execution_mode",
            ),
        ),
    ),
    "counterexample_verification_results": (
        (
            (
                "counterexample_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
            "counterexample_records",
            (
                "counterexample_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
        ),
        (
            (
                "verification_result_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
            "verification_results",
            (
                "verification_result_id",
                "hypothesis_id",
                "hypothesis_version_id",
                "model_spec_id",
                "model_execution_mode",
            ),
        ),
    ),
    "hypothesis_revision_verification_results": (
        (
            ("revision_id", "hypothesis_id"),
            "hypothesis_revisions",
            ("revision_id", "hypothesis_id"),
        ),
        (
            ("verification_result_id", "hypothesis_id"),
            "verification_results",
            ("verification_result_id", "hypothesis_id"),
        ),
    ),
    "hypothesis_revision_counterexamples": (
        (
            ("revision_id", "hypothesis_id"),
            "hypothesis_revisions",
            ("revision_id", "hypothesis_id"),
        ),
        (
            ("counterexample_id", "hypothesis_id"),
            "counterexample_records",
            ("counterexample_id", "hypothesis_id"),
        ),
    ),
    "hypothesis_admission_models": (
        (
            ("admission_decision_id", "hypothesis_id"),
            "hypothesis_admission_decisions",
            ("admission_decision_id", "hypothesis_id"),
        ),
        (
            ("model_spec_id", "hypothesis_id"),
            "executable_model_specs",
            ("model_spec_id", "hypothesis_id"),
        ),
    ),
    "hypothesis_admission_verification_results": (
        (
            ("admission_decision_id", "hypothesis_id"),
            "hypothesis_admission_decisions",
            ("admission_decision_id", "hypothesis_id"),
        ),
        (
            ("verification_result_id", "hypothesis_id"),
            "verification_results",
            ("verification_result_id", "hypothesis_id"),
        ),
    ),
    "hypothesis_admission_counterexamples": (
        (
            ("admission_decision_id", "hypothesis_id"),
            "hypothesis_admission_decisions",
            ("admission_decision_id", "hypothesis_id"),
        ),
        (
            ("counterexample_id", "hypothesis_id"),
            "counterexample_records",
            ("counterexample_id", "hypothesis_id"),
        ),
    ),
    "hypothesis_admission_revisions": (
        (
            ("admission_decision_id", "hypothesis_id"),
            "hypothesis_admission_decisions",
            ("admission_decision_id", "hypothesis_id"),
        ),
        (
            ("revision_id", "hypothesis_id"),
            "hypothesis_revisions",
            ("revision_id", "hypothesis_id"),
        ),
    ),
}


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'hypotheses.db').as_posix()}"


@pytest.mark.integration
def test_clean_upgrade_creates_hypothesis_and_representation_storage(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)

    assert _table_names(database_url) >= (
        AUTHORITATIVE_0005_TABLES | REFERENCE_0005_TABLES | PROJECTION_0005_TABLES
    )
    assert _revision(database_url) == REVISION


@pytest.mark.integration
def test_genuine_0001_database_upgrades_to_0005_without_changing_legacy_rows(
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
def test_0004_database_upgrades_to_0005_without_changing_rule_rows(
    database_url: str,
) -> None:
    _upgrade_to(database_url, PREVIOUS_REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_record(connection, "rule_incidents", "incident_id", "'existing-incident'")
    finally:
        engine.dispose()

    _upgrade_to(database_url, REVISION)

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT incident_id FROM rule_incidents")).scalar_one()
                == "existing-incident"
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0005_downgrade_removes_only_0005_storage_and_restores_0004(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    _downgrade_to(database_url, PREVIOUS_REVISION)

    names = _table_names(database_url)
    assert not (
        (AUTHORITATIVE_0005_TABLES | REFERENCE_0005_TABLES | PROJECTION_0005_TABLES) & names
    )
    assert names >= {"rule_incidents", "behavioral_rule_versions", "behavioral_rule_heads"}
    assert _revision(database_url) == PREVIOUS_REVISION


@pytest.mark.integration
def test_0005_declares_record_columns_primary_keys_and_ordered_references(
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
            for table_name in AUTHORITATIVE_0005_TABLES:
                columns = {column["name"]: column for column in inspector.get_columns(table_name)}
                assert {"record_json", "content_hash", "created_at"} <= columns.keys()
                assert all(
                    columns[name]["nullable"] is False
                    for name in ("record_json", "content_hash", "created_at")
                )
                checks = " ".join(
                    str(check["sqltext"]) for check in inspector.get_check_constraints(table_name)
                )
                assert "length(content_hash) = 64" in checks
            for table_name in REFERENCE_0005_TABLES:
                primary_key = tuple(inspector.get_pk_constraint(table_name)["constrained_columns"])
                assert primary_key[1] == "position"
                checks = " ".join(
                    str(check["sqltext"]) for check in inspector.get_check_constraints(table_name)
                )
                assert "position >= 0" in checks
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0005_normalized_references_have_owner_and_target_foreign_keys(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            for table_name, endpoints in REFERENCE_ENDPOINTS.items():
                owner_column, owner_table, reference_column, reference_table = endpoints
                actual = {
                    (
                        tuple(foreign_key["constrained_columns"]),
                        str(foreign_key["referred_table"]),
                    )
                    for foreign_key in inspector.get_foreign_keys(table_name)
                }
                assert ((owner_column,), owner_table) in actual
                assert ((reference_column,), reference_table) in actual
    finally:
        engine.dispose()


@pytest.mark.integration
def test_hypothesis_references_have_composite_scope_foreign_keys(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            for table_name, expected_foreign_keys in SCOPED_REFERENCE_FOREIGN_KEYS.items():
                actual = {
                    (
                        tuple(item["constrained_columns"]),
                        str(item["referred_table"]),
                        tuple(item["referred_columns"]),
                    )
                    for item in inspector.get_foreign_keys(table_name)
                }
                assert set(expected_foreign_keys) <= actual
    finally:
        engine.dispose()


@pytest.mark.integration
def test_admission_revision_owner_foreign_keys_are_initially_deferred(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            owner_foreign_keys = tuple(
                item
                for item in inspect(connection).get_foreign_keys("hypothesis_admission_revisions")
                if item["referred_table"] == "hypothesis_admission_decisions"
            )
            assert len(owner_foreign_keys) == 2
            assert all(
                item["options"] == {"deferrable": True, "initially": "DEFERRED"}
                for item in owner_foreign_keys
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_revision_requires_existing_prior_and_resulting_versions(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_hypothesis_version(
                connection,
                "hypothesis-1-v1",
                "hypothesis-1",
                1,
                "SOURCE_PATTERN_REVIEW_PENDING",
            )
            _insert_hypothesis_version(
                connection,
                "hypothesis-1-v2",
                "hypothesis-1",
                2,
                "TRANSFER_TESTING",
            )
            with pytest.raises(IntegrityError):
                _insert_revision(
                    connection,
                    "missing-prior",
                    "hypothesis-1",
                    "missing-version",
                    1,
                    "hypothesis-1-v2",
                    2,
                )
            with pytest.raises(IntegrityError):
                _insert_revision(
                    connection,
                    "missing-result",
                    "hypothesis-1",
                    "hypothesis-1-v1",
                    1,
                    "missing-version",
                    2,
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0005_enforces_unique_primitive_and_hypothesis_versions(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_primitive_version(
                connection,
                "primitive-1-v1",
                "primitive-1",
                "1.0.0",
                "PROPOSED",
            )
            with pytest.raises(IntegrityError):
                _insert_primitive_version(
                    connection,
                    "primitive-1-v1-copy",
                    "primitive-1",
                    "1.0.0",
                    "EXPERIMENTAL",
                )
            _insert_hypothesis_version(
                connection,
                "hypothesis-1-v1",
                "hypothesis-1",
                1,
                "SOURCE_PATTERN_REVIEW_PENDING",
            )
            with pytest.raises(IntegrityError):
                _insert_hypothesis_version(
                    connection,
                    "hypothesis-1-v1-copy",
                    "hypothesis-1",
                    1,
                    "TRANSFER_TESTING",
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_discriminators_are_materialized_and_exact_relationships_are_foreign_keyed(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            model_columns = {
                item["name"] for item in inspector.get_columns("executable_model_specs")
            }
            mechanism_columns = {
                item["name"] for item in inspector.get_columns("verification_mechanism_specs")
            }
            result_columns = {
                item["name"] for item in inspector.get_columns("verification_results")
            }
            assert "execution_mode" in model_columns
            assert "mechanism_category" in mechanism_columns
            assert {"mechanism_category", "result_category"} <= result_columns

            result_foreign_keys = {
                (
                    tuple(item["constrained_columns"]),
                    str(item["referred_table"]),
                    tuple(item["referred_columns"]),
                )
                for item in inspector.get_foreign_keys("verification_results")
            }
            assert (
                (
                    "mechanism_spec_id",
                    "hypothesis_id",
                    "hypothesis_version_id",
                    "mechanism_category",
                ),
                "verification_mechanism_specs",
                (
                    "mechanism_spec_id",
                    "hypothesis_id",
                    "hypothesis_version_id",
                    "mechanism_category",
                ),
            ) in result_foreign_keys
    finally:
        engine.dispose()


@pytest.mark.integration
def test_model_table_has_no_executable_source_or_network_fields(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            columns = {
                str(item["name"])
                for item in inspect(connection).get_columns("executable_model_specs")
            }
    finally:
        engine.dispose()

    assert (
        not {
            "source_text",
            "import_path",
            "entry_point",
            "argv",
            "command",
            "shell_command",
            "url",
            "network_url",
            "executable",
        }
        & columns
    )
    assert {"artifact_hash", "artifact_media_type", "artifact_size_bytes"} <= columns


@pytest.mark.integration
def test_heads_require_exact_immutable_version_and_status(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_primitive_version(
                connection,
                "primitive-1-v1",
                "primitive-1",
                "1.0.0",
                "EXPERIMENTAL",
            )
            connection.execute(
                text(
                    "INSERT INTO primitive_heads "
                    "(primitive_id, primitive_version_id, semantic_version, status) "
                    "VALUES ('primitive-1', 'primitive-1-v1', '1.0.0', 'EXPERIMENTAL')"
                )
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO primitive_heads "
                        "(primitive_id, primitive_version_id, semantic_version, status) "
                        "VALUES ('forged', 'primitive-1-v1', '1.0.1', 'EXPERIMENTAL')"
                    )
                )

            _insert_hypothesis_version(
                connection,
                "hypothesis-1-v1",
                "hypothesis-1",
                1,
                "TRANSFER_TESTING",
            )
            connection.execute(
                text(
                    "INSERT INTO hypothesis_heads "
                    "(hypothesis_id, hypothesis_version_id, version, admission_status) "
                    "VALUES ('hypothesis-1', 'hypothesis-1-v1', 1, 'TRANSFER_TESTING')"
                )
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO hypothesis_heads "
                        "(hypothesis_id, hypothesis_version_id, version, admission_status) "
                        "VALUES ('forged', 'hypothesis-1-v1', 2, 'TRANSFER_TESTING')"
                    )
                )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_runtime_metadata_exactly_matches_0005(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            assert compare_metadata(context, schema.metadata) == []
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


def _insert_primitive_version(
    connection: Connection,
    primitive_version_id: str,
    primitive_id: str,
    semantic_version: str,
    status: str,
) -> None:
    _insert_record(
        connection,
        "primitive_versions",
        "primitive_version_id, primitive_id, semantic_version, status",
        f"'{primitive_version_id}', '{primitive_id}', '{semantic_version}', '{status}'",
    )


def _insert_hypothesis_version(
    connection: Connection,
    hypothesis_version_id: str,
    hypothesis_id: str,
    version: int,
    admission_status: str,
) -> None:
    _insert_record(
        connection,
        "hypothesis_versions",
        "hypothesis_version_id, hypothesis_id, version, admission_status",
        f"'{hypothesis_version_id}', '{hypothesis_id}', {version}, '{admission_status}'",
    )


def _insert_revision(
    connection: Connection,
    revision_id: str,
    hypothesis_id: str,
    prior_id: str,
    prior_version: int,
    resulting_id: str,
    resulting_version: int,
) -> None:
    _insert_record(
        connection,
        "hypothesis_revisions",
        "revision_id, hypothesis_id, prior_hypothesis_version_id, "
        "prior_version, resulting_hypothesis_version_id, resulting_version",
        f"'{revision_id}', '{hypothesis_id}', '{prior_id}', {prior_version}, "
        f"'{resulting_id}', {resulting_version}",
    )
