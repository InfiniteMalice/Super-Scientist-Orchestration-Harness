from pathlib import Path

import pytest
from sqlalchemy import text

from super_scientist.providers.storage.database import create_database_engine, upgrade_database

KERNEL_TABLES = {
    "governance_policies",
    "governance_state",
    "evidence_records",
    "claim_versions",
    "claim_heads",
    "transactions",
    "audit_events",
}


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_initial_migration_creates_kernel_tables(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "kernel.db")

    upgrade_database(url)
    engine = create_database_engine(url)
    with engine.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()

    assert names >= KERNEL_TABLES
    assert revision == "0007_governed_cognitive_procedures"


def test_upgrade_database_is_idempotent(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "kernel.db")

    upgrade_database(url)
    upgrade_database(url)

    engine = create_database_engine(url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()

    assert revision == "0007_governed_cognitive_procedures"


def test_evidence_rows_cannot_be_updated(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "kernel.db")
    upgrade_database(url)
    engine = create_database_engine(url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evidence_records "
                "(evidence_id, content_hash, record_json, created_at) "
                "VALUES ('ev-1', :digest, '{}', :created_at)"
            ),
            {"digest": "a" * 64, "created_at": "2026-07-11T00:00:00+00:00"},
        )
        with pytest.raises(Exception, match="append-only table"):
            connection.execute(
                text("UPDATE evidence_records SET record_json = :record_json"),
                {"record_json": '{"changed":true}'},
            )
    engine.dispose()


def test_projection_rows_remain_mutable(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "kernel.db")
    upgrade_database(url)
    engine = create_database_engine(url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO governance_state (singleton_id, active_policy_hash) "
                "VALUES (1, :digest)"
            ),
            {"digest": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO claim_heads "
                "(claim_id, claim_version_id, version, status) "
                "VALUES ('claim-1', 'claim-1:1', 1, 'PROPOSED')"
            )
        )
        connection.execute(
            text("UPDATE governance_state SET active_policy_hash = :digest"),
            {"digest": "b" * 64},
        )
        connection.execute(
            text(
                "UPDATE claim_heads SET claim_version_id = 'claim-1:2', "
                "version = 2, status = 'EVIDENCE_LINKED'"
            )
        )
        connection.execute(text("DELETE FROM governance_state"))
        connection.execute(text("DELETE FROM claim_heads"))

        assert connection.execute(text("SELECT COUNT(*) FROM governance_state")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM claim_heads")).scalar_one() == 0
    engine.dispose()


def test_governance_state_enforces_singleton_identifier(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "kernel.db")
    upgrade_database(url)
    engine = create_database_engine(url)

    with engine.begin() as connection, pytest.raises(Exception, match="CHECK constraint failed"):
        connection.execute(
            text(
                "INSERT INTO governance_state (singleton_id, active_policy_hash) "
                "VALUES (2, :digest)"
            ),
            {"digest": "a" * 64},
        )
    engine.dispose()
