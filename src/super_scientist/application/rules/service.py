from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.behavioral_rules.consolidation import (
    build_candidate_diff,
    classify_overlap,
    rule_actors_are_independent,
)
from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    OverlapClassification,
    ReviewerAssessment,
    RuleAction,
    RuleConsolidationDecision,
    RuleIncident,
    RuleStatus,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    ChangeClassification,
    EvaluatorAuditRecord,
    MeasurementDecision,
    SelfImprovementMeasurementRecord,
)
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    ConsolidateBehavioralRule,
    ImportReviewerAssessment,
    ProposeBehavioralRule,
    RecordRuleIncident,
    RejectionCode,
    TransactionDecision,
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
    )
    from super_scientist.application.transactions.coordinator import TransactionCoordinator


type RuleMutationProposal = (
    RecordRuleIncident
    | ProposeBehavioralRule
    | ImportReviewerAssessment
    | ConsolidateBehavioralRule
)

FIXED_RULE_CLASSIFICATION = ChangeClassification(
    target=ChangeTarget.BEHAVIORAL_RULE,
    loop_closure=LoopClosure.HUMAN_IN_LOOP,
    persistence=PersistenceScope.PERSISTENT_RULE,
    verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    grounding=ExternalGrounding.PRIMARY_SOURCE,
    signal=ImprovementSignal.EXTRINSIC_GROUNDED_EXPERIENCE,
)


class _IncidentReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_incident(self, incident_id: str) -> RuleIncident | None: ...


class _RuleProposalReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None: ...

    def list_rules(self) -> tuple[BehavioralRuleVersion, ...]: ...

    def get_incident(self, incident_id: str) -> RuleIncident | None: ...


class _AssessmentReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None: ...

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None: ...

    def get_incident(self, incident_id: str) -> RuleIncident | None: ...

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None: ...


class _IntegratorReadCapability(_AssessmentReadCapability, Protocol):
    def get_decision(self, decision_id: str) -> RuleConsolidationDecision | None: ...

    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None: ...

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None: ...

    def get_head(self, rule_id: str) -> tuple[str, str, RuleStatus] | None: ...


class _ContextModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class _IncidentContext(_ContextModel):
    active_policy: PolicySnapshot
    existing: RuleIncident | None


class _RuleProposalContext(_ContextModel):
    active_policy: PolicySnapshot
    existing: BehavioralRuleVersion | None
    rules: tuple[BehavioralRuleVersion, ...]
    incidents: tuple[RuleIncident | None, ...]


class _AssessmentContext(_ContextModel):
    active_policy: PolicySnapshot
    existing: ReviewerAssessment | None
    rules: tuple[BehavioralRuleVersion | None, ...]
    incidents: tuple[RuleIncident | None, ...]
    reviewed_proposal: ProposeBehavioralRule | None


class _ConsolidationContext(_ContextModel):
    active_policy: PolicySnapshot
    existing_decision: RuleConsolidationDecision | None
    existing_candidate: BehavioralRuleVersion | None
    assessments: tuple[ReviewerAssessment | None, ...]
    incidents: tuple[RuleIncident | None, ...]
    predecessors: tuple[BehavioralRuleVersion | None, ...]
    measurement: SelfImprovementMeasurementRecord | None
    evaluator_audit: EvaluatorAuditRecord | None
    current_head: tuple[str, str, RuleStatus] | None


class RecordRuleIncidentHandler:
    proposal_type = "record_rule_incident"

    def build_context(
        self,
        proposal: RecordRuleIncident,
        reads: HandlerReadCapability,
    ) -> _IncidentContext:
        capability = cast(_IncidentReadCapability, reads)
        return _IncidentContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_incident(proposal.incident.incident_id),
        )

    def decide(
        self,
        proposal: RecordRuleIncident,
        context: _IncidentContext,
    ) -> TransactionDecision:
        incident = proposal.incident
        rejection = rule_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=(incident.reported_by,),
        )
        if rejection is not None:
            return rejection
        if incident.reported_by != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "rule incident reporter must match the proposal actor",
            )
        if incident.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_rejection(proposal.proposal_id, "rule incident")
        return _stable_record_decision(
            proposal.proposal_id,
            context.existing,
            incident,
            "rule incident",
        )

    def project(
        self,
        proposal: RecordRuleIncident,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.incident)


