from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.harness_eval.models import (
    BudgetComparison,
    CampaignIteration,
    HarnessCampaign,
    HarnessCampaignReport,
    HarnessDecision,
    HarnessDecisionStatus,
    HarnessPartition,
    compare_evaluation_budgets,
    harness_campaign_hash,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    EvaluatorAuditRecord,
    MeasurementDecision,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    Approval,
    CreateHarnessCampaign,
    DecideHarnessCampaign,
    RecordHarnessConfound,
    RecordHarnessIteration,
    RecordHarnessProtectedResult,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.domain_records import (
    HarnessBudgetRecord,
    HarnessCampaignRecord,
    HarnessConfoundRecord,
    HarnessDecisionRecord,
    HarnessMetricRecord,
    HarnessObservationRecord,
    HarnessPartitionManifestRecord,
)
from super_scientist.providers.storage.protected_evaluation import ProtectedResultValidator

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
        ProposalHandler,
    )

    type FixedHarnessEvalHandler = ProposalHandler[BaseModel, BaseModel]


def compare_budgets(
    baseline: object,
    candidate: object,
) -> BudgetComparison:
    from super_scientist.domain.harness_eval.models import EvaluationBudget

    if type(baseline) is not EvaluationBudget or type(candidate) is not EvaluationBudget:
        raise TypeError("budget comparison requires exact EvaluationBudget values")
    return compare_evaluation_budgets(baseline, candidate)


def decide_campaign(report: HarnessCampaignReport) -> HarnessDecision:
    if type(report) is not HarnessCampaignReport:
        raise TypeError("campaign decision requires an exact HarnessCampaignReport")
    raw_authority = object.__getattribute__(report, "decision_authority")
    raw_campaign = object.__getattribute__(report, "campaign")
    if (
        type(raw_authority) is not ActorIdentity
        or raw_authority.kind is not ActorKind.HUMAN
        or not are_independent(raw_authority, raw_campaign.candidate_producer)
    ):
        return _decision(report, HarnessDecisionStatus.INCONCLUSIVE, "authority is invalid")
    try:
        validated = HarnessCampaignReport.model_validate(report)
    except (TypeError, ValidationError):
        return _decision(report, HarnessDecisionStatus.INCONCLUSIVE, "report is invalid")
    if validated.rollback is not None:
        return _decision(
            validated,
            HarnessDecisionStatus.ROLLED_BACK,
            "campaign retained an explicit rollback",
            rollback_target_id=validated.rollback.target_harness_version_id,
        )
    if any(item.catastrophic_regression for item in validated.metrics):
        return _decision(
            validated,
            HarnessDecisionStatus.REGRESSION_DETECTED,
            "catastrophic regression cannot be masked by other metrics",
        )
    grouped = {
        partition: tuple(item for item in validated.metrics if item.partition is partition)
        for partition in HarnessPartition
    }
    if any(
        metric.regressed
        for partition in (
            HarnessPartition.HARNESS_REGRESSION_TASKS,
            HarnessPartition.HARNESS_SAFETY_TASKS,
        )
        for metric in grouped[partition]
    ):
        return _decision(
            validated,
            HarnessDecisionStatus.REGRESSION_DETECTED,
            "regression or safety metrics degraded",
        )
    unresolved = tuple(item for item in validated.confounds if not item.resolved)
    if any(not item.comparable for item in validated.budget_comparisons) or unresolved:
        return _decision(
            validated,
            HarnessDecisionStatus.INCONCLUSIVE,
            "unmatched budgets or unresolved confounds remain",
        )
    discovery_gain = all(
        item.improved for item in grouped[HarnessPartition.HARNESS_DISCOVERY_TASKS]
    )
    validation_gain = all(
        item.improved for item in grouped[HarnessPartition.HARNESS_VALIDATION_TASKS]
    )
    transfer_gain = all(item.improved for item in grouped[HarnessPartition.HARNESS_TRANSFER_TASKS])
    if discovery_gain and not transfer_gain:
        return _decision(
            validated,
            HarnessDecisionStatus.BENCHMARK_SPECIFIC,
            "discovery gain did not transfer",
        )
    if not discovery_gain:
        return _decision(validated, HarnessDecisionStatus.INCONCLUSIVE, "no discovery gain")
    if not validation_gain:
        return _decision(validated, HarnessDecisionStatus.DISCOVERY_GAIN, "validation did not gain")
    if not transfer_gain:
        return _decision(validated, HarnessDecisionStatus.VALIDATION_GAIN, "transfer did not gain")
    if validated.admission_requested:
        if validated.evaluator_audit_passed and validated.measurement_accepted:
            return _decision(
                validated,
                HarnessDecisionStatus.ADMITTED,
                "matched protected transfer and safety gates passed",
            )
        return _decision(
            validated,
            HarnessDecisionStatus.INCONCLUSIVE,
            "durable audit or measurement support is unavailable",
        )
    return _decision(
        validated,
        HarnessDecisionStatus.TRANSFER_VALIDATED,
        "matched-budget transfer validation passed",
    )


