from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from enum import Enum, StrEnum
from types import UnionType
from typing import Annotated, Any, Literal, Self, Union, cast, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ConsolidationProposal,
    ReviewerAssessment,
    RuleIncident,
)
from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CohortPlan,
    CohortPlanReceiptRef,
    CohortRequest,
    DiversityAssessment,
    ErrorCorrelationRecord,
)
from super_scientist.domain.collaboration import (
    CollaborationSession,
    CollaborationTermination,
    PeerContribution,
    PeerRequest,
    TopologyEvent,
)
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
from super_scientist.domain.harness_eval import (
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
    HarnessExecutionTrace,
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessProtocol,
    RewardHackingFamily,
    RewardHackingFinding,
    RewardObservation,
    RewardValidityAssessment,
    parse_untrusted_harness_execution_trace,
)
from super_scientist.domain.harness_eval.models import (
    CampaignIteration,
    FixedCheckerConfiguration,
    HarnessCampaign,
    HarnessCampaignReport,
    HarnessConfound,
    HarnessDecision,
    HarnessVariant,
    ProtectedCheckerResult,
)
from super_scientist.domain.hypotheses.models import (
    CounterexampleReceiptRef,
    CounterexampleRecord,
    ExecutableModelSpec,
    HypothesisAdmissionDecision,
    HypothesisCandidateReceiptRef,
    HypothesisRevisionReceiptRef,
    HypothesisSpec,
    ModelSpecReceiptRef,
    RevisionRecord,
    SimulationResult,
    SimulationResultReceiptRef,
    VerificationMechanismReceiptRef,
    VerificationMechanismSpec,
    VerificationResult,
    VerificationResultReceiptRef,
)
from super_scientist.domain.hypotheses.models import (
    EvaluatorAuditReceiptRef as HypothesisEvaluatorAuditReceiptRef,
)
from super_scientist.domain.hypotheses.models import (
    SelfImprovementMeasurementReceiptRef as HypothesisMeasurementReceiptRef,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
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
from super_scientist.domain.procedures import (
    CompiledProgressPlanBinding,
    MethodDirectionOutcome,
    OpaqueProcedureCompilationEnvelope,
    ProcedureCompilationReceiptRef,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    CompletionDecision,
    CompletionProposal,
    ProgressPlan,
    ProgressValidationEvent,
    RunCheckpoint,
)
from super_scientist.domain.representations.models import (
    EvaluatorAuditReceiptRef,
    PrimitiveEvaluation,
    PrimitiveEvaluationReceiptRef,
    PrimitiveVersion,
    PrimitiveVersionReceiptRef,
    SelfImprovementMeasurementReceiptRef,
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
    "record_rule_incident",
    "propose_behavioral_rule",
    "import_reviewer_assessment",
    "consolidate_behavioral_rule",
    "propose_primitive_version",
    "record_primitive_evaluation",
    "admit_primitive_version",
    "propose_hypothesis_version",
    "register_executable_model",
    "register_verification_mechanism",
    "record_simulation_result",
    "record_verification_result",
    "record_counterexample",
    "revise_hypothesis",
    "admit_hypothesis",
    "create_harness_campaign",
    "record_harness_iteration",
    "record_harness_protected_result",
    "record_harness_confound",
    "decide_harness_campaign",
    "record_capability_profile",
    "record_cohort_plan",
    "record_diversity_assessment",
    "record_collaboration_session",
    "append_peer_request",
    "append_peer_contribution",
    "append_topology_event",
    "record_collaboration_termination",
    "record_procedure_compilation",
    "record_method_direction_outcome",
    "bind_compiled_progress_plan",
    "record_guidance_evaluation_protocol",
    "append_guidance_evaluation_cell",
    "record_model_harness_protocol",
    "append_model_harness_cell",
    "record_model_harness_analysis",
    "record_harness_execution_trace",
    "record_reward_assessment",
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
    DERIVATION_MISMATCH = "DERIVATION_MISMATCH"
    STALE_REFERENCE = "STALE_REFERENCE"
    COLLABORATION_BOUND_EXCEEDED = "COLLABORATION_BOUND_EXCEEDED"
    INVALID_PROCEDURE = "INVALID_PROCEDURE"
    UNMATCHED_EVALUATION = "UNMATCHED_EVALUATION"
    INVALID_REWARD = "INVALID_REWARD"


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
    proposal_type: Literal["propose_evidence_trail_nodes"] = "propose_evidence_trail_nodes"
    trail_id: StableIdentifier
    trail_version_id: StableIdentifier
    classification: ChangeClassification
    source_receipts: tuple[AddEvidenceReceiptRef, ...] = Field(min_length=1)
    nodes: tuple[EvidenceTrailNode, ...] = Field(min_length=1)


class ProposeEvidenceTrailRelations(ProposalBase):
    proposal_type: Literal["propose_evidence_trail_relations"] = "propose_evidence_trail_relations"
    trail_id: StableIdentifier
    trail_version_id: StableIdentifier
    classification: ChangeClassification
    node_stage_receipt: EvidenceTrailNodeStageReceiptRef
    node_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    nodes_hash: Sha256Hex
    relations: tuple[EvidenceTrailRelation, ...]


class RecordEvidenceTrailVersion(ProposalBase):
    proposal_type: Literal["record_evidence_trail_version"] = "record_evidence_trail_version"
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


class RecordRuleIncident(ProposalBase):
    proposal_type: Literal["record_rule_incident"] = "record_rule_incident"
    classification: ChangeClassification
    incident: RuleIncident


class ProposeBehavioralRule(ProposalBase):
    proposal_type: Literal["propose_behavioral_rule"] = "propose_behavioral_rule"
    classification: ChangeClassification
    rule_version: BehavioralRuleVersion


class ImportReviewerAssessment(ProposalBase):
    proposal_type: Literal["import_reviewer_assessment"] = "import_reviewer_assessment"
    classification: ChangeClassification
    assessment: ReviewerAssessment


class ConsolidateBehavioralRule(ProposalBase):
    proposal_type: Literal["consolidate_behavioral_rule"] = "consolidate_behavioral_rule"
    classification: ChangeClassification
    consolidation: ConsolidationProposal
    measurement_id: StableIdentifier
    evaluator_audit_id: StableIdentifier
    rollback_rule_version_id: StableIdentifier


class ProposePrimitiveVersion(ProposalBase):
    proposal_type: Literal["propose_primitive_version"] = "propose_primitive_version"
    classification: ChangeClassification
    primitive_version: PrimitiveVersion


class RecordPrimitiveEvaluation(ProposalBase):
    proposal_type: Literal["record_primitive_evaluation"] = "record_primitive_evaluation"
    classification: ChangeClassification
    candidate_receipt: PrimitiveVersionReceiptRef
    evaluation: PrimitiveEvaluation


class AdmitPrimitiveVersion(ProposalBase):
    proposal_type: Literal["admit_primitive_version"] = "admit_primitive_version"
    classification: ChangeClassification
    candidate_receipt: PrimitiveVersionReceiptRef
    old_frame_evaluation_receipt: PrimitiveEvaluationReceiptRef
    new_frame_evaluation_receipt: PrimitiveEvaluationReceiptRef
    evaluator_audit_receipt: EvaluatorAuditReceiptRef | None
    measurement_receipt: SelfImprovementMeasurementReceiptRef | None
    rollback_primitive_version_id: StableIdentifier
    integrated_at: UtcTimestamp


class ProposeHypothesisVersion(ProposalBase):
    proposal_type: Literal["propose_hypothesis_version"] = "propose_hypothesis_version"
    classification: ChangeClassification
    hypothesis: HypothesisSpec


class RegisterExecutableModel(ProposalBase):
    proposal_type: Literal["register_executable_model"] = "register_executable_model"
    classification: ChangeClassification
    hypothesis_receipt: HypothesisCandidateReceiptRef
    model_spec: ExecutableModelSpec


class RegisterVerificationMechanism(ProposalBase):
    proposal_type: Literal["register_verification_mechanism"] = "register_verification_mechanism"
    classification: ChangeClassification
    hypothesis_receipt: HypothesisCandidateReceiptRef
    mechanism_spec: VerificationMechanismSpec


class RecordSimulationResult(ProposalBase):
    proposal_type: Literal["record_simulation_result"] = "record_simulation_result"
    classification: ChangeClassification
    hypothesis_receipt: HypothesisCandidateReceiptRef
    model_receipt: ModelSpecReceiptRef
    simulation_result: SimulationResult


class RecordVerificationResult(ProposalBase):
    proposal_type: Literal["record_verification_result"] = "record_verification_result"
    classification: ChangeClassification
    hypothesis_receipt: HypothesisCandidateReceiptRef
    mechanism_receipt: VerificationMechanismReceiptRef
    model_receipt: ModelSpecReceiptRef | None
    simulation_receipts: tuple[SimulationResultReceiptRef, ...]
    verification_result: VerificationResult


class RecordCounterexample(ProposalBase):
    proposal_type: Literal["record_counterexample"] = "record_counterexample"
    classification: ChangeClassification
    hypothesis_receipt: HypothesisCandidateReceiptRef
    model_receipt: ModelSpecReceiptRef | None
    simulation_receipts: tuple[SimulationResultReceiptRef, ...]
    verification_result_receipts: tuple[VerificationResultReceiptRef, ...] = Field(min_length=1)
    counterexample: CounterexampleRecord


class ReviseHypothesis(ProposalBase):
    proposal_type: Literal["revise_hypothesis"] = "revise_hypothesis"
    classification: ChangeClassification
    prior_hypothesis_receipt: HypothesisCandidateReceiptRef
    triggering_result_receipts: tuple[VerificationResultReceiptRef, ...] = Field(min_length=1)
    counterexample_receipts: tuple[CounterexampleReceiptRef, ...]
    resulting_hypothesis: HypothesisSpec
    revision: RevisionRecord


class AdmitHypothesis(ProposalBase):
    proposal_type: Literal["admit_hypothesis"] = "admit_hypothesis"
    classification: ChangeClassification
    hypothesis_receipt: HypothesisCandidateReceiptRef
    model_receipts: tuple[ModelSpecReceiptRef, ...] = Field(min_length=1)
    verification_result_receipts: tuple[VerificationResultReceiptRef, ...] = Field(min_length=1)
    counterexample_search_receipts: tuple[VerificationResultReceiptRef, ...] = Field(min_length=1)
    revision_receipts: tuple[HypothesisRevisionReceiptRef, ...]
    evaluator_audit_receipt: HypothesisEvaluatorAuditReceiptRef
    measurement_receipt: HypothesisMeasurementReceiptRef
    rollback_hypothesis_version_id: StableIdentifier | None
    integrated_at: UtcTimestamp
    admission_decision: HypothesisAdmissionDecision


class CreateHarnessCampaign(ProposalBase):
    proposal_type: Literal["create_harness_campaign"] = "create_harness_campaign"
    campaign: HarnessCampaign


class RecordHarnessIteration(ProposalBase):
    proposal_type: Literal["record_harness_iteration"] = "record_harness_iteration"
    iteration: CampaignIteration
    governing_policy_hash: Sha256Hex


class RecordHarnessProtectedResult(ProposalBase):
    proposal_type: Literal["record_harness_protected_result"] = "record_harness_protected_result"
    observation_id: StableIdentifier
    partition_manifest_id: StableIdentifier
    variant: HarnessVariant
    evaluator_version_id: StableIdentifier
    checker_configuration: FixedCheckerConfiguration
    result: ProtectedCheckerResult
    governing_policy_hash: Sha256Hex


class RecordHarnessConfound(ProposalBase):
    proposal_type: Literal["record_harness_confound"] = "record_harness_confound"
    confound: HarnessConfound


class DecideHarnessCampaign(ProposalBase):
    proposal_type: Literal["decide_harness_campaign"] = "decide_harness_campaign"
    report: HarnessCampaignReport
    decision: HarnessDecision


MAX_GOVERNED_PROPOSAL_IDENTIFIER_LENGTH = 200
MAX_HARNESS_TRACE_RECORD_IDENTIFIER_LENGTH = 200
MAX_PROPOSAL_COLLECTION_ITEMS = 256
MAX_PROPOSAL_RECONSTRUCTION_DEPTH = 128
MAX_PROPOSAL_RECONSTRUCTION_ITEMS = 4_096
MAX_PROPOSAL_JSON_DECIMAL_CHARACTERS = 260

_STABLE_IDENTIFIER_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
)
_LOWER_HEX_ALPHABET = frozenset("0123456789abcdef")
_ACTOR_IDENTITY_STATE_FIELDS = frozenset(
    {
        "actor_id",
        "kind",
        "created_at",
        "provider_id",
        "model_id",
        "adapter_id",
        "configuration_hash",
    }
)
_APPROVAL_STATE_FIELDS = frozenset({"approver", "approved_at"})


def _exact_model_state(
    value: object,
    expected_type: type[BaseModel],
    expected_fields: frozenset[str],
) -> dict[str, object]:
    if type(value) is not expected_type:
        raise ValueError("model boundary requires the exact declared type")
    state = object.__getattribute__(value, "__dict__")
    if type(state) is not dict:
        raise ValueError("model boundary requires exact dictionary state")
    if any(type(key) is not str for key in state):
        raise ValueError("model boundary requires exact built-in string keys")
    if frozenset(state) != expected_fields:
        raise ValueError("model boundary received unexpected instance state")
    return state


def _fresh_governed_identifier(value: object) -> str:
    if type(value) is not str:
        raise ValueError("governed identifier must be an exact built-in string")
    if (
        not value
        or len(value) > MAX_GOVERNED_PROPOSAL_IDENTIFIER_LENGTH
        or any(character not in _STABLE_IDENTIFIER_ALPHABET for character in value)
    ):
        raise ValueError("governed identifier is outside its bounded alphabet")
    return value


def _fresh_domain_record_identifier(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > MAX_GOVERNED_PROPOSAL_IDENTIFIER_LENGTH
        or "\x00" in value
    ):
        raise ValueError("domain record identifier must be exact bounded nonblank NUL-free text")
    return value


def _fresh_utc_timestamp(value: object) -> datetime:
    if type(value) is not datetime:
        raise ValueError("timestamp must be an exact datetime")
    if object.__getattribute__(value, "tzinfo") is not UTC:
        raise ValueError("timestamp must use the trusted UTC timezone")
    return datetime(
        object.__getattribute__(value, "year"),
        object.__getattribute__(value, "month"),
        object.__getattribute__(value, "day"),
        object.__getattribute__(value, "hour"),
        object.__getattribute__(value, "minute"),
        object.__getattribute__(value, "second"),
        object.__getattribute__(value, "microsecond"),
        tzinfo=UTC,
        fold=object.__getattribute__(value, "fold"),
    )


def _fresh_optional_identifier(value: object) -> str | None:
    return None if value is None else _fresh_governed_identifier(value)


def _fresh_optional_sha256(value: object) -> str | None:
    if value is None:
        return None
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _LOWER_HEX_ALPHABET for character in value)
    ):
        raise ValueError("configuration hash must be exact lowercase SHA-256 text")
    return value


