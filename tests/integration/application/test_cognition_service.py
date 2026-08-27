from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from super_scientist.application.cognition import service as cognition_service
from super_scientist.application.cognition.service import (
    RecordCapabilityProfileHandler,
    RecordCohortPlanHandler,
    RecordDiversityAssessmentHandler,
    _CapabilityProfileContext,
    _CohortPlanContext,
    _DiversityAssessmentContext,
)
from super_scientist.application.transactions.cognition import (
    cognition_capabilities,
    fixed_cognition_handlers,
)
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicy,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CapabilityRequirement,
    CohortPlan,
    CohortPlanReceiptRef,
    CohortRequest,
    DiversityAssessment,
    assess_diversity,
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
    RecordDiversityAssessment,
    RejectionCode,
)
from super_scientist.providers.storage.cognitive_records import CohortPlanRepository
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.repositories import RepositorySet
from tests.unit.collaboration.conftest import profile

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "f" * 64


@pytest.fixture
def v2_policy_snapshot() -> PolicySnapshot:
    return PolicySnapshot(
        policy_hash=POLICY_HASH,
        policy=GovernancePolicyV2(
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
        ),
    )


def _model_actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity.model(actor_id, "provider", actor_id, "adapter", NOW)


def _human(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=NOW)


def _approval() -> Approval:
    return Approval(approver=_human("approver"), approved_at=NOW)


def _receipt(proposal_id: str) -> CapabilityProfileReceiptRef:
    return CapabilityProfileReceiptRef(
        proposal_id=proposal_id,
        proposal_hash="a" * 64,
        audit_event_id=f"audit-{proposal_id}",
        audit_event_hash="b" * 64,
    )


def _request(*actor_ids: str) -> CohortRequest:
    return CohortRequest.build(
        request_id="cohort-request",
        task_id="task",
        required_capabilities=(
            CapabilityRequirement(
                requirement_id="requirement-analysis",
                capability_id="analysis",
                task_family_id="research",
                evidence_snapshot_hash="e" * 64,
            ),
        ),
        preferred_capabilities=(),
        min_members=1,
        max_members=len(actor_ids),
        candidate_actor_ids=tuple(actor_ids),
        prohibited_combinations=(),
        governing_policy_hash=POLICY_HASH,
    )


@dataclass
class _CapabilityReads:
    active_policy: PolicySnapshot
    existing: CapabilityProfile | None = None

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_profile(self, profile_id: str) -> CapabilityProfile | None:
        del profile_id
        return self.existing


@dataclass
class _CohortReads:
    active_policy: PolicySnapshot
    profiles: dict[str, CapabilityProfile]
    existing: CohortPlan | None = None

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_cohort_plan(self, cohort_plan_id: str) -> CohortPlan | None:
        del cohort_plan_id
        return self.existing

    def resolve_profile_receipt(
        self, reference: CapabilityProfileReceiptRef
    ) -> CapabilityProfile | None:
        return self.profiles.get(reference.proposal_id)


@dataclass
class _DiversityReads(_CohortReads):
    cohort: CohortPlan | None = None
    assessment: DiversityAssessment | None = None

    def get_diversity_assessment(self, diversity_assessment_id: str) -> DiversityAssessment | None:
        del diversity_assessment_id
        return self.assessment

    def resolve_cohort_receipt(self, reference: CohortPlanReceiptRef) -> CohortPlan | None:
        del reference
        return self.cohort


@dataclass
class _Writes:
    records: list[BaseModel] = field(default_factory=list)

    def append_authoritative(self, record: BaseModel) -> None:
        self.records.append(record)

    def update_projection(self, record: BaseModel) -> None:
        raise AssertionError(f"unexpected mutable projection: {record!r}")