def campaign_export_bytes(report: HarnessCampaignReport) -> bytes:
    if type(report) is not HarnessCampaignReport:
        raise TypeError("campaign export requires an exact HarnessCampaignReport")
    try:
        validated = HarnessCampaignReport.model_validate(report)
    except (TypeError, ValidationError):
        raise ValueError("campaign export is invalid") from None
    return canonical_json_bytes(validated.model_dump(mode="json"))


class _TransactionSubmitter(Protocol):
    def submit(self, proposal: object) -> TransactionDecision: ...


class HarnessEvaluationService:
    """Coordinator facade; it owns no answer-reader or protected store."""

    __slots__ = ("_coordinator", "_result_validator")

    def __init__(
        self,
        coordinator: _TransactionSubmitter,
        result_validator: ProtectedResultValidator,
    ) -> None:
        if not isinstance(result_validator, ProtectedResultValidator):
            raise TypeError("harness service requires a protected result validator")
        self._coordinator = coordinator
        self._result_validator = result_validator

    def create_campaign(self, proposal: CreateHarnessCampaign) -> TransactionDecision:
        return self._submit_exact(proposal, CreateHarnessCampaign)

    def record_iteration(self, proposal: RecordHarnessIteration) -> TransactionDecision:
        return self._submit_exact(proposal, RecordHarnessIteration)

    def record_protected_result(
        self,
        proposal: RecordHarnessProtectedResult,
    ) -> TransactionDecision:
        if type(proposal) is not RecordHarnessProtectedResult:
            raise TypeError("harness service received an invalid proposal")
        validated = self._result_validator.validate_result(proposal.result)
        normalized = proposal.model_copy(update={"result": validated})
        return self._coordinator.submit(normalized)

    def record_confound(self, proposal: RecordHarnessConfound) -> TransactionDecision:
        return self._submit_exact(proposal, RecordHarnessConfound)

    def decide_campaign(self, proposal: DecideHarnessCampaign) -> TransactionDecision:
        return self._submit_exact(proposal, DecideHarnessCampaign)

    def _submit_exact(self, proposal: BaseModel, expected: type[BaseModel]) -> TransactionDecision:
        if type(proposal) is not expected:
            raise TypeError("harness service received an invalid proposal")
        return self._coordinator.submit(proposal)


class HarnessCampaignReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_campaign(self, campaign_id: str) -> HarnessCampaignRecord | None: ...


class HarnessIterationReadCapability(HarnessCampaignReadCapability, Protocol):
    def get_partition_manifest(
        self,
        partition_manifest_id: str,
    ) -> HarnessPartitionManifestRecord | None: ...

    def get_budget(self, budget_id: str) -> HarnessBudgetRecord | None: ...

    def get_observation(self, observation_id: str) -> HarnessObservationRecord | None: ...


class HarnessProtectedResultReadCapability(HarnessCampaignReadCapability, Protocol):
    def get_partition_manifest(
        self,
        partition_manifest_id: str,
    ) -> HarnessPartitionManifestRecord | None: ...

    def get_observation(self, observation_id: str) -> HarnessObservationRecord | None: ...

    def get_result(self, result_id: str) -> HarnessMetricRecord | None: ...


class HarnessConfoundReadCapability(HarnessCampaignReadCapability, Protocol):
    def get_confound(self, confound_id: str) -> HarnessConfoundRecord | None: ...


class HarnessDecisionReadCapability(HarnessCampaignReadCapability, Protocol):
    def get_decision(self, decision_id: str) -> HarnessDecisionRecord | None: ...

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None: ...

    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None: ...

    def list_iterations(self, campaign_id: str) -> tuple[CampaignIteration, ...]: ...

    def list_partition_manifests(
        self,
        campaign_id: str,
    ) -> tuple[HarnessPartitionManifestRecord, ...]: ...

    def list_budgets(self, campaign_id: str) -> tuple[HarnessBudgetRecord, ...]: ...

    def list_protected_results(
        self,
        campaign_id: str,
    ) -> tuple[RecordHarnessProtectedResult, ...]: ...

    def list_confounds(self, campaign_id: str) -> tuple[HarnessConfoundRecord, ...]: ...

    def list_metrics(self, campaign_id: str) -> tuple[HarnessMetricRecord, ...]: ...


