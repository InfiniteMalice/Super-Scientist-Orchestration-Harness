from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError

import super_scientist.kernel.admission.engine as admission_engine
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.claims.transitions import ALLOWED
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, EvidenceSpan
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.evaluation.claim_drift.models import CheckOutcome, CheckResult
from super_scientist.kernel.admission.engine import AdmissionContext, AdmissionEngine
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    Proposal,
    ProposeClaim,
    RejectionCode,
    RejectionReason,
    TransactionDecision,
    TransitionClaim,
)


def _actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.HUMAN,
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
    )


def _policy(
    required_claim_checks: tuple[str, ...] = ("source_exists", "evidence_span_exists"),
) -> PolicySnapshot:
    return PolicySnapshot(
        policy_hash="a" * 64,
        policy=GovernancePolicy(required_claim_checks=required_claim_checks),
    )


def _context(
    *,
    evidence_by_id: dict[str, EvidenceRecord] | None = None,
    claim_by_id: dict[str, AtomicClaim] | None = None,
    prior_decisions: dict[str, TransactionDecision] | None = None,
    required_claim_checks: tuple[str, ...] = ("source_exists", "evidence_span_exists"),
) -> AdmissionContext:
    return AdmissionContext(
        active_policy=_policy(required_claim_checks),
        evidence_by_id=evidence_by_id or {},
        claim_by_id=claim_by_id or {},
        prior_decision_by_idempotency_key=prior_decisions or {},
    )


def _claim(
    actor_id: str = "proposer",
    *,
    status: ClaimStatus = ClaimStatus.PROPOSED,
    evidence_links: tuple[EvidenceLink, ...] = (),
) -> AtomicClaim:
    return AtomicClaim(
        claim_id="claim-1",
        version=1,
        proposition="Fixture proposition.",
        scope="fixture",
        population_or_system="fixture system",
        epistemic_modality="observed",
        status=status,
        evidence_links=evidence_links,
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
        created_by=actor_id,
    )


def _next_claim(
    current: AtomicClaim,
    target_status: ClaimStatus,
    *,
    actor_id: str = "proposer",
    evidence_links: tuple[EvidenceLink, ...] | None = None,
    **updates: object,
) -> AtomicClaim:
    values = current.model_dump(mode="python")
    values.update(
        {
            "version": current.version + 1,
            "status": target_status,
            "evidence_links": (
                current.evidence_links if evidence_links is None else evidence_links
            ),
            "parent_version_id": f"{current.claim_id}:{current.version}",
            "created_at": current.created_at + timedelta(seconds=1),
            "created_by": actor_id,
        }
    )
    values.update(updates)
    return AtomicClaim.model_validate(values)


def _evidence(extracted_text: str = "supporting fixture span") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="evidence-1",
        evidence_type="document",
        source_locator="fixture://one",
        retrieved_at=datetime(2026, 7, 12, tzinfo=UTC),
        artifact=ArtifactRef(
            sha256="b" * 64,
            size_bytes=1,
            media_type="text/plain",
            relative_path=f"sha256/bb/{'b' * 64}",
        ),
        extracted_span=EvidenceSpan(start=0, end=len(extracted_text), text=extracted_text),
        provenance={"collector": "test"},
        ingestion_actor_id="proposer",
    )


def _model_actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.MODEL,
        provider_id="provider",
        model_id="model",
        adapter_id="adapter",
        configuration_hash="c" * 64,
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
    )


def test_proposer_cannot_approve_own_claim() -> None:
    proposal = ProposeClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("same"),
        approval=Approval(approver=_actor("same"), approved_at=datetime(2026, 7, 12, tzinfo=UTC)),
        claim=_claim("same"),
    )

    decision = AdmissionEngine().decide(proposal, _context())

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.SELF_APPROVAL


def test_configuration_equivalent_model_cannot_approve_claim() -> None:
    proposal = ProposeClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_model_actor("model-proposer"),
        approval=Approval(
            approver=_model_actor("model-approver"),
            approved_at=datetime(2026, 7, 12, tzinfo=UTC),
        ),
        claim=_claim(),
    )

    decision = AdmissionEngine().decide(proposal, _context())

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.SELF_APPROVAL


