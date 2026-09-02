from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from super_scientist.providers.storage import schema

REVISION = "0006_handbook_and_harness_evaluation"
AUTHORITATIVE_0006_TABLES = {
    "behavior_rule_link_versions",
    "handbook_verification_records",
    "harness_campaigns",
    "harness_partition_manifests",
    "harness_budgets",
    "harness_observations",
    "harness_metrics",
    "harness_confounds",
    "harness_decisions",
}
PROJECTION_0006_TABLES = {"harness_campaign_heads"}


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{(tmp_path / 'migration-0006.db').as_posix()}"


@pytest.mark.integration
def test_clean_upgrade_creates_only_ordinary_handbook_and_harness_storage(
    database_url: str,
) -> None:
    _upgrade_to(database_url, REVISION)

    names = _table_names(database_url)
    assert names >= AUTHORITATIVE_0006_TABLES | PROJECTION_0006_TABLES
    assert "protected_expected_outputs" not in names
    assert _revision(database_url) == REVISION

    forbidden_columns = {
        "expected_output",
        "answer_bytes",
        "answer_reference",
        "artifact_path",
        "protected_path",
        "protected_url",
    }
    with create_engine(database_url).connect() as connection:
        inspector = inspect(connection)
        for table_name in AUTHORITATIVE_0006_TABLES:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert {"record_json", "content_hash", "created_at"} <= columns
            assert not (columns & forbidden_columns)


@pytest.mark.integration
def test_0005_upgrade_and_0006_downgrade_preserve_prior_history(database_url: str) -> None:
    _upgrade_to(database_url, "0005_hypotheses_and_representations")
    with create_engine(database_url).begin() as connection:
        connection.execute(
            text(
                "INSERT INTO primitive_versions "
                "(primitive_version_id, primitive_id, semantic_version, status, "
                "record_json, content_hash, created_at) VALUES "
                "('primitive-v1', 'primitive', '1.0.0', 'PROPOSED', '{}', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "'2026-07-20T00:00:00+00:00')"
            )
        )

    _upgrade_to(database_url, REVISION)
    with create_engine(database_url).connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM primitive_versions")).scalar_one() == 1

    _downgrade_to(database_url, "0005_hypotheses_and_representations")
    assert not ((AUTHORITATIVE_0006_TABLES | PROJECTION_0006_TABLES) & _table_names(database_url))
    assert _revision(database_url) == "0005_hypotheses_and_representations"
    with create_engine(database_url).connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM primitive_versions")).scalar_one() == 1


@pytest.mark.integration
def test_genuine_0001_database_upgrades_to_0006_without_changing_legacy_rows(
    database_url: str,
) -> None:
    _upgrade_to(database_url, "0001_epistemic_kernel")
    legacy = {
        "evidence_id": "legacy-evidence",
        "content_hash": "a" * 64,
        "record_json": "{}",
        "created_at": "2026-07-20T00:00:00+00:00",
    }
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO evidence_records "
                    "(evidence_id, content_hash, record_json, created_at) "
                    "VALUES (:evidence_id, :content_hash, :record_json, :created_at)"
                ),
                legacy,
            )
        _upgrade_to(database_url, REVISION)
        with engine.connect() as connection:
            assert (
                dict(
                    connection.execute(
                        text(
                            "SELECT evidence_id, content_hash, record_json, created_at "
                            "FROM evidence_records WHERE evidence_id = :evidence_id"
                        ),
                        {"evidence_id": legacy["evidence_id"]},
                    )
                    .mappings()
                    .one()
                )
                == legacy
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0006_foreign_keys_bind_rule_links_and_campaign_children(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
            with connection.begin():
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "INSERT INTO behavior_rule_link_versions "
                            "(link_version_id, behavior_id, version, rule_version_id, record_json, "
                            "content_hash, created_at) VALUES "
                            "('link-v1', 'behavior', 1, 'missing-rule', '{}', "
                            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                            "'2026-07-20T00:00:00+00:00')"
                        )
                    )
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "INSERT INTO harness_budgets "
                            "(budget_id, campaign_id, variant, record_json, "
                            "content_hash, created_at) "
                            "VALUES ('budget', 'missing-campaign', 'EVOLVED_HARNESS', '{}', "
                            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                            "'2026-07-20T00:00:00+00:00')"
                        )
                    )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_runtime_metadata_exactly_contains_0006_tables(database_url: str) -> None:
    _upgrade_to(database_url, REVISION)
    runtime_names = {
        table.name
        for table in schema.metadata.sorted_tables
        if table.name in AUTHORITATIVE_0006_TABLES | PROJECTION_0006_TABLES
    }
    assert runtime_names == AUTHORITATIVE_0006_TABLES | PROJECTION_0006_TABLES
    engine = create_engine(database_url)
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