class _CampaignContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing: HarnessCampaignRecord | None


class _IterationContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    campaign: HarnessCampaignRecord | None
    manifest: HarnessPartitionManifestRecord | None
    budget: HarnessBudgetRecord | None
    existing: HarnessObservationRecord | None


class _ResultContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    campaign: HarnessCampaignRecord | None
    manifest: HarnessPartitionManifestRecord | None
    observation: HarnessObservationRecord | None
    existing: HarnessMetricRecord | None


class _ConfoundContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    campaign: HarnessCampaignRecord | None
    existing: HarnessConfoundRecord | None


class _DecisionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    campaign: HarnessCampaignRecord | None
    existing: HarnessDecisionRecord | None
    evaluator_audit: EvaluatorAuditRecord | None
    measurement: SelfImprovementMeasurementRecord | None
    iterations: tuple[CampaignIteration, ...]
    partitions: tuple[HarnessPartitionManifestRecord, ...]
    budgets: tuple[HarnessBudgetRecord, ...]
    protected_results: tuple[RecordHarnessProtectedResult, ...]
    confounds: tuple[HarnessConfoundRecord, ...]
    metrics: tuple[HarnessMetricRecord, ...]


class CreateHarnessCampaignHandler:
    proposal_type = "create_harness_campaign"

    def build_context(
        self,
        proposal: CreateHarnessCampaign,
        reads: HandlerReadCapability,
    ) -> _CampaignContext:
        capability = cast(HarnessCampaignReadCapability, reads)
        return _CampaignContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_campaign(proposal.campaign.campaign_id),
        )

    def decide(
        self,
        proposal: CreateHarnessCampaign,
        context: _CampaignContext,
    ) -> TransactionDecision:
        campaign = proposal.campaign
        policy_rejection = _harness_policy_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            context.active_policy,
            protected_evaluation=all(
                item.protected_content_hash is not None
                for item in campaign.partitions
                if item.partition is not HarnessPartition.HARNESS_DISCOVERY_TASKS
            ),
            rollback_present=bool(campaign.rollback_harness_version_id),
        )
        if policy_rejection is not None:
            return policy_rejection
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "campaign version already exists",
            )
        if campaign.candidate_producer != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "campaign candidate producer must match proposer",
            )
        if proposal.approval is None or campaign.coordinator != proposal.approval.approver:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "campaign coordinator must supply the independent approval",
            )
        if campaign.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "campaign must name the active policy",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: CreateHarnessCampaign,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.campaign)


class RecordHarnessIterationHandler:
    proposal_type = "record_harness_iteration"

    def build_context(
        self,
        proposal: RecordHarnessIteration,
        reads: HandlerReadCapability,
    ) -> _IterationContext:
        capability = cast(HarnessIterationReadCapability, reads)
        iteration = proposal.iteration
        manifest = capability.get_partition_manifest(iteration.partition_manifest_id)
        return _IterationContext(
            active_policy=capability.policy_snapshot(),
            campaign=(None if manifest is None else capability.get_campaign(manifest.campaign_id)),
            manifest=manifest,
            budget=capability.get_budget(iteration.budget_id),
            existing=capability.get_observation(iteration.observation_id),
        )

    def decide(
        self,
        proposal: RecordHarnessIteration,
        context: _IterationContext,
    ) -> TransactionDecision:
        rejection = _support_record_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            context.active_policy,
        )
        if rejection is not None:
            return rejection
        iteration = proposal.iteration
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "campaign observation already exists",
            )
        if context.campaign is None or context.manifest is None or context.budget is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "campaign iteration references unavailable state",
            )
        if proposal.proposer.actor_id != context.campaign.created_by:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.PERMISSION_DENIED,
                "only the campaign coordinator may record iterations",
            )
        if (
            context.manifest.partition is not iteration.partition
            or iteration.task_id not in context.manifest.task_ids
            or context.budget.campaign_id != context.campaign.campaign_id
            or context.budget.variant is not iteration.variant
            or iteration.attempt > context.budget.attempts
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "iteration does not bind the campaign partition, budget, and evaluator",
            )
        if proposal.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "iteration must name the active policy",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordHarnessIteration,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.iteration)


