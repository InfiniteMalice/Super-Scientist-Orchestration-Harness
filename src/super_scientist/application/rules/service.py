from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.behavioral_rules.consolidation import (
    build_candidate_diff,
    classify_overlap,
    rule_actors_are_independent,
    semantic_version_increases,
)
from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    OverlapClassification,
    ReviewerAssessment,
    RuleAction,
    RuleAuthority,
    RuleConsolidationDecision,
    RuleIncident,
    RuleRegressionCase,
    RuleStatus,
)
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.evidence_trails.authority import parse_external_grounding
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

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None: ...


class _RuleProposalReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None: ...

    def list_rules(self) -> tuple[BehavioralRuleVersion, ...]: ...

    def get_incident(self, incident_id: str) -> RuleIncident | None: ...

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None: ...


class _AssessmentReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None: ...

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None: ...

    def get_incident(self, incident_id: str) -> RuleIncident | None: ...

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None: ...

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None: ...


class _IntegratorReadCapability(_AssessmentReadCapability, Protocol):
    def get_decision(self, decision_id: str) -> RuleConsolidationDecision | None: ...

    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None: ...

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None: ...

    def get_head(self, rule_id: str) -> tuple[str, str, RuleStatus] | None: ...

    def list_rules(self) -> tuple[BehavioralRuleVersion, ...]: ...

    def list_heads(self) -> tuple[tuple[str, str, str, RuleStatus], ...]: ...


class _IncidentWriteCapability(Protocol):
    def append_incident(self, incident: RuleIncident) -> None: ...


class _RuleProposalWriteCapability(Protocol):
    def append_rule_proposal(self, rule: BehavioralRuleVersion) -> None: ...


class _AssessmentWriteCapability(Protocol):
    def append_assessment(self, assessment: ReviewerAssessment) -> None: ...


class _IntegratorWriteCapability(Protocol):
    def append_rule_version(self, rule: BehavioralRuleVersion) -> None: ...

    def append_decision(self, decision: RuleConsolidationDecision) -> None: ...

    def append_regression(self, regression: RuleRegressionCase) -> None: ...

    def set_rule_head(self, rule: BehavioralRuleVersion) -> None: ...


class _ContextModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class _IncidentContext(_ContextModel):
    active_policy: PolicySnapshot
    existing: RuleIncident | None
    evidence: tuple[EvidenceRecord | None, ...]


class _RuleProposalContext(_ContextModel):
    active_policy: PolicySnapshot
    existing: BehavioralRuleVersion | None
    rules: tuple[BehavioralRuleVersion, ...]
    incidents: tuple[RuleIncident | None, ...]
    evidence: tuple[EvidenceRecord | None, ...]


class _AssessmentContext(_ContextModel):
    active_policy: PolicySnapshot
    existing: ReviewerAssessment | None
    rules: tuple[BehavioralRuleVersion | None, ...]
    incidents: tuple[RuleIncident | None, ...]
    evidence: tuple[EvidenceRecord | None, ...]
    reviewed_proposal: ProposeBehavioralRule | None


class _ConsolidationContext(_ContextModel):
    active_policy: PolicySnapshot
    existing_decision: RuleConsolidationDecision | None
    existing_candidate: BehavioralRuleVersion | None
    rules: tuple[BehavioralRuleVersion, ...]
    heads: tuple[tuple[str, str, str, RuleStatus], ...]
    assessments: tuple[ReviewerAssessment | None, ...]
    incidents: tuple[RuleIncident | None, ...]
    evidence: tuple[EvidenceRecord | None, ...]
    predecessors: tuple[BehavioralRuleVersion | None, ...]
    measurement: SelfImprovementMeasurementRecord | None
    evaluator_audit: EvaluatorAuditRecord | None
    current_head: tuple[str, str, RuleStatus] | None
    reviewed_proposal: ProposeBehavioralRule | None


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
            evidence=tuple(
                capability.get_retained_evidence(evidence_id)
                for evidence_id in proposal.incident.evidence_ids
            ),
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
        evidence_rejection = _primary_evidence_rejection(
            proposal.proposal_id,
            incident.evidence_ids,
            context.evidence,
            "rule incident",
        )
        if evidence_rejection is not None:
            return evidence_rejection
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
        _require_accepted_projection(decision)
        cast(_IncidentWriteCapability, writes).append_incident(proposal.incident)


