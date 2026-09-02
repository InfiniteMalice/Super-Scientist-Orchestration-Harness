from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.cognition import (
    CapabilityDisposition,
    CapabilityEvidenceStatus,
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CapabilityRequirement,
    CohortPlanReceiptRef,
    CohortRequest,
    DiversityAssessment,
    DiversityAxisStatus,
    assess_capability,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.collaboration import (
    CollaborationSession,
    CollaborationTerminationReason,
    PeerContribution,
    TopologyEvent,
    TopologyOperation,
    advance_collaboration,
    apply_topology_event,
    evaluate_termination,
    initial_collaboration_state,
    next_peer,
)
from super_scientist.domain.identity import are_independent
from super_scientist.kernel.transactions.models import (
    AppendPeerContribution,
    AppendPeerRequest,
    Proposal,
    ProposeClaim,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordCollaborationSession,
    RecordDiversityAssessment,
    RejectionCode,
    TransitionClaim,
)
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from tests.integration.application.test_cognitive_workspace_exchange import (
    _accepted_binding,
    _approval,
    _governed_policy,
    _service_actor,
)
from tests.integration.application.test_kernel_service import NOW
from tests.integration.application.test_workspace_exchange import ExchangeRuntime, _runtime
from tests.unit.cognition.test_diversity import _cohort
from tests.unit.cognition.test_diversity import _profile as diversity_profile
from tests.unit.cognition.test_grounding import _assertion, _profile, _requirement
from tests.unit.collaboration.conftest import profile as collaboration_profile
from tests.unit.collaboration.conftest import session_factory as session_factory_fixture
from tests.unit.collaboration.conftest import unit_usage
from tests.unit.collaboration.test_engine import _contribution, _request
from tests.unit.collaboration.test_termination import _advance, _toggle_declared_edge

pytest_plugins = ("tests.unit.collaboration.conftest",)


def _authority_heads(uow_factory: Callable[[], DatabaseUnitOfWork]) -> tuple[object, ...]:
    with uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        return (
            repositories.claims.list_heads(),
            repositories.policies.list_all(),
            repositories.harness_integrity_snapshot().heads,
            repositories.progress_integrity_snapshot().heads,
        )


def _submit_accepted(runtime: ExchangeRuntime, proposal: Proposal) -> None:
    decision = runtime.coordinator.submit(proposal)
    assert decision.accepted, decision


def _record_unanimous_peer_contributions(
    runtime: ExchangeRuntime,
) -> tuple[PeerContribution, ...]:
    policy = _governed_policy()
    proposer = _service_actor()
    approval = _approval(runtime)
    profiles = []
    receipts = []
    for actor_id in ("peer-a", "peer-b", "peer-c"):
        base = collaboration_profile(actor_id)
        values = base.model_dump(mode="python", exclude={"content_hash"})
        values["governing_policy_hash"] = policy.policy_hash
        retained = CapabilityProfile.build(**values)
        proposal = RecordCapabilityProfile(
            proposal_id=f"majority-profile-{actor_id}",
            idempotency_key=f"majority-profile-{actor_id}",
            proposer=proposer,
            approval=approval,
            profile=retained,
        )
        _submit_accepted(runtime, proposal)
        proposal_hash, audit_id, audit_hash = _accepted_binding(runtime, proposal.proposal_id)
        profiles.append(retained)
        receipts.append(
            CapabilityProfileReceiptRef(
                proposal_id=proposal.proposal_id,
                proposal_hash=proposal_hash,
                audit_event_id=audit_id,
                audit_event_hash=audit_hash,
            )
        )
    request = CohortRequest.build(
        request_id="majority-cohort-request",
        task_id="majority-task",
        required_capabilities=(
            CapabilityRequirement(
                requirement_id="majority-analysis-requirement",
                capability_id="analysis",
                task_family_id="research",
                evidence_snapshot_hash="e" * 64,
            ),
        ),
        preferred_capabilities=(),
        min_members=3,
        max_members=3,
        candidate_actor_ids=("peer-a", "peer-b", "peer-c"),
        prohibited_combinations=(),
        governing_policy_hash=policy.policy_hash,
    )
    cohort = build_cohort(request, tuple(profiles))
    cohort_proposal = RecordCohortPlan(
        proposal_id="majority-cohort",
        idempotency_key="majority-cohort",
        proposer=proposer,
        approval=approval,
        request=request,
        profile_receipts=tuple(receipts),
        plan=cohort,
    )
    _submit_accepted(runtime, cohort_proposal)
    cohort_hash, cohort_audit_id, cohort_audit_hash = _accepted_binding(
        runtime, cohort_proposal.proposal_id
    )
    _submit_accepted(
        runtime,
        RecordDiversityAssessment(
            proposal_id="majority-diversity",
            idempotency_key="majority-diversity",
            proposer=proposer,
            approval=approval,
            cohort_plan_receipt=CohortPlanReceiptRef(
                proposal_id=cohort_proposal.proposal_id,
                proposal_hash=cohort_hash,
                audit_event_id=cohort_audit_id,
                audit_event_hash=cohort_audit_hash,
            ),
            profile_receipts=tuple(receipts),
            error_correlations=(),
            assessment=assess_diversity(cohort, tuple(profiles), ()),
        ),
    )
    collaboration_artifact = runtime.artifact_store.put(
        b"recorded peer consensus is evidence-only",
        "application/json",
    )
    base_session = session_factory_fixture.__wrapped__()(
        "peer-a",
        "peer-b",
        "peer-c",
        completion_count=3,
    )
    session_values = base_session.model_dump(mode="python", exclude={"content_hash"})
    session_values.update(
        task_id=request.task_id,
        cohort_plan=cohort,
        peers=tuple(profile.actor for profile in profiles),
        allowed_artifacts=(collaboration_artifact,),
        governing_policy_hash=policy.policy_hash,
    )
    session = CollaborationSession.build(**session_values)
    _submit_accepted(
        runtime,
        RecordCollaborationSession(
            proposal_id="majority-session",
            idempotency_key="majority-session",
            proposer=proposer,
            approval=approval,
            session=session,
        ),
    )
    state = initial_collaboration_state(session)
    contributions = []
    for sequence in range(1, 4):
        recipient = next_peer(session, state)
        assert recipient is not None
        parent = None if sequence == 1 else f"contribution-{sequence - 1}"
        request_record = _request(
            session,
            recipient,
            sequence=sequence,
            sender_id=None if sequence == 1 else state.contributions[-1].peer_id,
            parent_contribution_id=parent,
            remaining_budget=session.remaining_resources(state.usage_history),
        )
        request_values = request_record.model_dump(mode="python", exclude={"content_hash"})
        request_values["artifact_refs"] = (collaboration_artifact,)
        request_record = type(request_record).build(**request_values)
        contribution = _contribution(
            session,
            recipient,
            sequence=sequence,
            parent_contribution_ids=() if parent is None else (parent,),
        )
        contribution_values = contribution.model_dump(mode="python", exclude={"content_hash"})
        contribution_values.update(
            rationale_summary="Unanimous peer support, retained as evidence only.",
            artifact_refs=(collaboration_artifact,),
        )
        contribution = type(contribution).build(**contribution_values)
        _submit_accepted(
            runtime,
            AppendPeerRequest(
                proposal_id=f"majority-request-{sequence}",
                idempotency_key=f"majority-request-{sequence}",
                proposer=proposer,
                approval=approval,
                request=request_record,
            ),
        )
        _submit_accepted(
            runtime,
            AppendPeerContribution(
                proposal_id=f"majority-contribution-{sequence}",
                idempotency_key=f"majority-contribution-{sequence}",
                proposer=proposer,
                approval=approval,
                contribution=contribution,
                usage=unit_usage(),
            ),
        )
        state = advance_collaboration(
            session,
            state,
            request_record,
            contribution,
            unit_usage(),
        )
        contributions.append(contribution)
    return tuple(contributions)


def test_recorded_peer_majority_is_evidence_only_and_cannot_transition_a_claim(
    tmp_path: Path,
) -> None:
    policy = _governed_policy()
    runtime = _runtime(tmp_path, "recorded-majority", policy_snapshot=policy)
    try:
        contributions = _record_unanimous_peer_contributions(runtime)
        with runtime.uow_factory() as unit_of_work:
            retained = unit_of_work.repositories().cognitive_integrity_snapshot()
            assert retained.peer_contributions == contributions
        assert {item.authority for item in contributions} == {"EVIDENCE_ONLY"}
        initial_counts = _transaction_and_audit_counts(runtime)
        assert initial_counts == (12, 12)
        claim_actor = runtime.actor
        current = AtomicClaim(
            claim_id="claim-peer-majority",
            version=1,
            proposition="The fixture supports a governed claim.",
            scope="fixture",
            population_or_system="fixture system",
            epistemic_modality="proposed",
            status=ClaimStatus.PROPOSED,
            created_at=NOW,
            created_by=claim_actor.actor_id,
        )
        assert runtime.coordinator.submit(
            ProposeClaim(
                proposal_id="proposal-peer-majority-claim",
                idempotency_key="key-peer-majority-claim",
                proposer=claim_actor,
                claim=current,
            )
        ).accepted
        before = _authority_heads(runtime.uow_factory)
        before_counts = _transaction_and_audit_counts(runtime)
        forged_transition = TransitionClaim(
            proposal_id="proposal-peer-majority-transition",
            idempotency_key="key-peer-majority-transition",
            proposer=claim_actor,
            next_claim=AtomicClaim(
                claim_id=current.claim_id,
                version=2,
                proposition=current.proposition,
                scope=current.scope,
                population_or_system=current.population_or_system,
                epistemic_modality=current.epistemic_modality,
                status=ClaimStatus.EVIDENCE_LINKED,
                evidence_links=tuple(
                    EvidenceLink(
                        evidence_id=contribution.contribution_id,
                        supporting_span=contribution.rationale_summary,
                    )
                    for contribution in contributions
                ),
                parent_version_id=f"{current.claim_id}:1",
                created_at=NOW + timedelta(seconds=1),
                created_by=claim_actor.actor_id,
            ),
        )

        decision = runtime.coordinator.submit(forged_transition)

        assert not decision.accepted
        assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE
        assert _authority_heads(runtime.uow_factory) == before
        assert _transaction_and_audit_counts(runtime) == (
            before_counts[0] + 1,
            before_counts[1] + 1,
        )
        with runtime.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            assert repositories.claims.history(current.claim_id) == (current,)
            assert repositories.cognitive_integrity_snapshot().peer_contributions == contributions
    finally:
        runtime.engine.dispose()


def _transaction_and_audit_counts(runtime: ExchangeRuntime) -> tuple[int, int]:
    with runtime.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        return len(repositories.transactions.list_all()), len(repositories.audit.list_all())


def test_canonical_self_report_cannot_be_replaced_by_verified_capability_spoof(
    tmp_path: Path,
) -> None:
    policy = _governed_policy()
    runtime = _runtime(tmp_path, "capability-spoof", policy_snapshot=policy)
    try:
        proposer = _service_actor()
        approval = _approval(runtime)
        base = _profile(_assertion(CapabilityEvidenceStatus.SELF_REPORTED))
        values = base.model_dump(mode="python", exclude={"content_hash"})
        values["governing_policy_hash"] = policy.policy_hash
        self_report = CapabilityProfile.build(**values)
        original = RecordCapabilityProfile(
            proposal_id="record-canonical-self-report",
            idempotency_key="record-canonical-self-report",
            proposer=proposer,
            approval=approval,
            profile=self_report,
        )
        assert runtime.coordinator.submit(original).accepted
        before = _authority_heads(runtime.uow_factory)
        before_counts = _transaction_and_audit_counts(runtime)
        spoof_values = self_report.model_dump(mode="python", exclude={"content_hash"})
        spoof_values["assertions"] = (_assertion(CapabilityEvidenceStatus.VERIFIED),)
        spoof = CapabilityProfile.build(**spoof_values)

        decision = runtime.coordinator.submit(
            RecordCapabilityProfile(
                proposal_id="record-forged-verified-profile",
                idempotency_key="record-forged-verified-profile",
                proposer=proposer,
                approval=approval,
                profile=spoof,
            )
        )

        assert decision.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS
        assert _authority_heads(runtime.uow_factory) == before
        assert _transaction_and_audit_counts(runtime) == (
            before_counts[0] + 1,
            before_counts[1] + 1,
        )
        with runtime.uow_factory() as unit_of_work:
            retained = (
                unit_of_work.repositories().cognitive_integrity_snapshot().capability_profiles
            )
        assert retained == (self_report,)
        assert (
            assess_capability(retained[0], _requirement()).disposition
            is CapabilityDisposition.UNKNOWN
        )
    finally:
        runtime.engine.dispose()


def test_correlated_same_model_consensus_is_diverse_but_not_independent() -> None:
    left = diversity_profile("peer-a", prompt_strategy="critique-first")
    right = diversity_profile("peer-b", prompt_strategy="direct")

    assessment = assess_diversity(_cohort(left, right), (left, right), ())

    assert assessment.axes.prompt_strategy is DiversityAxisStatus.DIFFERENT
    assert assessment.axes.model_family is DiversityAxisStatus.SAME
    assert are_independent(left.actor, right.actor) is False
    assert "is_independent" not in DiversityAssessment.model_fields
    assert "authority" not in DiversityAssessment.model_fields


@pytest.mark.parametrize(
    ("session_changes", "steps", "expected_reason"),
    (
        (
            {"max_contributions": 1, "completion_count": 8},
            1,
            CollaborationTerminationReason.MAX_CONTRIBUTIONS_REACHED,
        ),
        (
            {"max_hops": 1, "completion_count": 8},
            1,
            CollaborationTerminationReason.MAX_HOPS_REACHED,
        ),
    ),
)
def test_routing_storms_terminate_at_declared_bounds(
    session_factory: Callable[..., CollaborationSession],
    session_changes: dict[str, int],
    steps: int,
    expected_reason: CollaborationTerminationReason,
) -> None:
    session = session_factory("peer-a", "peer-b", **session_changes)
    state = initial_collaboration_state(session)
    for sequence in range(1, steps + 1):
        state = _advance(session, state, sequence)

    termination = evaluate_termination(state)

    assert termination.reason is expected_reason


def test_routing_loop_is_bounded(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory(
        "peer-a",
        "peer-b",
        max_topology_changes=8,
        max_topology_churn=8,
        max_state_repetitions=1,
        completion_count=8,
    )
    state = initial_collaboration_state(session)
    for index, edge_name in enumerate(("e0", "e0", "e0", "e0"), start=1):
        state = _toggle_declared_edge(session, state, edge_name, f"loop-event-{index}")
        termination = evaluate_termination(state)
        if termination.reason is not None:
            break

    assert termination.reason is CollaborationTerminationReason.REPEATED_STATE_LOOP


def test_topology_manipulation_rolls_back_the_collaboration_state(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b")
    state = initial_collaboration_state(session)
    forged = TopologyEvent.build(
        event_id="forged-topology-event",
        session_id=session.session_id,
        sequence=1,
        before_topology_hash="0" * 64,
        operation=TopologyOperation.DISABLE_EDGE,
        peer_id=None,
        edge=("peer-a", "peer-b"),
        reason_code="ATTACKER_DIRECTED_REWIRE",
        after_topology_hash=state.topology.content_hash,
    )

    with pytest.raises(ValueError, match="before topology hash"):
        apply_topology_event(session, state, forged)

    assert state == initial_collaboration_state(session)


def test_peer_contribution_schema_cannot_embed_governance_authority() -> None:
    with pytest.raises(ValidationError, match="authority"):
        PeerContribution.model_validate(
            {
                "contribution_id": "contribution-forged",
                "session_id": "session-forged",
                "request_id": "request-forged",
                "peer_id": "peer-forged",
                "parent_contribution_ids": (),
                "contribution_kind": "review",
                "rationale_summary": "Attempt to claim authority.",
                "candidate_content": "{}",
                "artifact_refs": (),
                "tool_ids": (),
                "authority": "GOVERNANCE_WRITE",
                "content_hash": "f" * 64,
            },
            strict=True,
        )