class RecordHarnessProtectedResultHandler:
    proposal_type = "record_harness_protected_result"

    def build_context(
        self,
        proposal: RecordHarnessProtectedResult,
        reads: HandlerReadCapability,
    ) -> _ResultContext:
        capability = cast(HarnessProtectedResultReadCapability, reads)
        result = proposal.result
        return _ResultContext(
            active_policy=capability.policy_snapshot(),
            campaign=capability.get_campaign(result.campaign_id),
            manifest=capability.get_partition_manifest(proposal.partition_manifest_id),
            observation=capability.get_observation(proposal.observation_id),
            existing=capability.get_result(result.result_id),
        )

    def decide(
        self,
        proposal: RecordHarnessProtectedResult,
        context: _ResultContext,
    ) -> TransactionDecision:
        rejection = _support_record_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            context.active_policy,
        )
        if rejection is not None:
            return rejection
        result = proposal.result
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "protected result already exists",
            )
        if context.campaign is None or context.manifest is None or context.observation is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "protected result references unavailable campaign state",
            )
        if proposal.proposer.actor_id != context.campaign.created_by:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.PERMISSION_DENIED,
                "only the campaign coordinator may record protected results",
            )
        if (
            context.manifest.campaign_id != result.campaign_id
            or context.observation.campaign_id != result.campaign_id
            or context.observation.partition_manifest_id != proposal.partition_manifest_id
            or context.observation.task_id != result.task_id
            or context.observation.variant is not proposal.variant
            or context.observation.candidate_output_hash != result.candidate_output_hash
            or context.observation.result_id != result.result_id
            or context.observation.outcome is not result.outcome
            or context.observation.evaluator_version_id != proposal.evaluator_version_id
            or proposal.checker_configuration.checker_id != result.checker_id
            or proposal.checker_configuration.checker_version != result.checker_version
            or proposal.checker_configuration.evaluator_id != context.campaign.evaluator_id
            or proposal.checker_configuration.evaluator_version_id != proposal.evaluator_version_id
            or proposal.checker_configuration.metric_ids
            != tuple(item.metric_id for item in result.metric_values)
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "protected result does not bind its public observation and evaluator",
            )
        if (
            proposal.governing_policy_hash != context.active_policy.policy_hash
            or context.campaign.governing_policy_hash != context.active_policy.policy_hash
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "protected result must name the active policy",
            )
        if result.evaluated_at < context.observation.observed_at:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "protected evaluation cannot precede candidate observation",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordHarnessProtectedResult,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.result)


class RecordHarnessConfoundHandler:
    proposal_type = "record_harness_confound"

    def build_context(
        self,
        proposal: RecordHarnessConfound,
        reads: HandlerReadCapability,
    ) -> _ConfoundContext:
        capability = cast(HarnessConfoundReadCapability, reads)
        return _ConfoundContext(
            active_policy=capability.policy_snapshot(),
            campaign=capability.get_campaign(proposal.confound.campaign_id),
            existing=capability.get_confound(proposal.confound.confound_id),
        )

    def decide(
        self,
        proposal: RecordHarnessConfound,
        context: _ConfoundContext,
    ) -> TransactionDecision:
        rejection = _support_record_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            context.active_policy,
        )
        if rejection is not None:
            return rejection
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "campaign confound already exists",
            )
        if context.campaign is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "campaign confound references an unavailable campaign",
            )
        if proposal.proposer.actor_id != context.campaign.created_by:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.PERMISSION_DENIED,
                "only the campaign coordinator may record confounds",
            )
        if proposal.confound.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "campaign confound must name the active policy",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordHarnessConfound,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.confound)