class ProposeBehavioralRuleHandler:
    proposal_type = "propose_behavioral_rule"

    def build_context(
        self,
        proposal: ProposeBehavioralRule,
        reads: HandlerReadCapability,
    ) -> _RuleProposalContext:
        capability = cast(_RuleProposalReadCapability, reads)
        rule = proposal.rule_version
        incidents = tuple(
            capability.get_incident(incident_id) for incident_id in rule.source_incident_ids
        )
        evidence_ids = _canonical_incident_evidence_ids(incidents)
        return _RuleProposalContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_rule(rule.rule_version_id),
            rules=capability.list_rules(),
            incidents=incidents,
            evidence=tuple(
                capability.get_retained_evidence(evidence_id) for evidence_id in evidence_ids
            ),
        )

    def decide(
        self,
        proposal: ProposeBehavioralRule,
        context: _RuleProposalContext,
    ) -> TransactionDecision:
        rule = proposal.rule_version
        relevant_rules = tuple(
            existing
            for existing in context.rules
            if existing.rule_version_id != rule.rule_version_id
            and (
                existing.rule_id == rule.rule_id
                or classify_overlap(rule, existing) is not OverlapClassification.NON_REDUNDANT
            )
        )
        authority_actors = tuple(
            (
                rule.creator,
                *(item.reported_by for item in context.incidents if item is not None),
                *(item.creator for item in relevant_rules),
                *(item.approver for item in relevant_rules if item.approver is not None),
            )
        )
        rejection = rule_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=authority_actors,
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
        expected_evidence_ids = _canonical_incident_evidence_ids(context.incidents)
        if rule.evidence_ids != expected_evidence_ids:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INSUFFICIENT_GROUNDING,
                "behavioral rule must bind the exact incident evidence",
            )
        evidence_rejection = _primary_evidence_rejection(
            proposal.proposal_id,
            expected_evidence_ids,
            context.evidence,
            "behavioral rule",
        )
        if evidence_rejection is not None:
            return evidence_rejection
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
        _require_accepted_projection(decision)
        cast(_RuleProposalWriteCapability, writes).append_rule_proposal(proposal.rule_version)