def test_capability_handler_requires_current_policy_and_projects_only_after_acceptance(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    retained = profile("peer-a")
    proposal = RecordCapabilityProfile(
        proposal_id="record-profile",
        idempotency_key="record-profile",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        profile=retained,
    )
    handler = RecordCapabilityProfileHandler()
    policy = v2_policy_snapshot.model_copy(update={"policy_hash": POLICY_HASH})

    decision = handler.decide(proposal, handler.build_context(proposal, _CapabilityReads(policy)))
    writes = _Writes()
    handler.project(proposal, decision, writes)

    assert decision.accepted is True
    assert writes.records == [retained]


def test_cohort_handler_recomputes_and_rejects_caller_selection_mismatch(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    retained = profile("peer-a")
    request = _request("peer-a")
    expected = build_cohort(request, (retained,))
    forged = expected.model_copy(update={"minimum_size_met": False})
    proposal = RecordCohortPlan.model_construct(
        proposal_id="record-cohort",
        idempotency_key="record-cohort",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        proposal_type="record_cohort_plan",
        request=request,
        profile_receipts=(_receipt("record-profile"),),
        plan=forged,
    )
    handler = RecordCohortPlanHandler()
    policy = v2_policy_snapshot.model_copy(update={"policy_hash": POLICY_HASH})

    decision = handler.decide(
        proposal,
        handler.build_context(
            proposal,
            _CohortReads(policy, {"record-profile": retained}),
        ),
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.DERIVATION_MISMATCH


def test_cohort_handler_rejects_forged_or_stale_profile_receipt(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    retained = profile("peer-a")
    request = _request("peer-a")
    proposal = RecordCohortPlan(
        proposal_id="record-cohort",
        idempotency_key="record-cohort",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        request=request,
        profile_receipts=(_receipt("forged-profile"),),
        plan=build_cohort(request, (retained,)),
    )
    handler = RecordCohortPlanHandler()
    policy = v2_policy_snapshot.model_copy(update={"policy_hash": POLICY_HASH})

    decision = handler.decide(
        proposal,
        handler.build_context(proposal, _CohortReads(policy, {})),
    )

    assert decision.reasons[0].code is RejectionCode.STALE_REFERENCE


def test_diversity_handler_resolves_receipts_and_recomputes_assessment(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    retained = profile("peer-a")
    request = _request("peer-a")
    cohort = build_cohort(request, (retained,))
    assessment = assess_diversity(cohort, (retained,), ())
    cohort_receipt = CohortPlanReceiptRef(
        proposal_id="record-cohort",
        proposal_hash="c" * 64,
        audit_event_id="audit-record-cohort",
        audit_event_hash="d" * 64,
    )
    proposal = RecordDiversityAssessment(
        proposal_id="record-diversity",
        idempotency_key="record-diversity",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        cohort_plan_receipt=cohort_receipt,
        profile_receipts=(_receipt("record-profile"),),
        error_correlations=(),
        assessment=assessment,
    )
    handler = RecordDiversityAssessmentHandler()

    decision = handler.decide(
        proposal,
        handler.build_context(
            proposal,
            _DiversityReads(
                v2_policy_snapshot,
                {"record-profile": retained},
                cohort=cohort,
            ),
        ),
    )

    assert decision.accepted is True


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("policy", RejectionCode.POLICY_HASH_MISMATCH),
        ("derivation", RejectionCode.DERIVATION_MISMATCH),
        ("existing", RejectionCode.ENTITY_ALREADY_EXISTS),
    ),
)
def test_capability_handler_rejects_each_durable_boundary(
    v2_policy_snapshot: PolicySnapshot,
    mutation: str,
    expected_code: RejectionCode,
) -> None:
    retained = profile("peer-a")
    proposed = retained
    existing = None
    if mutation == "policy":
        proposed = retained.model_copy(update={"governing_policy_hash": "e" * 64})
    elif mutation == "derivation":
        proposed = retained.model_copy(update={"content_hash": "e" * 64})
    else:
        existing = retained
    proposal = RecordCapabilityProfile.model_construct(
        proposal_id=f"record-profile-{mutation}",
        idempotency_key=f"record-profile-{mutation}",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        proposal_type="record_capability_profile",
        profile=proposed,
    )

    decision = RecordCapabilityProfileHandler().decide(
        proposal,
        _CapabilityProfileContext(
            active_policy=v2_policy_snapshot,
            existing_profile=existing,
        ),
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("policy", RejectionCode.POLICY_HASH_MISMATCH),
        ("missing", RejectionCode.STALE_REFERENCE),
        ("duplicate", RejectionCode.STALE_REFERENCE),
        ("derivation", RejectionCode.DERIVATION_MISMATCH),
        ("existing", RejectionCode.ENTITY_ALREADY_EXISTS),
    ),
)
def test_cohort_handler_rejects_each_grounding_boundary(
    v2_policy_snapshot: PolicySnapshot,
    mutation: str,
    expected_code: RejectionCode,
) -> None:
    retained = profile("peer-a")
    request = _request("peer-a")
    plan = build_cohort(request, (retained,))
    receipts = (_receipt("record-profile"),)
    resolved: tuple[CapabilityProfile | None, ...] = (retained,)
    existing = None
    if mutation == "policy":
        request = request.model_copy(update={"governing_policy_hash": "e" * 64})
    elif mutation == "missing":
        resolved = (None,)
    elif mutation == "duplicate":
        receipts = (receipts[0], receipts[0])
        resolved = (retained, retained)
    elif mutation == "derivation":
        plan = plan.model_copy(update={"minimum_size_met": False})
    else:
        existing = plan
    proposal = RecordCohortPlan.model_construct(
        proposal_id=f"record-cohort-{mutation}",
        idempotency_key=f"record-cohort-{mutation}",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        proposal_type="record_cohort_plan",
        request=request,
        profile_receipts=receipts,
        plan=plan,
    )

    decision = RecordCohortPlanHandler().decide(
        proposal,
        _CohortPlanContext(
            active_policy=v2_policy_snapshot,
            existing_plan=existing,
            resolved_profiles=resolved,
        ),
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("policy", RejectionCode.POLICY_HASH_MISMATCH),
        ("missing-cohort", RejectionCode.STALE_REFERENCE),
        ("missing-profile", RejectionCode.STALE_REFERENCE),
        ("duplicate", RejectionCode.STALE_REFERENCE),
        ("derivation", RejectionCode.DERIVATION_MISMATCH),
        ("existing", RejectionCode.ENTITY_ALREADY_EXISTS),
    ),
)
def test_diversity_handler_rejects_each_evidence_boundary(
    v2_policy_snapshot: PolicySnapshot,
    mutation: str,
    expected_code: RejectionCode,
) -> None:
    retained = profile("peer-a")
    request = _request("peer-a")
    cohort = build_cohort(request, (retained,))
    assessment = assess_diversity(cohort, (retained,), ())
    cohort_receipt = CohortPlanReceiptRef(
        proposal_id="record-cohort",
        proposal_hash="c" * 64,
        audit_event_id="audit-record-cohort",
        audit_event_hash="d" * 64,
    )
    profile_receipts = (_receipt("record-profile"),)
    resolved_cohort: CohortPlan | None = cohort
    resolved_profiles: tuple[CapabilityProfile | None, ...] = (retained,)
    existing = None
    if mutation == "policy":
        assessment = assessment.model_copy(update={"governing_policy_hash": "e" * 64})
    elif mutation == "missing-cohort":
        resolved_cohort = None
    elif mutation == "missing-profile":
        resolved_profiles = (None,)
    elif mutation == "duplicate":
        profile_receipts = (
            CapabilityProfileReceiptRef(
                proposal_id=cohort_receipt.proposal_id,
                proposal_hash="a" * 64,
                audit_event_id="audit-duplicate",
                audit_event_hash="b" * 64,
            ),
        )
    elif mutation == "derivation":
        assessment = assessment.model_copy(update={"content_hash": "e" * 64})
    else:
        existing = assessment
    proposal = RecordDiversityAssessment.model_construct(
        proposal_id=f"record-diversity-{mutation}",
        idempotency_key=f"record-diversity-{mutation}",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        proposal_type="record_diversity_assessment",
        cohort_plan_receipt=cohort_receipt,
        profile_receipts=profile_receipts,
        error_correlations=(),
        assessment=assessment,
    )

    decision = RecordDiversityAssessmentHandler().decide(
        proposal,
        _DiversityAssessmentContext(
            active_policy=v2_policy_snapshot,
            existing_assessment=existing,
            resolved_cohort=resolved_cohort,
            resolved_profiles=resolved_profiles,
        ),
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("v1", RejectionCode.PERMISSION_DENIED),
        ("missing-requirement", RejectionCode.PERMISSION_DENIED),
        ("weak-requirement", RejectionCode.INSUFFICIENT_GROUNDING),
        ("missing-approval", RejectionCode.INDEPENDENT_REVIEW_REQUIRED),
        ("nonhuman-authority", RejectionCode.PERMISSION_DENIED),
    ),
)
def test_capability_handler_rejects_each_cognitive_authority_boundary(
    mutation: str,
    expected_code: RejectionCode,
) -> None:
    requirement = AdaptationRequirement(
        change_target=ChangeTarget.RESEARCH_PROCESS,
        persistence=PersistenceScope.RUN_LOCAL,
        minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
        required_approver_kind=ActorKind.HUMAN,
        protected_evaluation_required=False,
        rollback_required=False,
    )
    if mutation == "v1":
        snapshot = PolicySnapshot(
            policy_hash=POLICY_HASH,
            policy=GovernancePolicy(required_claim_checks=("source_exists",)),
        )
    else:
        if mutation == "missing-requirement":
            requirement = requirement.model_copy(
                update={
                    "change_target": ChangeTarget.PROMPT,
                    "persistence": PersistenceScope.PERSISTENT_SKILL,
                }
            )
        elif mutation == "weak-requirement":
            requirement = requirement.model_copy(update={"rollback_required": True})
        elif mutation == "nonhuman-authority":
            requirement = requirement.model_copy(update={"required_approver_kind": ActorKind.MODEL})
        snapshot = PolicySnapshot(
            policy_hash=POLICY_HASH,
            policy=GovernancePolicyV2(
                required_claim_checks=("source_exists",),
                human_approval_for=frozenset(),
                adaptation_requirements=(requirement,),
            ),
        )
    approver = (
        ActorIdentity.model(
            "model-approver",
            "independent-provider",
            "independent-model",
            "independent-adapter",
            NOW,
        )
        if mutation == "nonhuman-authority"
        else _human("approver")
    )
    proposal = RecordCapabilityProfile(
        proposal_id=f"authority-{mutation}",
        idempotency_key=f"authority-{mutation}",
        proposer=_model_actor("proposer"),
        approval=None
        if mutation == "missing-approval"
        else Approval(approver=approver, approved_at=NOW),
        profile=profile("peer-a"),
    )

    decision = RecordCapabilityProfileHandler().decide(
        proposal,
        _CapabilityProfileContext(active_policy=snapshot, existing_profile=None),
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code


def test_cognitive_handlers_fail_closed_when_canonical_recomputation_raises(
    v2_policy_snapshot: PolicySnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = profile("peer-a")
    request = _request("peer-a")
    cohort = build_cohort(request, (retained,))
    cohort_proposal = RecordCohortPlan(
        proposal_id="cohort-recomputation-error",
        idempotency_key="cohort-recomputation-error",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        request=request,
        profile_receipts=(_receipt("record-profile"),),
        plan=cohort,
    )
    assessment = assess_diversity(cohort, (retained,), ())
    diversity_proposal = RecordDiversityAssessment(
        proposal_id="diversity-recomputation-error",
        idempotency_key="diversity-recomputation-error",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        cohort_plan_receipt=CohortPlanReceiptRef(
            proposal_id="record-cohort",
            proposal_hash="c" * 64,
            audit_event_id="audit-record-cohort",
            audit_event_hash="d" * 64,
        ),
        profile_receipts=(_receipt("record-profile"),),
        error_correlations=(),
        assessment=assessment,
    )

    def reject_recomputation(*args: object) -> None:
        del args
        raise ValueError("bounded canonical recomputation failed")

    monkeypatch.setattr(cognition_service, "build_cohort", reject_recomputation)
    cohort_decision = RecordCohortPlanHandler().decide(
        cohort_proposal,
        _CohortPlanContext(
            active_policy=v2_policy_snapshot,
            existing_plan=None,
            resolved_profiles=(retained,),
        ),
    )
    monkeypatch.setattr(cognition_service, "assess_diversity", reject_recomputation)
    diversity_decision = RecordDiversityAssessmentHandler().decide(
        diversity_proposal,
        _DiversityAssessmentContext(
            active_policy=v2_policy_snapshot,
            existing_assessment=None,
            resolved_cohort=cohort,
            resolved_profiles=(retained,),
        ),
    )

    assert cohort_decision.reasons[0].code is RejectionCode.DERIVATION_MISMATCH
    assert diversity_decision.reasons[0].code is RejectionCode.DERIVATION_MISMATCH


def test_cognitive_projection_rejects_a_rejected_decision(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    proposal = RecordCapabilityProfile(
        proposal_id="rejected-profile",
        idempotency_key="rejected-profile",
        proposer=_model_actor("proposer"),
        approval=None,
        profile=profile("peer-a"),
    )
    handler = RecordCapabilityProfileHandler()
    decision = handler.decide(
        proposal,
        _CapabilityProfileContext(
            active_policy=v2_policy_snapshot,
            existing_profile=None,
        ),
    )

    with pytest.raises(ValueError, match="rejected proposals cannot be projected"):
        handler.project(proposal, decision, _Writes())


def test_cohort_and_diversity_handlers_apply_authority_before_derivation() -> None:
    snapshot = PolicySnapshot(
        policy_hash=POLICY_HASH,
        policy=GovernancePolicy(required_claim_checks=("source_exists",)),
    )
    retained = profile("peer-a")
    request = _request("peer-a")
    cohort = build_cohort(request, (retained,))
    cohort_proposal = RecordCohortPlan(
        proposal_id="v1-cohort",
        idempotency_key="v1-cohort",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        request=request,
        profile_receipts=(_receipt("record-profile"),),
        plan=cohort,
    )
    diversity_proposal = RecordDiversityAssessment(
        proposal_id="v1-diversity",
        idempotency_key="v1-diversity",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        cohort_plan_receipt=CohortPlanReceiptRef(
            proposal_id="record-cohort",
            proposal_hash="c" * 64,
            audit_event_id="audit-record-cohort",
            audit_event_hash="d" * 64,
        ),
        profile_receipts=(_receipt("record-profile"),),
        error_correlations=(),
        assessment=assess_diversity(cohort, (retained,), ()),
    )

    cohort_decision = RecordCohortPlanHandler().decide(
        cohort_proposal,
        _CohortPlanContext(
            active_policy=snapshot,
            existing_plan=None,
            resolved_profiles=(retained,),
        ),
    )
    diversity_decision = RecordDiversityAssessmentHandler().decide(
        diversity_proposal,
        _DiversityAssessmentContext(
            active_policy=snapshot,
            existing_assessment=None,
            resolved_cohort=cohort,
            resolved_profiles=(retained,),
        ),
    )

    assert cohort_decision.reasons[0].code is RejectionCode.PERMISSION_DENIED
    assert diversity_decision.reasons[0].code is RejectionCode.PERMISSION_DENIED


def test_capability_handler_fails_closed_on_nonvalidating_nested_profile_state(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    malformed = profile("peer-a").model_copy(update={"actor": object()})
    proposal = RecordCapabilityProfile.model_construct(
        proposal_id="malformed-profile",
        idempotency_key="malformed-profile",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        proposal_type="record_capability_profile",
        profile=malformed,
    )

    decision = RecordCapabilityProfileHandler().decide(
        proposal,
        _CapabilityProfileContext(
            active_policy=v2_policy_snapshot,
            existing_profile=None,
        ),
    )

    assert decision.reasons[0].code is RejectionCode.DERIVATION_MISMATCH


def test_fixed_cognition_handlers_have_one_source_controlled_route_each() -> None:
    assert tuple(handler.proposal_type for handler in fixed_cognition_handlers()) == (
        "record_capability_profile",
        "record_cohort_plan",
        "record_diversity_assessment",
    )


def test_cognition_capabilities_resolve_exact_accepted_receipt_and_project_plan(
    tmp_path,
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'cognition.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    retained = profile("peer-a")
    profile_proposal = RecordCapabilityProfile(
        proposal_id="record-profile",
        idempotency_key="record-profile",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        profile=retained,
    )
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(
                text(
                    "INSERT INTO governance_policies "
                    "(policy_hash, policy_json, created_at) "
                    "VALUES (:policy_hash, :policy_json, :created_at)"
                ),
                {
                    "policy_hash": POLICY_HASH,
                    "policy_json": v2_policy_snapshot.policy.model_dump_json(),
                    "created_at": NOW.isoformat(),
                },
            )
            profile_handler = RecordCapabilityProfileHandler()
            profile_io = cognition_capabilities(
                profile_proposal,
                connection,
                v2_policy_snapshot,
                current_transaction_created_at=NOW,
            )
            decision = profile_handler.decide(
                profile_proposal,
                profile_handler.build_context(profile_proposal, profile_io),
            )
            profile_handler.project(profile_proposal, decision, profile_io)
            repositories = RepositorySet(connection)
            repositories.transactions.add(profile_proposal, decision, NOW)
            repositories.audit.add(
                append_event(
                    None,
                    "transaction_decision",
                    {
                        "proposal": profile_proposal.model_dump(mode="json"),
                        "decision": decision.model_dump(mode="json"),
                        "policy_hash": POLICY_HASH,
                        "stored_policy_hash": POLICY_HASH,
                        "transaction_persisted": True,
                    },
                    NOW,
                )
            )

        with engine.connect() as connection:
            repositories = RepositorySet(connection)
            stored = repositories.transactions.get_by_proposal_id(profile_proposal.proposal_id)
            audit = repositories.audit.last()
            assert stored is not None
            assert audit is not None
            receipt = CapabilityProfileReceiptRef(
                proposal_id=profile_proposal.proposal_id,
                proposal_hash=stored.proposal_hash,
                audit_event_id=audit.event_id,
                audit_event_hash=audit.event_hash,
            )

        request = _request("peer-a")
        cohort_proposal = RecordCohortPlan(
            proposal_id="record-cohort",
            idempotency_key="record-cohort",
            proposer=_model_actor("proposer"),
            approval=_approval(),
            request=request,
            profile_receipts=(receipt,),
            plan=build_cohort(request, (retained,)),
        )
        with engine.connect() as connection, connection.begin():
            cohort_handler = RecordCohortPlanHandler()
            cohort_io = cognition_capabilities(
                cohort_proposal,
                connection,
                v2_policy_snapshot,
                current_transaction_created_at=NOW,
            )
            decision = cohort_handler.decide(
                cohort_proposal,
                cohort_handler.build_context(cohort_proposal, cohort_io),
            )
            cohort_handler.project(cohort_proposal, decision, cohort_io)
            repositories = RepositorySet(connection)
            repositories.transactions.add(cohort_proposal, decision, NOW)
            repositories.audit.add(
                append_event(
                    repositories.audit.last(),
                    "transaction_decision",
                    {
                        "proposal": cohort_proposal.model_dump(mode="json"),
                        "decision": decision.model_dump(mode="json"),
                        "policy_hash": POLICY_HASH,
                        "stored_policy_hash": POLICY_HASH,
                        "transaction_persisted": True,
                    },
                    NOW,
                )
            )

        with engine.connect() as connection:
            assert CohortPlanRepository(connection).list_all() == (cohort_proposal.plan,)
    finally:
        engine.dispose()