class ProposeBehavioralRuleHandler:
    proposal_type = "propose_behavioral_rule"

    def build_context(
        self,
        proposal: ProposeBehavioralRule,
        reads: HandlerReadCapability,
    ) -> _RuleProposalContext:
        capability = cast(_RuleProposalReadCapability, reads)
        rule = proposal.rule_version
        return _RuleProposalContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_rule(rule.rule_version_id),
            rules=capability.list_rules(),
            incidents=tuple(
                capability.get_incident(incident_id) for incident_id in rule.source_incident_ids
            ),
        )

    def decide(
        self,
        proposal: ProposeBehavioralRule,
        context: _RuleProposalContext,
    ) -> TransactionDecision:
        rule = proposal.rule_version
        rejection = rule_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=(rule.creator,),
        )
        if rejection is not None:
            return rejection
        if rule.creator != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "behavioral rule creator must match the proposal actor",
            )
        if rule.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_rejection(proposal.proposal_id, "behavioral rule")
        stable = _stable_record_decision(
            proposal.proposal_id,
            context.existing,
            rule,
            "behavioral rule version",
        )
        if context.existing is not None:
            return stable
        if any(item is None for item in context.incidents):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "behavioral rule must reference retained incidents",
            )
        if (
            rule.status not in {RuleStatus.PROPOSED, RuleStatus.UNDER_REVIEW}
            or rule.approver is not None
            or rule.approved_at is not None
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.PERMISSION_DENIED,
                "initial rule proposals must remain unapproved and non-active",
            )
        overlaps = tuple(
            classify_overlap(rule, existing)
            for existing in context.rules
            if existing.rule_version_id != rule.rule_version_id
        )
        if OverlapClassification.EXACT_DUPLICATE in overlaps:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DUPLICATE_RULE,
                "an exact behavioral rule duplicate already exists",
            )
        if (
            OverlapClassification.SEMANTIC_DUPLICATE in overlaps
            and rule.status is not RuleStatus.UNDER_REVIEW
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DUPLICATE_RULE,
                "semantic duplicates must enter reviewer assessment",
            )
        return stable

    def project(
        self,
        proposal: ProposeBehavioralRule,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.rule_version)


class ImportReviewerAssessmentHandler:
    proposal_type = "import_reviewer_assessment"

    def build_context(
        self,
        proposal: ImportReviewerAssessment,
        reads: HandlerReadCapability,
    ) -> _AssessmentContext:
        capability = cast(_AssessmentReadCapability, reads)
        assessment = proposal.assessment
        return _AssessmentContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_assessment(assessment.assessment_id),
            rules=tuple(
                capability.get_rule(rule_version_id)
                for rule_version_id in assessment.rule_version_ids
            ),
            incidents=tuple(
                capability.get_incident(incident_id) for incident_id in assessment.incident_ids
            ),
            reviewed_proposal=capability.reviewed_rule_proposal(assessment.proposal_id),
        )

    def decide(
        self,
        proposal: ImportReviewerAssessment,
        context: _AssessmentContext,
    ) -> TransactionDecision:
        assessment = proposal.assessment
        authority_actors = tuple(
            (
                *(item.creator for item in context.rules if item is not None),
                *(item.reported_by for item in context.incidents if item is not None),
            )
        )
        rejection = rule_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=authority_actors,
        )
        if rejection is not None:
            return rejection
        if assessment.provenance.actor != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "reviewer assessment actor must match the proposal actor",
            )
        if assessment.provenance.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_rejection(proposal.proposal_id, "reviewer assessment")
        stable = _stable_record_decision(
            proposal.proposal_id,
            context.existing,
            assessment,
            "reviewer assessment",
        )
        if context.existing is not None:
            return stable
        if (
            context.reviewed_proposal is None
            or any(item is None for item in context.rules)
            or any(item is None for item in context.incidents)
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "reviewer assessment must reference an accepted rule proposal and retained records",
            )
        reviewed_rule = context.reviewed_proposal.rule_version
        if reviewed_rule.rule_version_id not in assessment.rule_version_ids:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "reviewer assessment does not include the reviewed rule version",
            )
        if assessment.provenance.proposer_relationship is not ActorRelationship.INDEPENDENT or any(
            not rule_actors_are_independent(assessment.provenance.actor, actor)
            for actor in authority_actors
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "reviewer must be independent of rule creators and incident reporters",
            )
        return stable

    def project(
        self,
        proposal: ImportReviewerAssessment,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.assessment)