class ImportReviewerAssessmentHandler:
    proposal_type = "import_reviewer_assessment"

    def build_context(
        self,
        proposal: ImportReviewerAssessment,
        reads: HandlerReadCapability,
    ) -> _AssessmentContext:
        capability = cast(_AssessmentReadCapability, reads)
        assessment = proposal.assessment
        incidents = tuple(
            capability.get_incident(incident_id) for incident_id in assessment.incident_ids
        )
        evidence_ids = _canonical_incident_evidence_ids(incidents)
        return _AssessmentContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_assessment(assessment.assessment_id),
            rules=tuple(
                capability.get_rule(rule_version_id)
                for rule_version_id in assessment.rule_version_ids
            ),
            incidents=incidents,
            evidence=tuple(
                capability.get_retained_evidence(evidence_id) for evidence_id in evidence_ids
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
        quality_rejection = _assessment_quality_rejection(
            proposal.proposal_id,
            assessment,
            context.incidents,
            context.evidence,
        )
        if quality_rejection is not None:
            return quality_rejection
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
        _require_accepted_projection(decision)
        cast(_AssessmentWriteCapability, writes).append_assessment(proposal.assessment)


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
        incidents = tuple(
            capability.get_incident(incident_id) for incident_id in consolidation.incident_ids
        )
        evidence_ids = _canonical_incident_evidence_ids(incidents)
        return _ConsolidationContext(
            active_policy=capability.policy_snapshot(),
            existing_decision=capability.get_decision(consolidation.consolidation_decision_id),
            existing_candidate=capability.get_rule(candidate.rule_version_id),
            rules=capability.list_rules(),
            heads=capability.list_heads(),
            assessments=tuple(
                capability.get_assessment(assessment_id)
                for assessment_id in consolidation.assessment_ids
            ),
            incidents=incidents,
            evidence=tuple(
                capability.get_retained_evidence(evidence_id) for evidence_id in evidence_ids
            ),
            predecessors=tuple(
                capability.get_rule(rule_version_id)
                for rule_version_id in candidate.supersedes_rule_version_ids
            ),
            measurement=capability.get_measurement(proposal.measurement_id),
            evaluator_audit=capability.get_evaluator_audit(proposal.evaluator_audit_id),
            current_head=capability.get_head(candidate.rule_id),
            reviewed_proposal=capability.reviewed_rule_proposal(consolidation.review_proposal_id),
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
        incidents = cast(tuple[RuleIncident, ...], context.incidents)
        predecessors = cast(tuple[BehavioralRuleVersion, ...], context.predecessors)
        lineage_rejection = _canonical_lineage_rejection(
            proposal,
            context,
            predecessors,
        )
        if lineage_rejection is not None:
            return lineage_rejection
        expected_evidence_ids = _canonical_incident_evidence_ids(context.incidents)
        if candidate.evidence_ids != expected_evidence_ids:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INSUFFICIENT_GROUNDING,
                "candidate rule must bind the exact retained incident evidence",
            )
        evidence_rejection = _primary_evidence_rejection(
            proposal.proposal_id,
            expected_evidence_ids,
            context.evidence,
            "rule consolidation",
        )
        if evidence_rejection is not None:
            return evidence_rejection
        for assessment in assessments:
            assessment_rejection = _assessment_quality_rejection(
                proposal.proposal_id,
                assessment,
                context.incidents,
                context.evidence,
            )
            if assessment_rejection is not None:
                return assessment_rejection
        if candidate.status is not _status_for_action(consolidation.action):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "candidate status does not match the consolidation action",
            )
        if (
            proposal.approval is None
            or candidate.approver != proposal.approval.approver
            or candidate.approved_at != proposal.approval.approved_at
            or candidate.created_at > consolidation.integrated_at
            or consolidation.integrated_at > proposal.approval.approved_at
            or any(
                assessment.provenance.assessed_at > candidate.created_at
                for assessment in assessments
            )
            or any(incident.recorded_at > candidate.created_at for incident in incidents)
            or any(item.created_at > candidate.created_at for item in predecessors)
            or context.measurement is None
            or context.measurement.decided_at > consolidation.integrated_at
            or context.evaluator_audit is None
            or context.evaluator_audit.audited_at > context.measurement.decided_at
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
                separating_boundary_test_id=consolidation.separating_boundary_test_id,
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
        capability = cast(_IntegratorWriteCapability, writes)
        consolidation = proposal.consolidation
        candidate = consolidation.candidate_rule
        if consolidation.action not in {RuleAction.REJECT, RuleAction.ESCALATE_TO_HUMAN}:
            capability.append_rule_version(candidate)
        capability.append_decision(rule_consolidation_decision(proposal))
        if consolidation.action not in {RuleAction.REJECT, RuleAction.ESCALATE_TO_HUMAN}:
            for regression_case in consolidation.regression_cases:
                capability.append_regression(regression_case)
            capability.set_rule_head(candidate)


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
    if (
        isinstance(proposal, ConsolidateBehavioralRule)
        and requirement.protected_evaluation_required
        and not protected_evaluation
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "protected evaluation required by active rule policy",
        )
    if (
        isinstance(proposal, ConsolidateBehavioralRule)
        and requirement.rollback_required
        and not rollback_present
    ):
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


_RULE_AUTHORITY_RANK = {
    authority: index
    for index, authority in enumerate(
        (
            RuleAuthority.CONSTITUTIONAL,
            RuleAuthority.GOVERNANCE,
            RuleAuthority.PROJECT,
            RuleAuthority.DOMAIN,
            RuleAuthority.COMPONENT,
            RuleAuthority.TASK,
            RuleAuthority.RUN_LOCAL,
        )
    )
}


def _canonical_lineage_rejection(
    proposal: ConsolidateBehavioralRule,
    context: _ConsolidationContext,
    predecessors: tuple[BehavioralRuleVersion, ...],
) -> TransactionDecision | None:
    consolidation = proposal.consolidation
    candidate = consolidation.candidate_rule
    rules_by_version = {item.rule_version_id: item for item in context.rules}
    head_rules: dict[str, BehavioralRuleVersion] = {}
    for rule_id, rule_version_id, semantic_version, status in context.heads:
        head_rule = rules_by_version.get(rule_version_id)
        if (
            head_rule is None
            or head_rule.rule_id != rule_id
            or head_rule.semantic_version != semantic_version
            or head_rule.status is not status
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "canonical rule registry contains an unresolved head",
            )
        head_rules[rule_id] = head_rule

    current = head_rules.get(candidate.rule_id)
    if context.current_head is None:
        if current is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "candidate rule head read is inconsistent with the canonical registry",
            )
    elif current is None or context.current_head != (
        current.rule_version_id,
        current.semantic_version,
        current.status,
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "candidate rule does not bind the exact canonical head",
        )

    active_head_rules = tuple(head_rules.values())
    overlaps = {
        item.rule_version_id: classify_overlap(candidate, item) for item in active_head_rules
    }
    if OverlapClassification.EXACT_DUPLICATE in overlaps.values():
        return _rejected(
            proposal.proposal_id,
            RejectionCode.DUPLICATE_RULE,
            "an exact duplicate cannot become a canonical active rule",
        )
    affected_other_heads = tuple(
        item
        for item in active_head_rules
        if item.rule_id != candidate.rule_id
        and overlaps[item.rule_version_id] is not OverlapClassification.NON_REDUNDANT
    )
    if affected_other_heads and consolidation.action is not RuleAction.MERGE_WITH_EXISTING:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.UNRESOLVED_RULE_CONFLICT,
            "overlapping active rule heads require explicit merge consolidation",
        )

    reviewed_rule = (
        None if context.reviewed_proposal is None else context.reviewed_proposal.rule_version
    )
    base = current if current is not None else reviewed_rule
    if base is None:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "first canonical rule version must descend from the reviewed proposal",
        )
    expected_predecessor_ids = {
        base.rule_version_id,
        *(item.rule_version_id for item in affected_other_heads),
    }
    supplied_predecessor_ids = set(candidate.supersedes_rule_version_ids)
    if (
        supplied_predecessor_ids != expected_predecessor_ids
        or {item.rule_version_id for item in predecessors} != expected_predecessor_ids
        or proposal.rollback_rule_version_id != base.rule_version_id
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "candidate lineage must name the exact current and affected active heads",
        )
    if base.rule_id == candidate.rule_id and not semantic_version_increases(
        candidate.semantic_version,
        base.semantic_version,
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "candidate semantic version must increase the canonical predecessor",
        )
    authority_sources = tuple(
        {item.rule_version_id: item for item in (base, *affected_other_heads)}.values()
    )
    if any(
        _RULE_AUTHORITY_RANK[candidate.authority] > _RULE_AUTHORITY_RANK[item.authority]
        for item in authority_sources
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "candidate cannot weaken predecessor constitutional authority",
        )
    return None