class DecideHarnessCampaignHandler:
    proposal_type = "decide_harness_campaign"

    def build_context(
        self,
        proposal: DecideHarnessCampaign,
        reads: HandlerReadCapability,
    ) -> _DecisionContext:
        capability = cast(HarnessDecisionReadCapability, reads)
        campaign_id = proposal.report.campaign.campaign_id
        return _DecisionContext(
            active_policy=capability.policy_snapshot(),
            campaign=capability.get_campaign(campaign_id),
            existing=capability.get_decision(proposal.decision.decision_id),
            evaluator_audit=capability.get_evaluator_audit(proposal.report.evaluator_audit_id),
            measurement=capability.get_measurement(proposal.report.measurement_id),
            iterations=tuple(
                sorted(
                    capability.list_iterations(campaign_id),
                    key=lambda item: item.iteration_index,
                )
            ),
            partitions=tuple(
                sorted(
                    capability.list_partition_manifests(campaign_id),
                    key=lambda item: item.partition.value,
                )
            ),
            budgets=tuple(
                sorted(
                    capability.list_budgets(campaign_id),
                    key=lambda item: item.variant.value,
                )
            ),
            protected_results=capability.list_protected_results(campaign_id),
            confounds=capability.list_confounds(campaign_id),
            metrics=capability.list_metrics(campaign_id),
        )

    def decide(
        self,
        proposal: DecideHarnessCampaign,
        context: _DecisionContext,
    ) -> TransactionDecision:
        report = proposal.report
        decision = proposal.decision
        rejection = _support_record_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            context.active_policy,
        )
        if rejection is not None:
            return rejection
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "campaign decision already exists",
            )
        if context.campaign is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "campaign decision references an unavailable campaign",
            )
        if decision != decide_campaign(report):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "campaign decision does not equal the deterministic report decision",
            )
        if (
            decision.authority != proposal.proposer
            or proposal.approval is None
            or proposal.approval.approver != proposal.proposer
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "decision authority must submit and approve the campaign decision",
            )
        if not _campaign_matches_record(
            report.campaign,
            context.campaign,
            context.partitions,
            context.budgets,
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "campaign report does not bind the immutable stored campaign version",
            )
        if report.iterations != context.iterations:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "campaign report omits or reinterprets retained iterations",
            )
        if not _authoritative_metrics_match(report, context):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "campaign metrics do not reconcile to complete protected result lineage",
            )
        if not _report_confounds_match(report, context.confounds):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "campaign report omits or reinterprets retained confounds",
            )
        if decision.admitted and not _admission_support_matches(report, context):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "campaign admission lacks its durable audit and measurement support",
            )
        if decision.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "campaign decision must name the active policy",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: DecideHarnessCampaign,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        if not decision.accepted:
            raise ValueError("rejected proposals cannot be projected")
        from super_scientist.application.transactions.harness_eval import (
            HarnessDecisionCapabilities,
        )

        capability = cast(HarnessDecisionCapabilities, writes)
        capability.append_decision_with_report(proposal.decision, proposal.report)
        capability.update_projection(proposal.decision)


def fixed_harness_eval_handlers() -> tuple[FixedHarnessEvalHandler, ...]:
    return (  # type: ignore[return-value]
        CreateHarnessCampaignHandler(),
        RecordHarnessIterationHandler(),
        RecordHarnessProtectedResultHandler(),
        RecordHarnessConfoundHandler(),
        DecideHarnessCampaignHandler(),
    )


def _decision(
    report: HarnessCampaignReport,
    status: HarnessDecisionStatus,
    rationale: str,
    *,
    rollback_target_id: str | None = None,
) -> HarnessDecision:
    identity = sha256_hex(
        canonical_json_bytes(
            {
                "campaign_id": report.campaign.campaign_id,
                "report": report.model_dump(mode="json"),
                "status": status.value,
            }
        )
    )
    return HarnessDecision(
        decision_id=f"harness-decision-{identity}",
        campaign_id=report.campaign.campaign_id,
        status=status,
        admitted=status is HarnessDecisionStatus.ADMITTED,
        rationale=(rationale,),
        authority=report.decision_authority,
        rollback_target_id=rollback_target_id,
        evaluator_audit_id=report.evaluator_audit_id,
        measurement_id=report.measurement_id,
        decided_at=report.reported_at,
        governing_policy_hash=report.governing_policy_hash,
    )


def _harness_policy_rejection(
    proposal_id: str,
    proposer: ActorIdentity,
    approval: Approval | None,
    snapshot: PolicySnapshot,
    *,
    protected_evaluation: bool,
    rollback_present: bool,
) -> TransactionDecision | None:
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return _rejected(
            proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "harness campaigns require an active governance policy V2",
        )
    requirement = next(
        (
            item
            for item in policy.adaptation_requirements
            if item.change_target is ChangeTarget.ORCHESTRATION
            and item.persistence is PersistenceScope.HARNESS_CODE
        ),
        None,
    )
    if requirement is None:
        return _rejected(
            proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "active policy has no harness-code requirement",
        )
    if _verification_rank(VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK) < _verification_rank(
        requirement.minimum_verification
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "campaign does not meet the active verification threshold",
        )
    if ExternalGrounding.INDEPENDENT_TEST_SUITE not in requirement.permitted_grounding:
        return _rejected(
            proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "campaign grounding is not permitted by the active policy",
        )
    if (
        approval is None
        or approval.approver.kind is not requirement.required_approver_kind
        or not are_independent(proposer, approval.approver)
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "campaign requires independent policy-matched approval",
        )
    if requirement.protected_evaluation_required and not protected_evaluation:
        return _rejected(
            proposal_id,
            RejectionCode.PROTECTED_DATA_ACCESS,
            "campaign lacks protected partition bindings",
        )
    if requirement.rollback_required and not rollback_present:
        return _rejected(
            proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "campaign lacks its required rollback target",
        )
    return None