class ConsolidateBehavioralRuleHandler:
    proposal_type = "consolidate_behavioral_rule"

    def build_context(
        self,
        proposal: ConsolidateBehavioralRule,
        reads: HandlerReadCapability,
    ) -> _ConsolidationContext:
        capability = cast(_IntegratorReadCapability, reads)
        consolidation = proposal.consolidation
        candidate = consolidation.candidate_rule
        return _ConsolidationContext(
            active_policy=capability.policy_snapshot(),
            existing_decision=capability.get_decision(consolidation.consolidation_decision_id),
            existing_candidate=capability.get_rule(candidate.rule_version_id),
            assessments=tuple(
                capability.get_assessment(assessment_id)
                for assessment_id in consolidation.assessment_ids
            ),
            incidents=tuple(
                capability.get_incident(incident_id) for incident_id in consolidation.incident_ids
            ),
            predecessors=tuple(
                capability.get_rule(rule_version_id)
                for rule_version_id in candidate.supersedes_rule_version_ids
            ),
            measurement=capability.get_measurement(proposal.measurement_id),
            evaluator_audit=capability.get_evaluator_audit(proposal.evaluator_audit_id),
            current_head=capability.get_head(candidate.rule_id),
        )

    def decide(
        self,
        proposal: ConsolidateBehavioralRule,
        context: _ConsolidationContext,
    ) -> TransactionDecision:
        consolidation = proposal.consolidation
        candidate = consolidation.candidate_rule
        authority_actors = tuple(
            (
                *(item.provenance.actor for item in context.assessments if item is not None),
                *(item.creator for item in context.predecessors if item is not None),
                *(item.reported_by for item in context.incidents if item is not None),
            )
        )
        rejection = rule_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=authority_actors,
            measurement=context.measurement,
            evaluator_audit=context.evaluator_audit,
        )
        if rejection is not None:
            return rejection
        if consolidation.integrated_by != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "only the named integrator may submit a canonical diff",
            )
        if consolidation.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_rejection(proposal.proposal_id, "rule consolidation")
        if context.existing_decision is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.IDEMPOTENCY_CONFLICT,
                "consolidation decision stable key already exists",
            )
        if context.existing_candidate is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.IDEMPOTENCY_CONFLICT,
                "candidate rule stable key already exists",
            )
        if (
            any(item is None for item in context.assessments)
            or any(item is None for item in context.incidents)
            or any(item is None for item in context.predecessors)
            or not context.predecessors
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "consolidation requires every assessment, incident, and predecessor",
            )
        assessments = cast(tuple[ReviewerAssessment, ...], context.assessments)
        predecessors = cast(tuple[BehavioralRuleVersion, ...], context.predecessors)
        if proposal.rollback_rule_version_id not in candidate.supersedes_rule_version_ids:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "rollback version must be one of the retained predecessors",
            )
        if any(
            item.rule_id == candidate.rule_id and item.authority is not candidate.authority
            for item in predecessors
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.PERMISSION_DENIED,
                "consolidation cannot change a predecessor rule authority",
            )
        if candidate.status is not _status_for_action(consolidation.action):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "candidate status does not match the consolidation action",
            )
        if (
            proposal.approval is None
            or candidate.approver != proposal.approval.approver
            or candidate.approved_at is None
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "active candidate must bind the independent approval",
            )
        prior_incident_ids = tuple(
            sorted(
                {incident_id for item in predecessors for incident_id in item.source_incident_ids}
            )
        )
        try:
            rebuilt = build_candidate_diff(
                consolidation_decision_id=consolidation.consolidation_decision_id,
                review_proposal_id=consolidation.review_proposal_id,
                assessments=assessments,
                candidate_rule=candidate,
                regression_cases=consolidation.regression_cases,
                action=consolidation.action,
                recommendation_dispositions=consolidation.recommendation_dispositions,
                separating_variable=consolidation.separating_variable,
                recurrence_incident_ids=consolidation.recurrence_incident_ids,
                recurrence_repairs=consolidation.recurrence_repairs,
                integrator=consolidation.integrated_by,
                integrated_at=consolidation.integrated_at,
                governing_policy_hash=consolidation.governing_policy_hash,
                prior_incident_ids=prior_incident_ids,
                overlap=consolidation.overlap,
            )
        except ValueError:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.UNRESOLVED_RULE_CONFLICT,
                "canonical diff fails role, dissent, conflict, recurrence, "
                "or regression validation",
            )
        if rebuilt != consolidation:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.UNRESOLVED_RULE_CONFLICT,
                "canonical diff does not preserve the exact reviewed history",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: ConsolidateBehavioralRule,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        if not decision.accepted:
            raise ValueError("rejected rule consolidations cannot be projected")
        consolidation = proposal.consolidation
        candidate = consolidation.candidate_rule
        if consolidation.action not in {RuleAction.REJECT, RuleAction.ESCALATE_TO_HUMAN}:
            writes.append_authoritative(candidate)
        writes.append_authoritative(rule_consolidation_decision(proposal))
        if consolidation.action not in {RuleAction.REJECT, RuleAction.ESCALATE_TO_HUMAN}:
            for regression_case in consolidation.regression_cases:
                writes.append_authoritative(regression_case)
            writes.update_projection(candidate)