def test_configuration_only_model_alias_cannot_approve_claim() -> None:
    proposer = _model_actor("model-proposer")
    approver = proposer.model_copy(
        update={"actor_id": "model-approver", "configuration_hash": "d" * 64}
    )
    proposal = ProposeClaim(
        proposal_id="proposal-config-alias",
        idempotency_key="key-config-alias",
        proposer=proposer,
        approval=Approval(
            approver=approver,
            approved_at=datetime(2026, 7, 12, tzinfo=UTC),
        ),
        claim=_claim(proposer.actor_id),
    )

    decision = AdmissionEngine().decide(proposal, _context())

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.SELF_APPROVAL


def test_replay_returns_prior_decision_without_reconsidering_proposal() -> None:
    prior = AdmissionEngine.rejected("old-proposal", RejectionCode.PERMISSION_DENIED, "denied")
    proposal = ProposeClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        claim=_claim(),
    )

    replay = AdmissionEngine().decide(proposal, _context(prior_decisions={"key-1": prior}))

    assert replay.replayed
    assert replay.model_copy(update={"replayed": False}) == prior


def test_malformed_model_construct_proposal_is_rejected_without_raising() -> None:
    proposal = AddEvidence.model_construct(proposal_id="proposal-1", idempotency_key="key-1")

    decision = AdmissionEngine().decide(cast(Proposal, proposal), _context())

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL


def test_model_copy_bypass_is_revalidated_before_admission() -> None:
    proposal = ProposeClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        claim=_claim(),
    ).model_copy(update={"claim": {"claim_id": "incomplete"}})

    decision = AdmissionEngine().decide(cast(Proposal, proposal), _context())

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL


def test_model_construct_context_is_revalidated_and_frozen() -> None:
    evidence = _evidence()
    claim = _claim(
        status=ClaimStatus.EVIDENCE_LINKED,
        evidence_links=(
            EvidenceLink(evidence_id=evidence.evidence_id, supporting_span="fixture span"),
        ),
    )
    proposal = TransitionClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        next_claim=_next_claim(claim, ClaimStatus.TESTABLE),
    )
    context = AdmissionContext.model_construct(
        active_policy=_policy(),
        evidence_by_id={evidence.evidence_id: evidence.model_dump(warnings="none")},
        claim_by_id={claim.claim_id: claim.model_dump()},
        prior_decision_by_idempotency_key={},
    )

    decision = AdmissionEngine().decide(proposal, context)

    assert decision.accepted


def test_malformed_model_construct_context_is_rejected_without_raising() -> None:
    context = AdmissionContext.model_construct(active_policy=_policy())
    proposal = AddEvidence(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        evidence=_evidence(),
    )

    decision = AdmissionEngine().decide(proposal, context)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL


def test_new_claim_must_begin_proposed() -> None:
    proposal = ProposeClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        claim=_claim(status=ClaimStatus.WITHDRAWN),
    )

    decision = AdmissionEngine().decide(proposal, _context())

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_STATUS_TRANSITION


def test_transition_requires_exact_successor_version() -> None:
    current = _claim()
    proposal = TransitionClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        next_claim=_next_claim(
            current,
            ClaimStatus.WITHDRAWN,
            version=3,
            parent_version_id="claim-1:2",
        ),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(claim_by_id={current.claim_id: current}),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_STATUS_TRANSITION


def test_transition_rejects_undeclared_status_edge() -> None:
    current = _claim()
    link = EvidenceLink(evidence_id="evidence-1", supporting_span="fixture span")
    proposal = TransitionClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        next_claim=_next_claim(
            current,
            ClaimStatus.CORROBORATED,
            evidence_links=(link,),
        ),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(claim_by_id={current.claim_id: current}),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_STATUS_TRANSITION


def test_transition_rejects_missing_deterministic_evidence() -> None:
    claim = _claim(
        status=ClaimStatus.EVIDENCE_LINKED,
        evidence_links=(EvidenceLink(evidence_id="missing", supporting_span="fixture"),),
    )
    proposal = TransitionClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        next_claim=_next_claim(claim, ClaimStatus.TESTABLE),
    )

    decision = AdmissionEngine().decide(proposal, _context(claim_by_id={claim.claim_id: claim}))

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE


def test_transition_rejects_policy_check_without_deterministic_coverage() -> None:
    evidence = _evidence()
    claim = _claim(
        status=ClaimStatus.EVIDENCE_LINKED,
        evidence_links=(
            EvidenceLink(evidence_id=evidence.evidence_id, supporting_span="fixture span"),
        ),
    )
    proposal = TransitionClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        next_claim=_next_claim(claim, ClaimStatus.TESTABLE),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(
            evidence_by_id={evidence.evidence_id: evidence},
            claim_by_id={claim.claim_id: claim},
            required_claim_checks=("source_exists", "semantic_review"),
        ),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