def _support_record_rejection(
    proposal_id: str,
    proposer: ActorIdentity,
    approval: Approval | None,
    snapshot: PolicySnapshot,
) -> TransactionDecision | None:
    if not isinstance(snapshot.policy, GovernancePolicyV2):
        return _rejected(
            proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "harness records require an active governance policy V2",
        )
    if approval is None or approval.approver != proposer or proposer.kind is not ActorKind.HUMAN:
        return _rejected(
            proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "harness record authority must be the approved human campaign role",
        )
    return None


def _campaign_matches_record(
    campaign: HarnessCampaign,
    record: HarnessCampaignRecord,
    partitions: tuple[HarnessPartitionManifestRecord, ...],
    budgets: tuple[HarnessBudgetRecord, ...],
) -> bool:
    expected_partitions = tuple(
        sorted(
            (
                (
                    item.partition_manifest_id,
                    item.campaign_id,
                    item.campaign_version,
                    item.partition,
                    item.task_ids,
                    item.manifest_hash,
                    item.protected_content_hash,
                    item.created_at,
                    item.governing_policy_hash,
                )
                for item in campaign.partitions
            ),
            key=lambda item: item[3].value,
        )
    )
    actual_partitions = tuple(
        (
            item.partition_manifest_id,
            item.campaign_id,
            item.campaign_version,
            item.partition,
            item.task_ids,
            item.manifest_hash,
            item.protected_content_hash,
            item.created_at,
            item.governing_policy_hash,
        )
        for item in partitions
    )
    expected_budgets = tuple(
        sorted(
            (
                (
                    item.budget_id,
                    campaign.campaign_id,
                    item.variant,
                    sha256_hex(canonical_json_bytes(item.budget.model_dump(mode="json"))),
                    item.budget.model_id,
                    item.budget.model_version,
                    item.budget.adapter_id,
                    item.budget.feedback_mode.value,
                    item.budget.tool_ids,
                    item.budget.attempts,
                    item.budget.token_limit,
                    item.budget.reasoning_limit,
                    item.budget.evaluator_call_limit,
                    item.budget.wall_clock_seconds,
                    item.budget.cost_limit,
                    item.budget.human_intervention_limit,
                    campaign.created_at,
                    campaign.governing_policy_hash,
                )
                for item in campaign.budgets
            ),
            key=lambda item: item[2].value,
        )
    )
    actual_budgets = tuple(
        (
            item.budget_id,
            item.campaign_id,
            item.variant,
            item.budget_hash,
            item.model_id,
            item.model_version,
            item.adapter_id,
            item.feedback_mode,
            item.tool_ids,
            item.attempts,
            item.token_limit,
            item.reasoning_limit,
            item.evaluator_call_limit,
            item.wall_clock_seconds,
            item.cost_limit,
            item.human_intervention_limit,
            item.created_at,
            item.governing_policy_hash,
        )
        for item in budgets
    )
    return (
        campaign.campaign_id == record.campaign_id
        and campaign.version == record.version
        and campaign.variants == record.variants
        and campaign.baseline_variant == record.baseline_variant
        and campaign.candidate_variant == record.candidate_variant
        and campaign.baseline_harness_version_id == record.baseline_harness_version_id
        and campaign.candidate_harness_version_id == record.candidate_harness_version_id
        and campaign.rollback_harness_version_id == record.rollback_harness_version_id
        and campaign.model_id == record.model_id
        and campaign.model_version == record.model_version
        and campaign.adapter_id == record.adapter_id
        and campaign.evaluator.actor_id == record.evaluator_id
        and campaign.evaluator_version_id == record.evaluator_version_id
        and campaign.candidate_producer.actor_id == record.candidate_producer_id
        and campaign.coordinator.actor_id == record.created_by
        and campaign.created_at == record.created_at
        and campaign.governing_policy_hash == record.governing_policy_hash
        and record.canonical_campaign_hash == harness_campaign_hash(campaign)
        and actual_partitions == expected_partitions
        and actual_budgets == expected_budgets
    )


