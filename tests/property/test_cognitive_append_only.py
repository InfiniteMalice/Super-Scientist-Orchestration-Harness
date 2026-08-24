from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from super_scientist.kernel.transactions.models import RecordCapabilityProfile
from super_scientist.providers.storage.cognitive_records import CapabilityProfileRepository
from super_scientist.providers.storage.database import upgrade_database
from tests.unit.collaboration.conftest import actor, profile

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.mark.property
def test_governed_cognitive_rows_reject_update_and_delete(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'append-only.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            record = profile("peer-a")
            proposal = RecordCapabilityProfile(
                proposal_id="proposal-profile-a",
                idempotency_key="proposal-profile-a",
                proposer=actor("coordinator"),
                profile=record,
            )
            CapabilityProfileRepository(connection).add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash="f" * 64,
            )

            with pytest.raises(IntegrityError, match="append-only"):
                connection.execute(
                    text(
                        "UPDATE capability_profiles SET created_at = :created_at "
                        "WHERE profile_id = :profile_id"
                    ),
                    {"created_at": NOW.isoformat(), "profile_id": record.profile_id},
                )
            connection.rollback()
    finally:
        engine.dispose()