def test_transition_rejects_nonrequired_independent_review_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence()
    claim = _claim(
        status=ClaimStatus.EVIDENCE_LINKED,
        evidence_links=(
            EvidenceLink(evidence_id=evidence.evidence_id, supporting_span="fixture span"),
        ),
    )
    proposal = TransitionClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        next_claim=_next_claim(claim, ClaimStatus.TESTABLE),
    )
    monkeypatch.setattr(
        admission_engine,
        "run_deterministic_checks",
        lambda _claim, _evidence_by_id: (
            CheckResult(
                code="source_exists",
                outcome=CheckOutcome.PASS_DETERMINISTIC,
                reason="linked evidence exists",
            ),
            CheckResult(
                code="evidence_span_exists",
                outcome=CheckOutcome.PASS_DETERMINISTIC,
                reason="supporting span exists in linked evidence",
            ),
            CheckResult(
                code="semantic_review",
                outcome=CheckOutcome.REQUIRES_INDEPENDENT_REVIEW,
                reason="semantic evaluation requires an independent reviewer",
            ),
        ),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(
            evidence_by_id={evidence.evidence_id: evidence},
            claim_by_id={claim.claim_id: claim},
        ),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


def test_transition_accepts_declared_edge_with_exact_evidence() -> None:
    evidence = _evidence()
    claim = _claim(
        status=ClaimStatus.EVIDENCE_LINKED,
        evidence_links=(
            EvidenceLink(evidence_id=evidence.evidence_id, supporting_span="fixture span"),
        ),
    )
    proposal = TransitionClaim(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        next_claim=_next_claim(claim, ClaimStatus.TESTABLE),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(
            evidence_by_id={evidence.evidence_id: evidence},
            claim_by_id={claim.claim_id: claim},
        ),
    )

    assert decision == TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)


def test_withdrawal_skips_evidence_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    current = _claim()
    proposal = TransitionClaim(
        proposal_id="proposal-withdraw",
        idempotency_key="key-withdraw",
        proposer=_actor("proposer"),
        next_claim=_next_claim(current, ClaimStatus.WITHDRAWN, evidence_links=()),
    )

    def fail_if_called(*_args: object, **_kwargs: object) -> tuple[CheckResult, ...]:
        raise AssertionError("withdrawal must not run evidence checks")

    monkeypatch.setattr(admission_engine, "run_deterministic_checks", fail_if_called)

    decision = AdmissionEngine().decide(
        proposal,
        _context(claim_by_id={current.claim_id: current}),
    )

    assert decision.accepted