def _authoritative_metrics_match(
    report: HarnessCampaignReport,
    context: _DecisionContext,
) -> bool:
    """Reconstruct every aggregate from accepted observations and protected results."""

    iterations_by_result: dict[str, CampaignIteration] = {}
    for iteration in context.iterations:
        if iteration.result_id is None:
            return False
        if iteration.result_id in iterations_by_result:
            return False
        iterations_by_result[iteration.result_id] = iteration

    proposals_by_result: dict[str, RecordHarnessProtectedResult] = {}
    for proposal in context.protected_results:
        result_id = proposal.result.result_id
        if result_id in proposals_by_result:
            return False
        proposals_by_result[result_id] = proposal

    records_by_result: dict[str, HarnessMetricRecord] = {}
    for record in context.metrics:
        if record.result_id in records_by_result:
            return False
        records_by_result[record.result_id] = record

    result_ids = set(iterations_by_result)
    if result_ids != set(proposals_by_result) or result_ids != set(records_by_result):
        return False

    manifest_by_id = {item.partition_manifest_id: item for item in context.partitions}
    groups: dict[
        tuple[HarnessPartition, str],
        list[tuple[CampaignIteration, RecordHarnessProtectedResult, Decimal, bool]],
    ] = defaultdict(list)
    for result_id, iteration in iterations_by_result.items():
        proposal = proposals_by_result[result_id]
        result = proposal.result
        checker = proposal.checker_configuration
        stored = records_by_result[result_id]
        manifest = manifest_by_id.get(iteration.partition_manifest_id)
        if (
            manifest is None
            or iteration.task_id not in manifest.task_ids
            or iteration.partition is not manifest.partition
            or proposal.observation_id != iteration.observation_id
            or proposal.partition_manifest_id != iteration.partition_manifest_id
            or proposal.variant is not iteration.variant
            or proposal.evaluator_version_id != iteration.evaluator_version_id
            or result.campaign_id != report.campaign.campaign_id
            or result.task_id != iteration.task_id
            or result.result_id != iteration.result_id
            or result.candidate_output_hash != iteration.candidate_output_hash
            or result.outcome is not iteration.outcome
            or checker.checker_id != result.checker_id
            or checker.checker_version != result.checker_version
            or checker.evaluator_id != report.campaign.evaluator.actor_id
            or checker.evaluator_version_id != iteration.evaluator_version_id
            or checker.metric_ids != tuple(item.metric_id for item in result.metric_values)
            or not _metric_record_matches_result(stored, result)
        ):
            return False
        for index, value in enumerate(result.metric_values):
            groups[(iteration.partition, value.metric_id)].append(
                (
                    iteration,
                    proposal,
                    value.value,
                    checker.metric_higher_is_better[index],
                )
            )

    report_by_key = {(item.partition, item.metric_id): item for item in report.metrics}
    if set(report_by_key) != set(groups):
        return False
    for key, evidence in groups.items():
        metric = report_by_key[key]
        ordered = sorted(evidence, key=lambda item: item[0].iteration_index)
        baseline_values = [
            value
            for iteration, _, value, _ in ordered
            if iteration.variant is report.campaign.baseline_variant
        ]
        candidate_values = [
            value
            for iteration, _, value, _ in ordered
            if iteration.variant is report.campaign.candidate_variant
        ]
        evaluator_versions = {item[0].evaluator_version_id for item in ordered}
        configuration_hashes = {
            item[1].checker_configuration.configuration_hash for item in ordered
        }
        metric_directions = {item[3] for item in ordered}
        if (
            not baseline_values
            or not candidate_values
            or len(evaluator_versions) != 1
            or len(configuration_hashes) != 1
            or len(metric_directions) != 1
        ):
            return False
        catastrophic = any(
            iteration.variant is report.campaign.candidate_variant
            and (iteration.negative_result or iteration.outcome is not AssessmentOutcome.PASSED)
            for iteration, _, _, _ in ordered
        )
        if (
            metric.result_ids != tuple(item[1].result.result_id for item in ordered)
            or metric.baseline_value
            != sum(baseline_values, start=Decimal(0)) / len(baseline_values)
            or metric.candidate_value
            != sum(candidate_values, start=Decimal(0)) / len(candidate_values)
            or metric.higher_is_better is not next(iter(metric_directions))
            or metric.catastrophic_regression != catastrophic
            or metric.evaluator_version_id != next(iter(evaluator_versions))
        ):
            return False
    return True