def _fresh_actor_identity(value: object) -> ActorIdentity:
    state = _exact_model_state(value, ActorIdentity, _ACTOR_IDENTITY_STATE_FIELDS)
    kind = state["kind"]
    if type(kind) is not ActorKind:
        raise ValueError("actor kind must be an exact ActorKind")
    actor = ActorIdentity.model_validate(
        {
            "actor_id": _fresh_governed_identifier(state["actor_id"]),
            "kind": kind,
            "created_at": _fresh_utc_timestamp(state["created_at"]),
            "provider_id": _fresh_optional_identifier(state["provider_id"]),
            "model_id": _fresh_optional_identifier(state["model_id"]),
            "adapter_id": _fresh_optional_identifier(state["adapter_id"]),
            "configuration_hash": _fresh_optional_sha256(state["configuration_hash"]),
        },
        strict=True,
    )
    if not actor.actor_id:
        raise ValueError("actor_id must not be empty")
    return actor


def _fresh_approval(value: object) -> Approval | None:
    if value is None:
        return None
    state = _exact_model_state(value, Approval, _APPROVAL_STATE_FIELDS)
    return Approval.model_validate(
        {
            "approver": _fresh_actor_identity(state["approver"]),
            "approved_at": _fresh_utc_timestamp(state["approved_at"]),
        },
        strict=True,
    )