class RuleService:
    def __init__(self, coordinator: TransactionCoordinator) -> None:
        self._coordinator = coordinator

    def record_incident(self, proposal: RecordRuleIncident) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def propose_rule(self, proposal: ProposeBehavioralRule) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def import_assessment(
        self,
        proposal: ImportReviewerAssessment,
    ) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def consolidate(
        self,
        proposal: ConsolidateBehavioralRule,
    ) -> TransactionDecision:
        return self._coordinator.submit(proposal)


def rule_authority_rejection(
    proposal: RuleMutationProposal,
    snapshot: PolicySnapshot,
    *,
    authority_actors: tuple[ActorIdentity, ...] = (),
    measurement: SelfImprovementMeasurementRecord | None = None,
    evaluator_audit: EvaluatorAuditRecord | None = None,
) -> TransactionDecision | None:
    if proposal.classification != FIXED_RULE_CLASSIFICATION:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "behavioral-rule proposals require the exact fixed classification",
        )
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "behavioral-rule proposals require an active governance policy V2",
        )
    requirement = next(
        (
            item
            for item in policy.adaptation_requirements
            if item.change_target is ChangeTarget.BEHAVIORAL_RULE
            and item.persistence is PersistenceScope.PERSISTENT_RULE
        ),
        None,
    )
    if requirement is None:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "active policy does not govern persistent behavioral rules",
        )
    if (
        requirement.minimum_verification is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        or ExternalGrounding.PRIMARY_SOURCE not in requirement.permitted_grounding
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "active policy does not permit the fixed rule verification and grounding",
        )
    protected_evaluation = measurement is not None and bool(measurement.protected_metrics)
    rollback_present = isinstance(proposal, ConsolidateBehavioralRule) and bool(
        proposal.rollback_rule_version_id
    )
    if requirement.protected_evaluation_required and not protected_evaluation:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "protected evaluation required by active rule policy",
        )
    if requirement.rollback_required and not rollback_present:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "rollback target required by active rule policy",
        )
    approval = proposal.approval
    if (
        approval is None
        or requirement.required_approver_kind is not ActorKind.HUMAN
        or approval.approver.kind is not requirement.required_approver_kind
        or not rule_actors_are_independent(proposal.proposer, approval.approver)
        or any(
            not rule_actors_are_independent(approval.approver, authority_actor)
            for authority_actor in authority_actors
        )
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "behavioral-rule mutation requires independent human approval",
        )
    if isinstance(proposal, ConsolidateBehavioralRule) and not _measurement_and_audit_bind(
        proposal,
        snapshot,
        measurement,
        evaluator_audit,
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "canonical rule consolidation requires a matching accepted measurement "
            "and evaluator audit",
        )
    return None