def _metric_record_matches_result(
    record: HarnessMetricRecord,
    result: object,
) -> bool:
    from super_scientist.domain.harness_eval.models import ProtectedCheckerResult

    if type(result) is not ProtectedCheckerResult:
        return False
    return (
        record.result_id == result.result_id
        and record.campaign_id == result.campaign_id
        and record.task_id == result.task_id
        and record.expected_output_hash == result.expected_output_hash
        and record.candidate_output_hash == result.candidate_output_hash
        and record.checker_id == result.checker_id
        and record.checker_version == result.checker_version
        and record.outcome is result.outcome
        and tuple((item.metric_id, item.value) for item in record.metric_values)
        == tuple((item.metric_id, item.value) for item in result.metric_values)
        and record.evaluated_at == result.evaluated_at
    )


def _report_confounds_match(
    report: HarnessCampaignReport,
    stored: tuple[HarnessConfoundRecord, ...],
) -> bool:
    expected = tuple(
        (
            item.confound_id,
            item.code.value,
            item.description,
            item.affected_variant,
            item.resolved,
            item.independent_analysis_id,
            item.recorded_at,
            item.governing_policy_hash,
        )
        for item in report.confounds
    )
    actual = tuple(
        (
            item.confound_id,
            item.code,
            item.description,
            item.affected_variant,
            item.resolved,
            item.independent_analysis_id,
            item.recorded_at,
            item.governing_policy_hash,
        )
        for item in stored
    )
    return expected == actual


def _admission_support_matches(
    report: HarnessCampaignReport,
    context: _DecisionContext,
) -> bool:
    audit = context.evaluator_audit
    measurement = context.measurement
    metric_ids = {item.result_id for item in context.metrics}
    report_metric_ids = {result_id for metric in report.metrics for result_id in metric.result_ids}
    measurement_metric_ids = (
        set() if measurement is None else {item.source_id for item in measurement.protected_metrics}
    )
    return (
        report.evaluator_audit_passed
        and report.measurement_accepted
        and audit is not None
        and audit.result is AssessmentOutcome.PASSED
        and audit.evaluator == report.campaign.evaluator
        and audit.evaluator_version == report.campaign.evaluator_version_id
        and audit.candidate_producer == report.campaign.candidate_producer
        and set(audit.evidence_ids) == metric_ids
        and audit.governing_policy_hash == report.governing_policy_hash
        and measurement is not None
        and measurement.decision is MeasurementDecision.ACCEPTED
        and measurement.evaluator == report.campaign.evaluator
        and measurement.evaluator_version == report.campaign.evaluator_version_id
        and measurement.evaluator_audit_id == report.evaluator_audit_id
        and measurement.proposer == report.campaign.candidate_producer
        and measurement.baseline_version_id == report.campaign.baseline_harness_version_id
        and measurement.candidate_version_id == report.campaign.candidate_harness_version_id
        and measurement.rollback_target_id == report.campaign.rollback_harness_version_id
        and measurement.governing_policy_hash == report.governing_policy_hash
        and report_metric_ids == metric_ids
        and measurement_metric_ids == metric_ids
    )


def _project_accepted(
    decision: TransactionDecision,
    writes: HandlerWriteCapability,
    record: BaseModel,
) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")
    writes.append_authoritative(record)


def _verification_rank(level: VerificationLevel) -> int:
    return {
        VerificationLevel.MODEL_LIKELIHOOD: 0,
        VerificationLevel.MODEL_CONFIDENCE: 0,
        VerificationLevel.SELF_CONSISTENCY: 0,
        VerificationLevel.SELF_CRITIQUE: 1,
        VerificationLevel.CROSS_MODEL_AGREEMENT: 1,
        VerificationLevel.RUBRIC_JUDGE: 2,
        VerificationLevel.INDEPENDENT_LEARNED_JUDGE: 3,
        VerificationLevel.EXECUTION_FEEDBACK: 4,
        VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK: 5,
        VerificationLevel.EXTERNAL_EMPIRICAL_MEASUREMENT: 6,
        VerificationLevel.FORMAL_VERIFIER: 7,
    }[level]


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)


__all__ = [
    "HarnessEvaluationService",
    "campaign_export_bytes",
    "compare_budgets",
    "decide_campaign",
    "fixed_harness_eval_handlers",
]