def _canonical_incident_evidence_ids(
    incidents: tuple[RuleIncident | None, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            evidence_id
            for incident in incidents
            if incident is not None
            for evidence_id in incident.evidence_ids
        )
    )


def _primary_evidence_rejection(
    proposal_id: str,
    expected_ids: tuple[str, ...],
    evidence: tuple[EvidenceRecord | None, ...],
    label: str,
) -> TransactionDecision | None:
    retained = tuple(item for item in evidence if item is not None)
    if (
        len(retained) != len(expected_ids)
        or tuple(item.evidence_id for item in retained) != expected_ids
        or len({item.evidence_id for item in retained}) != len(expected_ids)
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            f"{label} must resolve every evidence identifier exactly once",
        )
    try:
        primary = all(
            item.verification_state is VerificationState.HASH_VERIFIED
            and parse_external_grounding(item) is ExternalGrounding.PRIMARY_SOURCE
            for item in retained
        )
    except ValueError:
        primary = False
    if not primary:
        return _rejected(
            proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            f"{label} requires hash-verified retained primary-source evidence",
        )
    return None


def _assessment_quality_rejection(
    proposal_id: str,
    assessment: ReviewerAssessment,
    available_incidents: tuple[RuleIncident | None, ...],
    available_evidence: tuple[EvidenceRecord | None, ...],
) -> TransactionDecision | None:
    provenance = assessment.provenance
    expected_check_id = f"{assessment.role.value.lower()}-review"
    if (
        provenance.category is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        or provenance.deterministic_or_learned != "DETERMINISTIC"
        or provenance.result is not AssessmentOutcome.PASSED
        or provenance.meaningful_confidence is not None
        or provenance.checks_run != (expected_check_id,)
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "review assessment requires the exact independent deterministic passed mechanism",
        )
    incidents_by_id = {
        incident.incident_id: incident for incident in available_incidents if incident is not None
    }
    incidents = tuple(incidents_by_id.get(incident_id) for incident_id in assessment.incident_ids)
    if any(incident is None for incident in incidents):
        return _rejected(
            proposal_id,
            RejectionCode.MISSING_ENTITY,
            "review assessment references evidence outside the retained incident set",
        )
    expected_evidence_ids = _canonical_incident_evidence_ids(incidents)
    if provenance.evidence_ids != expected_evidence_ids:
        return _rejected(
            proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "review assessment must bind the exact evidence of its incidents",
        )
    evidence_by_id = {item.evidence_id: item for item in available_evidence if item is not None}
    return _primary_evidence_rejection(
        proposal_id,
        expected_evidence_ids,
        tuple(evidence_by_id.get(evidence_id) for evidence_id in expected_evidence_ids),
        "review assessment",
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


def _require_accepted_projection(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected behavioral-rule records cannot be projected")


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