def _measurement_and_audit_bind(
    proposal: ConsolidateBehavioralRule,
    snapshot: PolicySnapshot,
    measurement: SelfImprovementMeasurementRecord | None,
    audit: EvaluatorAuditRecord | None,
) -> bool:
    candidate = proposal.consolidation.candidate_rule
    approval = proposal.approval
    return bool(
        measurement is not None
        and audit is not None
        and approval is not None
        and measurement.measurement_id == proposal.measurement_id
        and measurement.decision is MeasurementDecision.ACCEPTED
        and measurement.classification == FIXED_RULE_CLASSIFICATION
        and measurement.candidate_version_id == candidate.rule_version_id
        and measurement.baseline_version_id == proposal.rollback_rule_version_id
        and measurement.rollback_target_id == proposal.rollback_rule_version_id
        and measurement.evaluator_audit_id == proposal.evaluator_audit_id
        and measurement.proposer == proposal.proposer
        and measurement.decision_authority == approval.approver
        and measurement.governing_policy_hash == snapshot.policy_hash
        and audit.evaluator_audit_id == proposal.evaluator_audit_id
        and audit.result is AssessmentOutcome.PASSED
        and audit.evaluator == measurement.evaluator
        and audit.evaluator_version == measurement.evaluator_version
        and audit.proposer == proposal.proposer
        and audit.candidate_producer == proposal.proposer
        and audit.governing_policy_hash == snapshot.policy_hash
        and rule_actors_are_independent(audit.auditor, proposal.proposer)
    )


def rule_consolidation_decision(
    proposal: ConsolidateBehavioralRule,
) -> RuleConsolidationDecision:
    consolidation = proposal.consolidation
    producing = consolidation.action not in {
        RuleAction.REJECT,
        RuleAction.ESCALATE_TO_HUMAN,
    }
    accepted = tuple(
        f"{item.assessment_id}:{item.recommended_action.value}: {item.explanation}"
        for item in consolidation.recommendation_dispositions
        if item.accepted
    )
    rejected = tuple(
        f"{item.assessment_id}:{item.recommended_action.value}: {item.explanation}"
        for item in consolidation.recommendation_dispositions
        if not item.accepted
    )
    return RuleConsolidationDecision(
        consolidation_decision_id=consolidation.consolidation_decision_id,
        proposal_id=proposal.proposal_id,
        consumed_assessment_ids=consolidation.assessment_ids,
        consumed_incident_ids=consolidation.incident_ids,
        resulting_rule_version_id=(
            consolidation.candidate_rule.rule_version_id if producing else None
        ),
        action=consolidation.action,
        rationale=(
            "Governed integration retained every review, incident, and recommendation disposition."
        ),
        separating_variable=consolidation.separating_variable,
        decision_boundary=consolidation.candidate_rule.decision_boundary,
        accepted_recommendations=accepted,
        rejected_recommendations=rejected,
        preserved_dissent=consolidation.preserved_dissent,
        decided_by=consolidation.integrated_by,
        decided_at=consolidation.integrated_at,
        governing_policy_hash=consolidation.governing_policy_hash,
    )


def _status_for_action(action: RuleAction) -> RuleStatus:
    if action is RuleAction.QUARANTINE:
        return RuleStatus.QUARANTINED
    if action in {RuleAction.REJECT, RuleAction.ESCALATE_TO_HUMAN}:
        return RuleStatus.REJECTED
    return RuleStatus.ACTIVE


def _stable_record_decision(
    proposal_id: str,
    existing: BaseModel | None,
    candidate: BaseModel,
    label: str,
) -> TransactionDecision:
    if existing is None or existing == candidate:
        return TransactionDecision(proposal_id=proposal_id, accepted=True)
    return _rejected(
        proposal_id,
        RejectionCode.IDEMPOTENCY_CONFLICT,
        f"{label} stable key was reused with changed content",
    )


def _project_accepted(
    decision: TransactionDecision,
    writes: HandlerWriteCapability,
    record: BaseModel,
) -> None:
    if not decision.accepted:
        raise ValueError("rejected behavioral-rule records cannot be projected")
    writes.append_authoritative(record)


def _policy_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.POLICY_HASH_MISMATCH,
        f"{label} must name the exact active governance policy",
    )


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)
