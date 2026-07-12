from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from super_scientist.providers.storage.database import create_database_engine, upgrade_database

APPEND_ONLY_ROWS = {
    "governance_policies": (
        "INSERT INTO governance_policies (policy_hash, policy_json, created_at) "
        "VALUES (:identifier, '{}', :created_at)",
        "policy_hash",
        "a" * 64,
    ),
    "evidence_records": (
        "INSERT INTO evidence_records (evidence_id, content_hash, record_json, created_at) "
        "VALUES (:identifier, :digest, '{}', :created_at)",
        "evidence_id",
        "evidence-1",
    ),
    "claim_versions": (
        "INSERT INTO claim_versions "
        "(claim_version_id, claim_id, version, status, record_json, content_hash, created_at) "
        "VALUES (:identifier, 'claim-1', 1, 'PROPOSED', '{}', :digest, :created_at)",
        "claim_version_id",
        "claim-1:1",
    ),
    "transactions": (
        "INSERT INTO transactions "
        "(proposal_id, idempotency_key, proposal_hash, proposal_json, decision_json, created_at) "
        "VALUES (:identifier, 'key-1', :digest, '{}', '{}', :created_at)",
        "proposal_id",
        "proposal-1",
    ),
    "audit_events": (
        "INSERT INTO audit_events "
        "(sequence, event_id, previous_hash, payload_hash, event_hash, event_json) "
        "VALUES (1, :identifier, :digest, :digest, :digest, '{}')",
        "event_id",
        "audit-1",
    ),
}


@given(
    table_name=st.sampled_from(tuple(APPEND_ONLY_ROWS)),
    operation=st.sampled_from(("UPDATE", "DELETE")),
)
@settings(deadline=None)
def test_history_tables_reject_every_update_and_delete(
    table_name: str,
    operation: str,
) -> None:
    insert_sql, primary_key, identifier = APPEND_ONLY_ROWS[table_name]
    with TemporaryDirectory() as directory:
        database_path = Path(directory) / "kernel.db"
        url = f"sqlite:///{database_path.as_posix()}"
        upgrade_database(url)
        engine = create_database_engine(url)

        with engine.begin() as connection:
            connection.execute(
                text(insert_sql),
                {
                    "identifier": identifier,
                    "digest": "a" * 64,
                    "created_at": "2026-07-11T00:00:00+00:00",
                },
            )
            statement = (
                f"UPDATE {table_name} SET {primary_key} = {primary_key}"
                if operation == "UPDATE"
                else f"DELETE FROM {table_name}"
            )
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(statement))

            count = connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()

        engine.dispose()
        assert count == 1
