from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from pydantic import ValidationError

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.cognition import (
    CapabilityDisposition,
    CapabilityEvidenceStatus,
    DiversityAssessment,
    DiversityAxisStatus,
    assess_capability,
    assess_diversity,
)
from super_scientist.domain.collaboration import (
    CollaborationSession,
    CollaborationTerminationReason,
    PeerContribution,
    TopologyEvent,
    TopologyOperation,
    apply_topology_event,
    evaluate_termination,
    initial_collaboration_state,
)
from super_scientist.domain.identity import are_independent
from super_scientist.kernel.transactions.models import (
    ProposeClaim,
    RejectionCode,
    TransitionClaim,
)
from tests.integration.application.test_kernel_service import NOW, KernelFixture
from tests.integration.application.test_transaction_coordinator import Runtime
from tests.unit.cognition.test_diversity import _cohort
from tests.unit.cognition.test_diversity import _profile as diversity_profile
from tests.unit.cognition.test_grounding import _assertion, _profile, _requirement
from tests.unit.collaboration.conftest import artifact
from tests.unit.collaboration.test_termination import _advance, _toggle_declared_edge

pytest_plugins = (
    "tests.integration.application.test_kernel_service",
    "tests.integration.application.test_transaction_coordinator",
    "tests.unit.collaboration.conftest",
)


def _authority_heads(runtime: Runtime) -> tuple[object, ...]:
    with runtime.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        return (
            repositories.claims.list_heads(),
            repositories.policies.list_all(),
            repositories.harness_integrity_snapshot().heads,
            repositories.progress_integrity_snapshot().heads,
        )


def _kernel_authority_heads(kernel: KernelFixture) -> tuple[object, ...]:
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        return (
            repositories.claims.list_heads(),
            repositories.policies.list_all(),
            repositories.harness_integrity_snapshot().heads,
            repositories.progress_integrity_snapshot().heads,
        )


def test_peer_majority_is_evidence_only_and_cannot_transition_a_claim(
    kernel: KernelFixture,
) -> None:
    contributions = tuple(
        PeerContribution.build(
            contribution_id=f"contribution-{index}",
            session_id="session-majority",
            request_id=f"request-{index}",
            peer_id=f"peer-{index}",
            parent_contribution_ids=(),
            contribution_kind="review",
            rationale_summary="The peers agree on the proposed transition.",
            candidate_content='{"finding":"supported"}',
            artifact_refs=(artifact(),),
            tool_ids=(),
        )
        for index in range(3)
    )
    assert {item.authority for item in contributions} == {"EVIDENCE_ONLY"}

    current = AtomicClaim(
        claim_id="claim-peer-majority",
        version=1,
        proposition="The fixture supports a governed claim.",
        scope="fixture",
        population_or_system="fixture system",
        epistemic_modality="proposed",
        status=ClaimStatus.PROPOSED,
        created_at=NOW,
        created_by=kernel.actor.actor_id,
    )
    assert kernel.service.submit(
        ProposeClaim(
            proposal_id="proposal-peer-majority-claim",
            idempotency_key="key-peer-majority-claim",
            proposer=kernel.actor,
            claim=current,
        )
    ).accepted
    before = _kernel_authority_heads(kernel)
    forged_transition = TransitionClaim(
        proposal_id="proposal-peer-majority-transition",
        idempotency_key="key-peer-majority-transition",
        proposer=kernel.actor,
        next_claim=AtomicClaim(
            claim_id=current.claim_id,
            version=2,
            proposition=current.proposition,
            scope=current.scope,
            population_or_system=current.population_or_system,
            epistemic_modality=current.epistemic_modality,
            status=ClaimStatus.EVIDENCE_LINKED,
            evidence_links=(
                EvidenceLink(
                    evidence_id="peer-majority-is-not-evidence",
                    supporting_span="Three peers agreed.",
                ),
            ),
            parent_version_id=f"{current.claim_id}:1",
            created_at=NOW + timedelta(seconds=1),
            created_by=kernel.actor.actor_id,
        ),
    )

    decision = kernel.service.submit(forged_transition)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE
    assert _kernel_authority_heads(kernel) == before


def test_self_report_and_capability_spoof_cannot_promote_capability(
    runtime: Runtime,
) -> None:
    before = _authority_heads(runtime)
    profile = _profile(_assertion(CapabilityEvidenceStatus.SELF_REPORTED))
    assessment = assess_capability(profile, _requirement())

    assert assessment.disposition is CapabilityDisposition.UNKNOWN
    assert assessment.evidence_status is CapabilityEvidenceStatus.SELF_REPORTED
    forged = profile.model_copy(
        update={"assertions": (_assertion(CapabilityEvidenceStatus.VERIFIED),)}
    )
    with pytest.raises(ValueError, match="capability profile"):
        assess_capability(forged, _requirement())
    assert _authority_heads(runtime) == before


def test_correlated_same_model_consensus_is_diverse_but_not_independent(
    runtime: Runtime,
) -> None:
    before = _authority_heads(runtime)
    left = diversity_profile("peer-a", prompt_strategy="critique-first")
    right = diversity_profile("peer-b", prompt_strategy="direct")

    assessment = assess_diversity(_cohort(left, right), (left, right), ())

    assert assessment.axes.prompt_strategy is DiversityAxisStatus.DIFFERENT
    assert assessment.axes.model_family is DiversityAxisStatus.SAME
    assert are_independent(left.actor, right.actor) is False
    assert "is_independent" not in DiversityAssessment.model_fields
    assert "authority" not in DiversityAssessment.model_fields
    assert _authority_heads(runtime) == before


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
def test_routing_storms_terminate_without_authority_changes(
    runtime: Runtime,
    session_factory: Callable[..., CollaborationSession],
    session_changes: dict[str, int],
    steps: int,
    expected_reason: CollaborationTerminationReason,
) -> None:
    before = _authority_heads(runtime)
    session = session_factory("peer-a", "peer-b", **session_changes)
    state = initial_collaboration_state(session)
    for sequence in range(1, steps + 1):
        state = _advance(session, state, sequence)

    termination = evaluate_termination(state)

    assert termination.reason is expected_reason
    assert _authority_heads(runtime) == before


def test_routing_loop_is_bounded_and_cannot_change_authority(
    runtime: Runtime,
    session_factory: Callable[..., CollaborationSession],
) -> None:
    before = _authority_heads(runtime)
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
    assert _authority_heads(runtime) == before


def test_topology_manipulation_rolls_back_the_collaboration_state(
    runtime: Runtime,
    session_factory: Callable[..., CollaborationSession],
) -> None:
    before_authority = _authority_heads(runtime)
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
    assert _authority_heads(runtime) == before_authority


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
