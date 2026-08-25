from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from super_scientist.application.cognitive.integrity import expected_cognitive_snapshot
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CapabilityRequirement,
    CohortRequest,
    build_cohort,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    Approval,
    RecordCapabilityProfile,
    RecordCohortPlan,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.cognitive_records import CapabilityProfileRepository
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.repositories import RepositorySet, StoredTransaction
from tests.unit.collaboration.conftest import POLICY_HASH, profile

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _stored(proposal: RecordCapabilityProfile | RecordCohortPlan) -> StoredTransaction:
    return StoredTransaction(
        proposal=proposal,
        proposal_hash="a" * 64,
        decision=TransactionDecision(proposal_id=proposal.proposal_id, accepted=True),
        intent_fingerprint=None,
        created_at=NOW,
    )


def _actor() -> ActorIdentity:
    return ActorIdentity(actor_id="service", kind=ActorKind.SERVICE, created_at=NOW)


def _governed_policy() -> PolicySnapshot:
    policy = GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset(),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.RESEARCH_PROCESS,
                persistence=PersistenceScope.RUN_LOCAL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=False,
                rollback_required=False,
            ),
        ),
    )
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _profile_for_policy(policy: PolicySnapshot) -> CapabilityProfile:
    retained = profile("peer-a")
    values = retained.model_dump(mode="python", exclude={"content_hash"})
    values["governing_policy_hash"] = policy.policy_hash
    return type(retained).build(**values)


def test_expected_cognitive_snapshot_recomputes_cohort_plan() -> None:
    retained = profile("peer-a")
    profile_proposal = RecordCapabilityProfile(
        proposal_id="profile-proposal",
        idempotency_key="profile-proposal",
        proposer=_actor(),
        profile=retained,
    )
    request = CohortRequest.build(
        request_id="request",
        task_id="research",
        min_members=1,
        max_members=1,
        candidate_actor_ids=("peer-a",),
        required_capabilities=(
            CapabilityRequirement(
                requirement_id="analysis",
                capability_id="analysis",
                task_family_id="research",
                evidence_snapshot_hash="e" * 64,
            ),
        ),
        preferred_capabilities=(),
        prohibited_combinations=(),
        governing_policy_hash=POLICY_HASH,
    )
    canonical = build_cohort(request, (retained,))
    forged = canonical.model_copy(update={"minimum_size_met": not canonical.minimum_size_met})
    cohort_proposal = RecordCohortPlan.model_construct(
        proposal_id="cohort-proposal",
        idempotency_key="cohort-proposal",
        proposer=_actor(),
        approval=None,
        proposal_type="record_cohort_plan",
        request=request,
        profile_receipts=(
            CapabilityProfileReceiptRef(
                proposal_id="profile-proposal",
                proposal_hash="a" * 64,
                audit_event_id="audit-profile",
                audit_event_hash="b" * 64,
            ),
        ),
        plan=forged,
    )

    with pytest.raises(ValueError, match="cohort plan"):
        expected_cognitive_snapshot((_stored(profile_proposal), _stored(cohort_proposal)))


def test_cognitive_row_tampering_fails_workspace_reconstruction(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'workspace.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    policy = _governed_policy()
    retained = _profile_for_policy(policy)
    proposal = RecordCapabilityProfile(
        proposal_id="profile-proposal",
        idempotency_key="profile-proposal",
        proposer=_actor(),
        approval=Approval(
            approver=ActorIdentity(
                actor_id="reviewer",
                kind=ActorKind.HUMAN,
                created_at=NOW,
            ),
            approved_at=NOW,
        ),
        profile=retained,
    )
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            repositories.policies.add_and_activate(policy, NOW)
            repositories.transactions.add(proposal, decision, NOW)
            repositories.audit.add(
                append_event(
                    None,
                    "transaction_decision",
                    {
                        "proposal": proposal.model_dump(mode="json"),
                        "decision": decision.model_dump(mode="json"),
                        "policy_hash": policy.policy_hash,
                        "stored_policy_hash": policy.policy_hash,
                        "transaction_persisted": True,
                    },
                    NOW,
                )
            )
            CapabilityProfileRepository(connection).add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=policy.policy_hash,
            )
        with engine.connect() as connection:
            assert verify_workspace(RepositorySet(connection), artifacts).valid is True
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("DROP TRIGGER capability_profiles_no_update")
            connection.execute(
                text(
                    "UPDATE capability_profiles "
                    "SET record_json = json_set(record_json, '$.profile_id', 'forged-profile')"
                )
            )
        with engine.connect() as connection:
            result = verify_workspace(RepositorySet(connection), artifacts)
            assert result.valid is False
            assert result.reason is not None and "workspace integrity error" in result.reason
    finally:
        engine.dispose()
