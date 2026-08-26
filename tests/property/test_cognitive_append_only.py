from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from super_scientist.domain.cognition import CohortRequest, build_cohort
from super_scientist.providers.storage.database import upgrade_database
from super_scientist.providers.storage.repositories import RepositorySet
from tests.integration.storage.test_cognitive_repositories import (
    ALL_18_GOVERNED_REPOSITORY_CASES,
    _governed_examples,
    _persist_accepted_with_audit,
    _proposal_record_and_id,
    _record_policy_hash,
)
from tests.unit.cognition.test_diversity import _profile

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "f" * 64


@pytest.mark.property
def test_all_18_governed_cognitive_rows_reject_update_and_delete(tmp_path) -> None:
    assert len(ALL_18_GOVERNED_REPOSITORY_CASES) == 18
    proposals = _governed_examples()
    assert len(proposals) == 18
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'append-only.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            connection.execute(
                text(
                    "INSERT INTO governance_policies "
                    "(policy_hash, policy_json, created_at) VALUES "
                    "(:policy_f, '{}', :created_at), "
                    "(:policy_a, '{}', :created_at)"
                ),
                {
                    "policy_f": POLICY_HASH,
                    "policy_a": "a" * 64,
                    "created_at": NOW.isoformat(),
                },
            )
            stored: list[tuple[object, object, str]] = []
            for case, proposal in zip(
                ALL_18_GOVERNED_REPOSITORY_CASES,
                proposals,
                strict=True,
            ):
                record, record_id = _proposal_record_and_id(proposal)
                policy_hash = _record_policy_hash(record)
                _persist_accepted_with_audit(repositories, proposal, policy_hash)
                repository = case.repository_type(connection)
                repository.add_from_proposal(
                    proposal,
                    created_at=NOW,
                    transaction_id=proposal.proposal_id,
                    governing_policy_hash=policy_hash,
                )
                stored.append((case, repository, record_id))

            for case, repository, record_id in stored:
                with pytest.raises(IntegrityError, match="append-only"), connection.begin_nested():
                    connection.execute(
                        text(
                            f"UPDATE {case.table_name} SET created_at = :created_at "
                            f"WHERE {case.identifier_column} = :record_id"
                        ),
                        {"created_at": NOW.isoformat(), "record_id": record_id},
                    )
                with pytest.raises(IntegrityError, match="append-only"), connection.begin_nested():
                    connection.execute(
                        text(
                            f"DELETE FROM {case.table_name} "
                            f"WHERE {case.identifier_column} = :record_id"
                        ),
                        {"record_id": record_id},
                    )
                assert repository.get(record_id) is not None
    finally:
        engine.dispose()


@given(st.permutations(("peer-a", "peer-b", "peer-c")))
def test_equivalent_profile_input_permutations_have_stable_cohort_hash_and_order(
    actor_order: tuple[str, ...],
) -> None:
    profiles_by_actor = {
        actor_id: _profile(actor_id, prompt_strategy="direct")
        for actor_id in ("peer-a", "peer-b", "peer-c")
    }
    request = CohortRequest.build(
        request_id="request-permutation",
        task_id="task-permutation",
        required_capabilities=(),
        preferred_capabilities=(),
        min_members=3,
        max_members=3,
        candidate_actor_ids=("peer-a", "peer-b", "peer-c"),
        prohibited_combinations=(),
        governing_policy_hash=POLICY_HASH,
    )
    permuted = tuple(profiles_by_actor[actor_id] for actor_id in actor_order)

    result = build_cohort(request, permuted)
    canonical = build_cohort(
        request,
        tuple(profiles_by_actor[actor_id] for actor_id in sorted(profiles_by_actor)),
    )

    assert result == canonical
    assert result.content_hash == canonical.content_hash
    assert tuple(member.actor_id for member in result.members) == (
        "peer-a",
        "peer-b",
        "peer-c",
    )
