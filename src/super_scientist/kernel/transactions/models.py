from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.evidence_trails.models import (
    AddEvidenceReceiptRef,
    EvidenceTrailNode,
    EvidenceTrailNodeStageReceiptRef,
    EvidenceTrailRelation,
    EvidenceTrailSnapshot,
    EvidenceTrailVersion,
    ReportSentenceBinding,
    TrailAssessment,
    TrailCheckResult,
)
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.improvement.models import (
    ChangeClassification,
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    CompletionDecision,
    CompletionProposal,
    ProgressPlan,
    ProgressValidationEvent,
    RunCheckpoint,
)
from super_scientist.domain.research_runs.models import ResearchRun, ResearchRunEvent

type ProposalKind = Literal[
    "add_evidence",
    "propose_claim",
    "transition_claim",
    "create_research_run",
    "append_research_run_event",
    "record_configuration_version",
    "record_evaluator_audit",
    "record_self_improvement_measurement",
    "propose_evaluator_version",
    "decide_evaluator_succession",
    "propose_governance_policy_transition",
    "record_progress_plan",
    "append_progress_event",
    "record_run_budget",
    "record_run_checkpoint",
    "decide_completion",
    "propose_evidence_trail_nodes",
    "propose_evidence_trail_relations",
    "record_evidence_trail_version",
    "bind_report_sentence",
]


class RejectionCode(StrEnum):
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    ENTITY_ID_MISMATCH = "ENTITY_ID_MISMATCH"
    ENTITY_ALREADY_EXISTS = "ENTITY_ALREADY_EXISTS"
    SELF_APPROVAL = "SELF_APPROVAL"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    EVIDENCE_HASH_MISMATCH = "EVIDENCE_HASH_MISMATCH"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    INDEPENDENT_REVIEW_REQUIRED = "INDEPENDENT_REVIEW_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
    MISSING_ENTITY = "MISSING_ENTITY"
    INVALID_LINEAGE = "INVALID_LINEAGE"
    INSUFFICIENT_GROUNDING = "INSUFFICIENT_GROUNDING"
    PROHIBITED_CLOSED_LOOP = "PROHIBITED_CLOSED_LOOP"
    UNMATCHED_BUDGETS = "UNMATCHED_BUDGETS"
    PROTECTED_DATA_ACCESS = "PROTECTED_DATA_ACCESS"
    STALE_HANDBOOK_MAPPING = "STALE_HANDBOOK_MAPPING"
    INVALID_DEPENDENCY = "INVALID_DEPENDENCY"
    FALSE_FINISH = "FALSE_FINISH"
    CIRCULAR_EVALUATOR_APPROVAL = "CIRCULAR_EVALUATOR_APPROVAL"
    BENCHMARK_SPECIFIC_ADMISSION = "BENCHMARK_SPECIFIC_ADMISSION"
    DUPLICATE_RULE = "DUPLICATE_RULE"
    UNRESOLVED_RULE_CONFLICT = "UNRESOLVED_RULE_CONFLICT"
    EXPERIMENTAL_PRIMITIVE_QUARANTINED = "EXPERIMENTAL_PRIMITIVE_QUARANTINED"


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    approver: ActorIdentity
    approved_at: UtcTimestamp


class ProposalAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_id: StableIdentifier
    idempotency_key: StableIdentifier
    proposer: ActorIdentity
    proposal_kind: ProposalKind
    intent_digest: Sha256Hex