BoundedGovernedProposalIdentifier = Annotated[
    StableIdentifier,
    Field(
        max_length=MAX_GOVERNED_PROPOSAL_IDENTIFIER_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]
BoundedDomainRecordIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=MAX_GOVERNED_PROPOSAL_IDENTIFIER_LENGTH),
]
BoundedHarnessTraceRecordIdentifier = Annotated[
    StableIdentifier,
    Field(
        max_length=MAX_HARNESS_TRACE_RECORD_IDENTIFIER_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]


class GovernedProposalBase(ProposalBase):
    proposal_id: BoundedGovernedProposalIdentifier
    idempotency_key: BoundedGovernedProposalIdentifier

    @field_validator("proposal_id", "idempotency_key", mode="before")
    @classmethod
    def require_exact_raw_identifier(cls, value: object) -> str:
        return _fresh_governed_identifier(value)


class RecordCapabilityProfile(GovernedProposalBase):
    proposal_type: Literal["record_capability_profile"] = "record_capability_profile"
    profile: CapabilityProfile


class RecordCohortPlan(GovernedProposalBase):
    proposal_type: Literal["record_cohort_plan"] = "record_cohort_plan"
    request: CohortRequest
    profile_receipts: tuple[CapabilityProfileReceiptRef, ...] = Field(
        min_length=1,
        max_length=MAX_PROPOSAL_COLLECTION_ITEMS,
    )
    plan: CohortPlan


class RecordDiversityAssessment(GovernedProposalBase):
    proposal_type: Literal["record_diversity_assessment"] = "record_diversity_assessment"
    cohort_plan_receipt: CohortPlanReceiptRef
    profile_receipts: tuple[CapabilityProfileReceiptRef, ...] = Field(
        min_length=1,
        max_length=MAX_PROPOSAL_COLLECTION_ITEMS,
    )
    error_correlations: tuple[ErrorCorrelationRecord, ...] = Field(
        max_length=MAX_PROPOSAL_COLLECTION_ITEMS,
    )
    assessment: DiversityAssessment


class RecordCollaborationSession(GovernedProposalBase):
    proposal_type: Literal["record_collaboration_session"] = "record_collaboration_session"
    session: CollaborationSession


class AppendPeerRequest(GovernedProposalBase):
    proposal_type: Literal["append_peer_request"] = "append_peer_request"
    request: PeerRequest


class AppendPeerContribution(GovernedProposalBase):
    proposal_type: Literal["append_peer_contribution"] = "append_peer_contribution"
    contribution: PeerContribution


class AppendTopologyEvent(GovernedProposalBase):
    proposal_type: Literal["append_topology_event"] = "append_topology_event"
    event: TopologyEvent


class RecordCollaborationTermination(GovernedProposalBase):
    proposal_type: Literal["record_collaboration_termination"] = "record_collaboration_termination"
    session_id: BoundedDomainRecordIdentifier
    termination: CollaborationTermination

    @field_validator("session_id", mode="before")
    @classmethod
    def require_exact_raw_session_id(cls, value: object) -> str:
        return _fresh_domain_record_identifier(value)


class RecordProcedureCompilation(GovernedProposalBase):
    proposal_type: Literal["record_procedure_compilation"] = "record_procedure_compilation"
    compilation: OpaqueProcedureCompilationEnvelope


class RecordMethodDirectionOutcome(GovernedProposalBase):
    proposal_type: Literal["record_method_direction_outcome"] = "record_method_direction_outcome"
    compilation_id: BoundedDomainRecordIdentifier
    outcome: MethodDirectionOutcome

    @field_validator("compilation_id", mode="before")
    @classmethod
    def require_exact_raw_compilation_id(cls, value: object) -> str:
        return _fresh_domain_record_identifier(value)


class BindCompiledProgressPlan(GovernedProposalBase):
    proposal_type: Literal["bind_compiled_progress_plan"] = "bind_compiled_progress_plan"
    compilation_receipt: ProcedureCompilationReceiptRef
    binding: CompiledProgressPlanBinding
    plan: ProgressPlan


class RecordGuidanceEvaluationProtocol(GovernedProposalBase):
    proposal_type: Literal["record_guidance_evaluation_protocol"] = (
        "record_guidance_evaluation_protocol"
    )
    protocol: GuidanceEvaluationProtocol


class AppendGuidanceEvaluationCell(GovernedProposalBase):
    proposal_type: Literal["append_guidance_evaluation_cell"] = "append_guidance_evaluation_cell"
    cell: GuidanceEvaluationCell


class RecordModelHarnessProtocol(GovernedProposalBase):
    proposal_type: Literal["record_model_harness_protocol"] = "record_model_harness_protocol"
    protocol: ModelHarnessProtocol


class AppendModelHarnessCell(GovernedProposalBase):
    proposal_type: Literal["append_model_harness_cell"] = "append_model_harness_cell"
    cell: ModelHarnessCell


class RecordModelHarnessAnalysis(GovernedProposalBase):
    proposal_type: Literal["record_model_harness_analysis"] = "record_model_harness_analysis"
    analysis: ModelHarnessAnalysis


class _StrictProposalEnvelopeModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class HarnessTraceRecordMetadata(_StrictProposalEnvelopeModel):
    schema_version: Literal[1] = 1
    received_at: UtcTimestamp
    source_id: BoundedHarnessTraceRecordIdentifier

    @field_validator("source_id", mode="before")
    @classmethod
    def require_exact_raw_source_id(cls, value: object) -> str:
        return _fresh_governed_identifier(value)

    @field_validator("received_at", mode="before")
    @classmethod
    def require_safe_raw_received_at(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if info.mode == "json":
            if type(value) is not str or not value or len(value) > 64:
                raise ValueError("JSON timestamp must be bounded exact text")
            return datetime.fromisoformat(value)
        return _fresh_utc_timestamp(value)

    @field_validator("received_at", mode="after")
    @classmethod
    def require_exact_decoded_received_at(cls, value: datetime) -> datetime:
        return _fresh_utc_timestamp(value)


class HarnessExecutionTraceEnvelope(_StrictProposalEnvelopeModel):
    schema_version: Literal[1] = 1
    metadata: HarnessTraceRecordMetadata
    trace: HarnessExecutionTrace

    @field_validator("trace", mode="before")
    @classmethod
    def decode_strict_json_trace(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if info.mode != "json":
            return value
        if type(value) is not dict or any(type(key) is not str for key in value):
            raise ValueError("JSON trace must decode to an exact object")
        return parse_untrusted_harness_execution_trace(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )


class RecordHarnessExecutionTrace(GovernedProposalBase):
    proposal_type: Literal["record_harness_execution_trace"] = "record_harness_execution_trace"
    envelope: HarnessExecutionTraceEnvelope


class RecordRewardAssessment(GovernedProposalBase):
    proposal_type: Literal["record_reward_assessment"] = "record_reward_assessment"
    observation: RewardObservation
    findings: tuple[RewardHackingFinding, ...] = Field(
        min_length=len(RewardHackingFamily),
        max_length=len(RewardHackingFamily),
    )
    assessment: RewardValidityAssessment

    @model_validator(mode="after")
    def require_exact_assessment_inputs(self) -> Self:
        if (
            self.observation != self.assessment.observation
            or self.findings != self.assessment.findings
        ):
            raise ValueError("reward assessment proposal must bind exact observation and findings")
        return self


GOVERNED_PROPOSAL_CLASSES = (
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordDiversityAssessment,
    RecordCollaborationSession,
    AppendPeerRequest,
    AppendPeerContribution,
    AppendTopologyEvent,
    RecordCollaborationTermination,
    RecordProcedureCompilation,
    RecordMethodDirectionOutcome,
    BindCompiledProgressPlan,
    RecordGuidanceEvaluationProtocol,
    AppendGuidanceEvaluationCell,
    RecordModelHarnessProtocol,
    AppendModelHarnessCell,
    RecordModelHarnessAnalysis,
    RecordHarnessExecutionTrace,
    RecordRewardAssessment,
)

_GOVERNED_PROPOSAL_BY_TYPE = {
    proposal_type.model_fields["proposal_type"].default: proposal_type
    for proposal_type in GOVERNED_PROPOSAL_CLASSES
}


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
    | RecordRuleIncident
    | ProposeBehavioralRule
    | ImportReviewerAssessment
    | ConsolidateBehavioralRule
    | ProposePrimitiveVersion
    | RecordPrimitiveEvaluation
    | AdmitPrimitiveVersion
    | ProposeHypothesisVersion
    | RegisterExecutableModel
    | RegisterVerificationMechanism
    | RecordSimulationResult
    | RecordVerificationResult
    | RecordCounterexample
    | ReviseHypothesis
    | AdmitHypothesis
    | CreateHarnessCampaign
    | RecordHarnessIteration
    | RecordHarnessProtectedResult
    | RecordHarnessConfound
    | DecideHarnessCampaign
    | RecordCapabilityProfile
    | RecordCohortPlan
    | RecordDiversityAssessment
    | RecordCollaborationSession
    | AppendPeerRequest
    | AppendPeerContribution
    | AppendTopologyEvent
    | RecordCollaborationTermination
    | RecordProcedureCompilation
    | RecordMethodDirectionOutcome
    | BindCompiledProgressPlan
    | RecordGuidanceEvaluationProtocol
    | AppendGuidanceEvaluationCell
    | RecordModelHarnessProtocol
    | AppendModelHarnessCell
    | RecordModelHarnessAnalysis
    | RecordHarnessExecutionTrace
    | RecordRewardAssessment
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


_HARNESS_TRACE_METADATA_STATE_FIELDS = frozenset({"schema_version", "received_at", "source_id"})
_REWARD_PROPOSAL_STATE_FIELDS = frozenset(
    {
        "proposal_id",
        "idempotency_key",
        "proposer",
        "approval",
        "proposal_type",
        "observation",
        "findings",
        "assessment",
    }
)


def _fresh_exact_value(
    value: object,
    annotation: object,
    *,
    depth: int = 0,
) -> object:
    if depth > MAX_PROPOSAL_RECONSTRUCTION_DEPTH:
        raise ValueError("proposal reconstruction exceeds its depth bound")
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Annotated:
        return _fresh_exact_value(value, arguments[0], depth=depth + 1)
    if origin in (Union, UnionType):
        for option in arguments:
            try:
                return _fresh_exact_value(value, option, depth=depth + 1)
            except (TypeError, ValueError):
                continue
        raise ValueError("proposal union value has no exact admitted type")
    if origin is Literal:
        if not any(type(value) is type(option) and value == option for option in arguments):
            raise ValueError("proposal literal value is not exact")
        return value
    if annotation is Any:
        if value is None or type(value) in (str, int, float, bool, bytes):
            return value
        raise ValueError("untyped proposal state admits exact primitives only")
    if annotation is type(None):
        if value is not None:
            raise ValueError("proposal value must be None")
        return None
    if annotation is datetime:
        return _fresh_utc_timestamp(value)
    if annotation is Decimal:
        if type(value) is not Decimal:
            raise ValueError("proposal decimal must have the exact declared type")
        return _bounded_json_decimal(str(value))
    if annotation in (str, int, float, bool, bytes):
        if type(value) is not annotation:
            raise ValueError("proposal primitive must have the exact declared type")
        return value
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not annotation:
            raise ValueError("proposal enum must have the exact declared type")
        return value
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        fields = frozenset(annotation.model_fields)
        state = _exact_model_state(value, annotation, fields)
        rebuilt = {
            field_name: _fresh_exact_value(
                state[field_name],
                field.annotation,
                depth=depth + 1,
            )
            for field_name, field in annotation.model_fields.items()
        }
        return annotation.model_validate(rebuilt, strict=True)
    if origin is tuple:
        if type(value) is not tuple or len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS:
            raise ValueError("proposal tuple must be exact and bounded")
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(_fresh_exact_value(item, arguments[0], depth=depth + 1) for item in value)
        if len(value) != len(arguments):
            raise ValueError("fixed proposal tuple has the wrong length")
        return tuple(
            _fresh_exact_value(item, item_type, depth=depth + 1)
            for item, item_type in zip(value, arguments, strict=True)
        )
    if origin is list:
        if type(value) is not list or len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS:
            raise ValueError("proposal list must be exact and bounded")
        item_type = arguments[0] if arguments else Any
        return [_fresh_exact_value(item, item_type, depth=depth + 1) for item in value]
    if origin in (dict, Mapping):
        if type(value) is not dict or len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS:
            raise ValueError("proposal mapping must be exact and bounded")
        if any(type(key) is not str for key in value):
            raise ValueError("proposal mapping keys must be exact built-in strings")
        key_type, item_type = arguments if len(arguments) == 2 else (str, Any)
        return {
            _fresh_exact_value(key, key_type, depth=depth + 1): _fresh_exact_value(
                item,
                item_type,
                depth=depth + 1,
            )
            for key, item in value.items()
        }
    raise ValueError("proposal state contains an unsupported declared type")


def _fresh_harness_trace_metadata(value: object) -> HarnessTraceRecordMetadata:
    state = _exact_model_state(
        value,
        HarnessTraceRecordMetadata,
        _HARNESS_TRACE_METADATA_STATE_FIELDS,
    )
    schema_version = state["schema_version"]
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError("trace metadata schema_version must be exact integer 1")
    received_at = state["received_at"]
    source_id = state["source_id"]
    if type(received_at) is not datetime or type(source_id) is not str:
        raise ValueError("trace metadata fields require exact primitive types")
    return HarnessTraceRecordMetadata(
        schema_version=1,
        received_at=_fresh_utc_timestamp(received_at),
        source_id=_fresh_governed_identifier(source_id),
    )


def _fresh_reward_assessment_proposal(value: object) -> RecordRewardAssessment:
    state = _exact_model_state(
        value,
        RecordRewardAssessment,
        _REWARD_PROPOSAL_STATE_FIELDS,
    )
    proposal_type = state["proposal_type"]
    observation = state["observation"]
    findings = state["findings"]
    assessment = state["assessment"]
    if type(proposal_type) is not str or proposal_type != "record_reward_assessment":
        raise ValueError("reward proposal type must be exact trusted text")
    if type(observation) is not RewardObservation:
        raise ValueError("reward observation must have the exact declared type")
    if type(findings) is not tuple or any(
        type(finding) is not RewardHackingFinding for finding in findings
    ):
        raise ValueError("reward findings must be an exact tuple of exact finding types")
    if type(assessment) is not RewardValidityAssessment:
        raise ValueError("reward assessment must have the exact declared type")
    return RecordRewardAssessment(
        proposal_id=_fresh_governed_identifier(state["proposal_id"]),
        idempotency_key=_fresh_governed_identifier(state["idempotency_key"]),
        proposer=_fresh_actor_identity(state["proposer"]),
        approval=_fresh_approval(state["approval"]),
        proposal_type="record_reward_assessment",
        observation=cast(
            RewardObservation,
            _fresh_exact_value(observation, RewardObservation),
        ),
        findings=tuple(
            cast(
                RewardHackingFinding,
                _fresh_exact_value(finding, RewardHackingFinding),
            )
            for finding in findings
        ),
        assessment=cast(
            RewardValidityAssessment,
            _fresh_exact_value(assessment, RewardValidityAssessment),
        ),
    )


def _invalid_reward_decision(value: object) -> TransactionDecision:
    proposal_id = "invalid-reward-proposal"
    with suppress(AttributeError, MemoryError, RecursionError, TypeError, ValueError):
        state = object.__getattribute__(value, "__dict__")
        if type(state) is not dict or any(type(key) is not str for key in state):
            raise ValueError("unsafe proposal state")
        if "proposal_id" in state:
            proposal_id = _fresh_governed_identifier(state["proposal_id"])
    return TransactionDecision(
        proposal_id=proposal_id,
        accepted=False,
        reasons=(
            RejectionReason(
                code=RejectionCode.INVALID_REWARD,
                message="reward assessment proposal is invalid",
            ),
        ),
    )


def _normalize_untyped_json_value(value: object, *, depth: int) -> object:
    if depth > MAX_PROPOSAL_RECONSTRUCTION_DEPTH:
        raise ValueError("proposal JSON exceeds its depth bound")
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) is list:
        if len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS:
            raise ValueError("proposal JSON array exceeds its item bound")
        return [_normalize_untyped_json_value(item, depth=depth + 1) for item in value]
    if type(value) is dict:
        if len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS or any(
            type(key) is not str for key in value
        ):
            raise ValueError("proposal JSON object exceeds its exact bounded contract")
        return {
            key: _normalize_untyped_json_value(item, depth=depth + 1) for key, item in value.items()
        }
    raise ValueError("proposal JSON contains a non-JSON runtime type")


def _base_json_annotation(annotation: object) -> object:
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _bounded_json_decimal(value: object) -> Decimal:
    if type(value) is not str or not value or len(value) > MAX_PROPOSAL_JSON_DECIMAL_CHARACTERS:
        raise ValueError("proposal JSON decimal must be bounded exact text")
    decimal_value: Decimal | None = None
    with suppress(ArithmeticError, DecimalException, ValueError):
        decimal_value = Decimal(value)
    if decimal_value is None:
        raise ValueError("proposal JSON decimal text is invalid") from None
    return decimal_value


def _normalize_json_proposal_value(
    value: object,
    annotation: object,
    *,
    depth: int = 0,
) -> object:
    if depth > MAX_PROPOSAL_RECONSTRUCTION_DEPTH:
        raise ValueError("proposal JSON exceeds its depth bound")
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Annotated:
        return _normalize_json_proposal_value(value, arguments[0], depth=depth + 1)
    if origin in (Union, UnionType):
        base_options = tuple(_base_json_annotation(option) for option in arguments)
        if value is None and type(None) in base_options:
            return None
        if Decimal in base_options and str in base_options:
            if (
                type(value) is not dict
                or any(type(key) is not str for key in value)
                or frozenset(value) != frozenset({"kind", "value"})
            ):
                raise ValueError("reward JSON value requires an exact tagged object")
            kind = value["kind"]
            tagged_value = value["value"]
            if type(kind) is not str or type(tagged_value) is not str:
                raise ValueError("tagged reward JSON requires exact text")
            if kind == "numeric":
                return _bounded_json_decimal(tagged_value)
            if kind == "categorical":
                return tagged_value
            raise ValueError("tagged reward JSON kind is not admitted")
        for option in arguments:
            try:
                normalized = _normalize_json_proposal_value(
                    value,
                    option,
                    depth=depth + 1,
                )
                return TypeAdapter(option).validate_python(normalized, strict=True)
            except (TypeError, ValueError):
                continue
        raise ValueError("proposal JSON union value has no exact admitted type")
    if origin is Literal:
        if not any(type(value) is type(option) and value == option for option in arguments):
            raise ValueError("proposal JSON literal is not exact")
        return value
    if annotation is Any:
        return _normalize_untyped_json_value(value, depth=depth + 1)
    if annotation is type(None):
        if value is not None:
            raise ValueError("proposal JSON value must be null")
        return None
    if annotation is datetime:
        if type(value) is not str or not value or len(value) > 64:
            raise ValueError("proposal JSON timestamp must be bounded exact text")
        return _fresh_utc_timestamp(datetime.fromisoformat(value))
    if annotation is Decimal:
        return _bounded_json_decimal(value)
    if annotation in (str, int, float, bool):
        if type(value) is not annotation:
            raise ValueError("proposal JSON primitive has the wrong exact type")
        return value
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        enum_members = tuple(annotation)
        if not enum_members:
            raise ValueError("proposal JSON enum must declare a value")
        expected_value_type = type(enum_members[0].value)
        if type(value) is not expected_value_type:
            raise ValueError("proposal JSON enum has the wrong exact value type")
        return annotation(value)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if (
            type(value) is not dict
            or len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS
            or any(type(key) is not str for key in value)
        ):
            raise ValueError("proposal JSON model must be an exact bounded object")
        fields = annotation.model_fields
        return {
            key: (
                _normalize_json_proposal_value(
                    item,
                    fields[key].annotation,
                    depth=depth + 1,
                )
                if key in fields
                else _normalize_untyped_json_value(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if origin is tuple:
        if type(value) is not list or len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS:
            raise ValueError("proposal JSON tuple must be a bounded array")
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(
                _normalize_json_proposal_value(
                    item,
                    arguments[0],
                    depth=depth + 1,
                )
                for item in value
            )
        if len(value) != len(arguments):
            raise ValueError("fixed proposal JSON tuple has the wrong length")
        return tuple(
            _normalize_json_proposal_value(item, item_type, depth=depth + 1)
            for item, item_type in zip(value, arguments, strict=True)
        )
    if origin is list:
        if type(value) is not list or len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS:
            raise ValueError("proposal JSON list must be a bounded array")
        item_type = arguments[0] if arguments else Any
        return [_normalize_json_proposal_value(item, item_type, depth=depth + 1) for item in value]
    if origin is frozenset:
        if type(value) is not list or len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS:
            raise ValueError("proposal JSON frozen set must be a bounded array")
        item_type = arguments[0] if arguments else Any
        return frozenset(
            _normalize_json_proposal_value(item, item_type, depth=depth + 1) for item in value
        )
    if origin in (dict, Mapping):
        if (
            type(value) is not dict
            or len(value) > MAX_PROPOSAL_RECONSTRUCTION_ITEMS
            or any(type(key) is not str for key in value)
        ):
            raise ValueError("proposal JSON mapping must be an exact bounded object")
        key_type, item_type = arguments if len(arguments) == 2 else (str, Any)
        return {
            _normalize_json_proposal_value(key, key_type, depth=depth + 1): (
                _normalize_json_proposal_value(item, item_type, depth=depth + 1)
            )
            for key, item in value.items()
        }
    raise ValueError("proposal JSON contains an unsupported declared type")


def _normalize_governed_proposal_json(value: object) -> object:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError("governed proposal JSON must be an exact object")
    proposal_type = value.get("proposal_type")
    if type(proposal_type) is not str:
        raise ValueError("governed proposal JSON requires an exact type tag")
    proposal_model = _GOVERNED_PROPOSAL_BY_TYPE.get(proposal_type)
    if proposal_model is None:
        return value
    return _normalize_json_proposal_value(value, proposal_model)


MAX_PROPOSAL_BYTES = 8 * 1_024 * 1_024
MAX_PROPOSAL_JSON_DEPTH = 128
MAX_PROPOSAL_JSON_NODES = 4_096
MAX_PROPOSAL_JSON_CONTAINER_ITEMS = 4_096


class ProposalBoundaryValidationError(ValueError):
    """Fixed public error for rejected serialized proposal input."""


PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)


def proposal_json_is_within_depth_limit(value: object) -> bool:
    if type(value) is not str and type(value) is not bytes:
        return False
    try:
        if type(value) is str:
            if len(value) > MAX_PROPOSAL_BYTES:
                return False
            encoded = value.encode("utf-8")
            if len(encoded) > MAX_PROPOSAL_BYTES:
                return False
        elif len(value) > MAX_PROPOSAL_BYTES:
            return False
        decoded = json.loads(value)
    except (
        ArithmeticError,
        MemoryError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        return False

    stack: list[tuple[object, int]] = [(decoded, 0)]
    visited_nodes = 0
    while stack:
        current, depth = stack.pop()
        visited_nodes += 1
        if depth > MAX_PROPOSAL_JSON_DEPTH or visited_nodes > MAX_PROPOSAL_JSON_NODES:
            return False
        if type(current) is dict:
            if len(current) > MAX_PROPOSAL_JSON_CONTAINER_ITEMS or any(
                type(key) is not str for key in current
            ):
                return False
            stack.extend((item, depth + 1) for item in current.values())
        elif type(current) is list:
            if len(current) > MAX_PROPOSAL_JSON_CONTAINER_ITEMS:
                return False
            stack.extend((item, depth + 1) for item in current)
        elif current is not None and type(current) not in (str, int, float, bool):
            return False
    return True


def parse_untrusted_proposal_json(value: str | bytes) -> Proposal:
    proposal: Proposal | None = None
    if type(value) is str or type(value) is bytes:
        with suppress(
            ArithmeticError,
            MemoryError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            serialized_bytes = value.encode("utf-8") if type(value) is str else value
            if len(serialized_bytes) <= MAX_PROPOSAL_BYTES and proposal_json_is_within_depth_limit(
                value
            ):
                decoded = json.loads(value)
                normalized = _normalize_governed_proposal_json(decoded)
                proposal = (
                    PROPOSAL_ADAPTER.validate_json(value)
                    if normalized is decoded
                    else PROPOSAL_ADAPTER.validate_python(normalized, strict=True)
                )
    if proposal is None:
        raise ProposalBoundaryValidationError("transaction proposal failed validation") from None
    return proposal


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