def test_evidence_linked_requires_a_new_valid_link() -> None:
    evidence = _evidence()
    existing_link = EvidenceLink(
        evidence_id=evidence.evidence_id,
        supporting_span="fixture span",
    )
    current = _claim(evidence_links=(existing_link,))
    proposal = TransitionClaim(
        proposal_id="proposal-evidence-linked",
        idempotency_key="key-evidence-linked",
        proposer=_actor("proposer"),
        next_claim=_next_claim(
            current,
            ClaimStatus.EVIDENCE_LINKED,
            evidence_links=(existing_link,),
        ),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(
            evidence_by_id={evidence.evidence_id: evidence},
            claim_by_id={current.claim_id: current},
        ),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proposition", "changed proposition"),
        ("scope", "changed scope"),
        ("population_or_system", "changed system"),
        ("epistemic_modality", "changed modality"),
    ],
)
def test_transition_rejects_changes_to_claim_identity_content(field: str, value: str) -> None:
    current = _claim()
    proposal = TransitionClaim(
        proposal_id=f"proposal-{field}",
        idempotency_key=f"key-{field}",
        proposer=_actor("proposer"),
        next_claim=_next_claim(current, ClaimStatus.WITHDRAWN, **{field: value}),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(claim_by_id={current.claim_id: current}),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_STATUS_TRANSITION


def test_transition_creator_must_match_proposer() -> None:
    current = _claim()
    proposal = TransitionClaim(
        proposal_id="proposal-creator",
        idempotency_key="key-creator",
        proposer=_actor("proposer"),
        next_claim=_next_claim(
            current,
            ClaimStatus.WITHDRAWN,
            actor_id="different-actor",
        ),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(claim_by_id={current.claim_id: current}),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH


@pytest.mark.parametrize(
    "updates",
    [
        {"assumptions": ("smuggled assumption",)},
        {
            "evidence_links": (
                EvidenceLink(evidence_id="missing-evidence", supporting_span="missing span"),
            )
        },
    ],
)
def test_withdrawal_is_status_only(updates: dict[str, object]) -> None:
    current = _claim()
    proposal = TransitionClaim(
        proposal_id="proposal-withdraw-status-only",
        idempotency_key="key-withdraw-status-only",
        proposer=_actor("proposer"),
        next_claim=_next_claim(current, ClaimStatus.WITHDRAWN, **updates),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(claim_by_id={current.claim_id: current}),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_STATUS_TRANSITION


def test_transition_parent_is_revalidated_at_public_boundary() -> None:
    current = _claim()
    invalid_next = _next_claim(current, ClaimStatus.WITHDRAWN).model_copy(
        update={"parent_version_id": "claim-1:99"}
    )
    proposal = TransitionClaim.model_construct(
        proposal_type="transition_claim",
        proposal_id="proposal-parent",
        idempotency_key="key-parent",
        proposer=_actor("proposer"),
        approval=None,
        next_claim=invalid_next,
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(claim_by_id={current.claim_id: current}),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [(current, target) for current, targets in ALLOWED.items() for target in targets],
)
def test_every_declared_transition_has_status_specific_admission(
    current_status: ClaimStatus,
    target_status: ClaimStatus,
) -> None:
    evidence = _evidence()
    link = EvidenceLink(evidence_id=evidence.evidence_id, supporting_span="fixture span")
    current_links = () if current_status is ClaimStatus.PROPOSED else (link,)
    current = _claim(status=current_status, evidence_links=current_links)
    next_links = current_links if target_status is ClaimStatus.WITHDRAWN else (link,)
    proposal = TransitionClaim(
        proposal_id=f"proposal-{current_status}-{target_status}",
        idempotency_key=f"key-{current_status}-{target_status}",
        proposer=_actor("proposer"),
        next_claim=_next_claim(
            current,
            target_status,
            evidence_links=next_links,
        ),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(
            evidence_by_id={evidence.evidence_id: evidence},
            claim_by_id={current.claim_id: current},
        ),
    )

    proof_statuses = {
        ClaimStatus.REPRODUCED,
        ClaimStatus.CORROBORATED,
        ClaimStatus.CONSTRAINT_VALIDATED,
        ClaimStatus.FALSIFIED,
        ClaimStatus.SUPERSEDED,
    }
    if target_status in proof_statuses:
        assert not decision.accepted
        assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    else:
        assert decision.accepted


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        (ClaimStatus.PROPOSED, ClaimStatus.FALSIFIED),
        (ClaimStatus.FALSIFIED, ClaimStatus.SUPERSEDED),
    ],
)
def test_unimplemented_terminal_proof_precedes_generic_evidence_validation(
    current_status: ClaimStatus,
    target_status: ClaimStatus,
) -> None:
    missing_link = EvidenceLink(
        evidence_id="missing-evidence",
        supporting_span="missing span",
    )
    current = _claim(
        status=current_status,
        evidence_links=() if current_status is ClaimStatus.PROPOSED else (missing_link,),
    )
    proposal = TransitionClaim(
        proposal_id=f"proposal-{target_status}",
        idempotency_key=f"key-{target_status}",
        proposer=_actor("proposer"),
        next_claim=_next_claim(
            current,
            target_status,
            evidence_links=(missing_link,),
        ),
    )

    decision = AdmissionEngine().decide(
        proposal,
        _context(claim_by_id={current.claim_id: current}),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


def test_add_evidence_is_accepted_without_mutating_context() -> None:
    evidence_by_id: dict[str, EvidenceRecord] = {}
    context = _context(evidence_by_id=evidence_by_id)
    proposal = AddEvidence(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        evidence=_evidence(),
    )

    decision = AdmissionEngine().decide(proposal, context)

    assert decision.accepted
    assert evidence_by_id == {}
    assert dict(context.evidence_by_id) == {}


@pytest.mark.parametrize("proposal_type", ["evidence", "claim"])
def test_initial_entity_creator_must_match_proposer(proposal_type: str) -> None:
    proposer = _actor("proposer")
    proposal: Proposal
    if proposal_type == "evidence":
        proposal = AddEvidence(
            proposal_id="proposal-provenance",
            idempotency_key="key-provenance",
            proposer=proposer,
            evidence=_evidence().model_copy(update={"ingestion_actor_id": "different-actor"}),
        )
    else:
        proposal = ProposeClaim(
            proposal_id="proposal-provenance",
            idempotency_key="key-provenance",
            proposer=proposer,
            claim=_claim("different-actor"),
        )

    decision = AdmissionEngine().decide(proposal, _context())

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH


@pytest.mark.parametrize("proposal_type", ["evidence", "claim"])
def test_duplicate_entity_id_is_rejected(proposal_type: str) -> None:
    evidence = _evidence()
    claim = _claim()
    proposal: Proposal
    context: AdmissionContext
    if proposal_type == "evidence":
        proposal = AddEvidence(
            proposal_id="proposal-1",
            idempotency_key="key-1",
            proposer=_actor("proposer"),
            evidence=evidence,
        )
        context = _context(evidence_by_id={evidence.evidence_id: evidence})
    else:
        proposal = ProposeClaim(
            proposal_id="proposal-1",
            idempotency_key="key-1",
            proposer=_actor("proposer"),
            claim=claim,
        )
        context = _context(claim_by_id={claim.claim_id: claim})

    decision = AdmissionEngine().decide(proposal, context)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS


@pytest.mark.parametrize("entity_type", ["evidence", "claim"])
def test_context_entity_mapping_key_must_match_contained_id(entity_type: str) -> None:
    evidence = _evidence()
    claim = _claim()
    proposal = AddEvidence(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        evidence=evidence,
    )
    context = _context(
        evidence_by_id={"wrong-evidence-id": evidence} if entity_type == "evidence" else {},
        claim_by_id={"wrong-claim-id": claim} if entity_type == "claim" else {},
    )

    decision = AdmissionEngine().decide(proposal, context)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH


def test_admission_context_mappings_are_deeply_immutable() -> None:
    context = _context(evidence_by_id={"evidence-1": _evidence()})

    with pytest.raises(TypeError):
        cast(dict[str, EvidenceRecord], context.evidence_by_id)["other"] = _evidence()


def test_proposals_and_decisions_round_trip_as_durable_json() -> None:
    proposal = AddEvidence(
        proposal_id="proposal-1",
        idempotency_key="key-1",
        proposer=_actor("proposer"),
        evidence=_evidence(),
    )
    decision = AdmissionEngine.rejected(
        proposal.proposal_id,
        RejectionCode.MISSING_EVIDENCE,
        "claim evidence checks failed",
    )

    proposal_adapter = TypeAdapter(Proposal)

    assert proposal_adapter.validate_json(proposal_adapter.dump_json(proposal)) == proposal
    assert TransactionDecision.model_validate_json(decision.model_dump_json()) == decision


def test_proposal_contract_rejects_coerced_identifiers() -> None:
    with pytest.raises(ValidationError, match="proposal_id"):
        AddEvidence(
            proposal_id=1,
            idempotency_key="key-1",
            proposer=_actor("proposer"),
            evidence=_evidence(),
        )


def test_task_seven_contracts_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        AddEvidence(
            proposal_id="proposal-1",
            idempotency_key="key-1",
            proposer=_actor("proposer"),
            evidence=_evidence(),
            unexpected="extra",
        )


@pytest.mark.parametrize(
    "decision",
    [
        TransactionDecision(proposal_id="proposal-1", accepted=True),
        TransactionDecision(
            proposal_id="proposal-1",
            accepted=False,
            reasons=(RejectionReason(code=RejectionCode.PERMISSION_DENIED, message="denied"),),
        ),
    ],
)
def test_valid_transaction_decision_invariants(decision: TransactionDecision) -> None:
    assert decision == TransactionDecision.model_validate_json(decision.model_dump_json())


@pytest.mark.parametrize(
    "values",
    [
        {
            "proposal_id": "proposal-1",
            "accepted": True,
            "reasons": (RejectionReason(code=RejectionCode.PERMISSION_DENIED, message="denied"),),
        },
        {"proposal_id": "proposal-1", "accepted": False},
    ],
)
def test_transaction_decision_rejects_inconsistent_reason_state(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="reason"):
        TransactionDecision(**values)