class ProposalBase(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_id: StableIdentifier
    idempotency_key: StableIdentifier
    proposer: ActorIdentity
    approval: Approval | None = None


class AddEvidence(ProposalBase):
    proposal_type: Literal["add_evidence"] = "add_evidence"
    evidence: EvidenceRecord

    @field_serializer("evidence", when_used="json")
    def serialize_evidence(self, evidence: EvidenceRecord) -> object:
        return _json_compatible(evidence.model_dump(warnings="none"))


class ProposeClaim(ProposalBase):
    proposal_type: Literal["propose_claim"] = "propose_claim"
    claim: AtomicClaim


class TransitionClaim(ProposalBase):
    proposal_type: Literal["transition_claim"] = "transition_claim"
    next_claim: AtomicClaim


class CreateResearchRun(ProposalBase):
    proposal_type: Literal["create_research_run"] = "create_research_run"
    run: ResearchRun


class AppendResearchRunEvent(ProposalBase):
    proposal_type: Literal["append_research_run_event"] = "append_research_run_event"
    event: ResearchRunEvent


class RecordConfigurationVersion(ProposalBase):
    proposal_type: Literal["record_configuration_version"] = "record_configuration_version"
    configuration_version: ConfigurationVersion
    classification: ChangeClassification


class RecordEvaluatorAudit(ProposalBase):
    proposal_type: Literal["record_evaluator_audit"] = "record_evaluator_audit"
    evaluator_audit: EvaluatorAuditRecord


class RecordSelfImprovementMeasurement(ProposalBase):
    proposal_type: Literal["record_self_improvement_measurement"] = (
        "record_self_improvement_measurement"
    )
    measurement: SelfImprovementMeasurementRecord


class ProposeEvaluatorVersion(ProposalBase):
    proposal_type: Literal["propose_evaluator_version"] = "propose_evaluator_version"
    evaluator_version: EvaluatorVersion
    classification: ChangeClassification


class DecideEvaluatorSuccession(ProposalBase):
    proposal_type: Literal["decide_evaluator_succession"] = "decide_evaluator_succession"
    succession_decision: EvaluatorSuccessionDecision
    classification: ChangeClassification


class ProposeGovernancePolicyTransition(ProposalBase):
    proposal_type: Literal["propose_governance_policy_transition"] = (
        "propose_governance_policy_transition"
    )
    research_run: ResearchRun
    evaluator_audit: EvaluatorAuditRecord
    measurement: SelfImprovementMeasurementRecord
    candidate_policy_snapshot: PolicySnapshot
    prior_policy_hash: Sha256Hex
    rollback_policy_hash: Sha256Hex
    classification: ChangeClassification


class RecordProgressPlan(ProposalBase):
    proposal_type: Literal["record_progress_plan"] = "record_progress_plan"
    plan: ProgressPlan


class AppendProgressEvent(ProposalBase):
    proposal_type: Literal["append_progress_event"] = "append_progress_event"
    event: ProgressValidationEvent


class RecordRunBudget(ProposalBase):
    proposal_type: Literal["record_run_budget"] = "record_run_budget"
    budget: BudgetAllocation


class RecordRunCheckpoint(ProposalBase):
    proposal_type: Literal["record_run_checkpoint"] = "record_run_checkpoint"
    checkpoint: RunCheckpoint


class DecideCompletion(ProposalBase):
    proposal_type: Literal["decide_completion"] = "decide_completion"
    completion_proposal: CompletionProposal
    completion_decision: CompletionDecision


class ProposeEvidenceTrailNodes(ProposalBase):
    proposal_type: Literal["propose_evidence_trail_nodes"] = (
        "propose_evidence_trail_nodes"
    )
    trail_id: StableIdentifier
    trail_version_id: StableIdentifier
    classification: ChangeClassification
    source_receipts: tuple[AddEvidenceReceiptRef, ...] = Field(min_length=1)
    nodes: tuple[EvidenceTrailNode, ...] = Field(min_length=1)


class ProposeEvidenceTrailRelations(ProposalBase):
    proposal_type: Literal["propose_evidence_trail_relations"] = (
        "propose_evidence_trail_relations"
    )
    trail_id: StableIdentifier
    trail_version_id: StableIdentifier
    classification: ChangeClassification
    node_stage_receipt: EvidenceTrailNodeStageReceiptRef
    node_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    nodes_hash: Sha256Hex
    relations: tuple[EvidenceTrailRelation, ...]


class RecordEvidenceTrailVersion(ProposalBase):
    proposal_type: Literal["record_evidence_trail_version"] = (
        "record_evidence_trail_version"
    )
    trail_version: EvidenceTrailVersion
    nodes: tuple[EvidenceTrailNode, ...] = Field(min_length=1)
    relations: tuple[EvidenceTrailRelation, ...]
    checks: tuple[TrailCheckResult, ...] = Field(min_length=1)
    assessments: tuple[TrailAssessment, ...] = Field(min_length=1)

    def snapshot(self) -> EvidenceTrailSnapshot:
        return EvidenceTrailSnapshot(
            version=self.trail_version,
            nodes=self.nodes,
            relations=self.relations,
            checks=self.checks,
            assessments=self.assessments,
        )


class BindReportSentence(ProposalBase):
    proposal_type: Literal["bind_report_sentence"] = "bind_report_sentence"
    binding: ReportSentenceBinding


class InvalidProposal(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_type: Literal["invalid_proposal"] = "invalid_proposal"
    proposal_id: StableIdentifier
    idempotency_key: StableIdentifier
    validation_error: NonBlankText
    proposer: ActorIdentity | None = None
    attempted_proposal_kind: ProposalKind | None = None


Proposal = Annotated[
    AddEvidence
    | ProposeClaim
    | TransitionClaim
    | CreateResearchRun
    | AppendResearchRunEvent
    | RecordConfigurationVersion
    | RecordEvaluatorAudit
    | RecordSelfImprovementMeasurement
    | ProposeEvaluatorVersion
    | DecideEvaluatorSuccession
    | ProposeGovernancePolicyTransition
    | RecordProgressPlan
    | AppendProgressEvent
    | RecordRunBudget
    | RecordRunCheckpoint
    | DecideCompletion
    | ProposeEvidenceTrailNodes
    | ProposeEvidenceTrailRelations
    | RecordEvidenceTrailVersion
    | BindReportSentence
    | InvalidProposal,
    Field(discriminator="proposal_type"),
]


class RejectionReason(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: RejectionCode
    message: NonBlankText


class TransactionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_id: StableIdentifier
    accepted: bool
    replayed: bool = False
    reasons: tuple[RejectionReason, ...] = ()

    @model_validator(mode="after")
    def validate_reason_state(self) -> TransactionDecision:
        if self.accepted and self.reasons:
            raise ValueError("accepted decisions must not include rejection reasons")
        if not self.accepted and not self.reasons:
            raise ValueError("rejected decisions must include at least one rejection reason")
        return self


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, frozenset):
        compatible = [_json_compatible(item) for item in value]
        return sorted(
            compatible,
            key=lambda item: json.dumps(
                item,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    return value
