from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.identity import ActorIdentity, are_independent
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
    ResourceBudget,
    ResourceUsage,
    usage_within_budget,
)
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)
from super_scientist.domain.progress._decimal_math import _require_bounded_decimal


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ProgressStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    PROVISIONALLY_COMPLETE = "PROVISIONALLY_COMPLETE"
    VALIDATED = "VALIDATED"
    INVALIDATED = "INVALIDATED"
    ABANDONED = "ABANDONED"


class ProgressSubtask(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    subtask_id: StableIdentifier
    plan_version_id: StableIdentifier
    description: NonBlankText
    dependency_ids: tuple[StableIdentifier, ...]
    completion_criteria: tuple[NonBlankText, ...] = Field(min_length=1)
    validator: ActorIdentity
    validator_version: StableIdentifier
    weight: Decimal = Field(strict=True, gt=Decimal("0"))
    evidence_requirements: tuple[NonBlankText, ...] = Field(min_length=1)
    order: int = Field(strict=True, ge=1)

    @field_validator("weight")
    @classmethod
    def require_bounded_weight(cls, value: Decimal) -> Decimal:
        return _require_bounded_decimal(value)


class ProgressPlan(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    plan_version_id: StableIdentifier
    run_id: StableIdentifier
    version: int = Field(strict=True, ge=1)
    subtasks: tuple[ProgressSubtask, ...] = Field(min_length=1)
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class ProgressValidationEvent(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    event_id: StableIdentifier
    run_id: StableIdentifier
    plan_version_id: StableIdentifier
    subtask_id: StableIdentifier
    requested_status: ProgressStatus
    completion_proposer: ActorIdentity
    validator: ActorIdentity
    validator_version: StableIdentifier
    validator_category: VerificationLevel
    relationship_to_run_creator: ActorRelationship
    relationship_to_completion_proposer: ActorRelationship
    are_independent: bool
    evidence_ids: tuple[StableIdentifier, ...]
    checks_run: tuple[StableIdentifier, ...] = Field(min_length=1)
    assumptions: tuple[NonBlankText, ...]
    limitations: tuple[NonBlankText, ...]
    result: AssessmentOutcome
    occurred_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def recompute_completion_proposer_independence(self) -> ProgressValidationEvent:
        computed = progress_actors_are_independent(
            self.validator,
            self.completion_proposer,
        )
        if self.are_independent is not computed:
            raise ValueError("declared independent status does not match recomputed independence")
        if (
            not computed
            and self.relationship_to_completion_proposer is ActorRelationship.INDEPENDENT
        ):
            raise ValueError("alias identities cannot claim an independent relationship")
        if self.requested_status is ProgressStatus.VALIDATED and (
            not computed
            or self.relationship_to_run_creator is not ActorRelationship.INDEPENDENT
            or self.relationship_to_completion_proposer is not ActorRelationship.INDEPENDENT
            or self.result is not AssessmentOutcome.PASSED
        ):
            raise ValueError("VALIDATED status requires passed independent validation")
        if self.requested_status is ProgressStatus.VALIDATED and not self.evidence_ids:
            raise ValueError("VALIDATED status requires retained evidence")
        return self


class ProgressSummary(_StrictFrozenModel):
    plan_version_id: StableIdentifier
    total_weight: Decimal
    provisional_weight: Decimal
    official_weight: Decimal
    provisional_subtask_ids: tuple[StableIdentifier, ...]
    validated_subtask_ids: tuple[StableIdentifier, ...]


class FalseFinishResult(StrEnum):
    FALSE_FINISH = "FALSE_FINISH"
    NOT_FALSE_FINISH = "NOT_FALSE_FINISH"


class FalseFinishFinding(_StrictFrozenModel):
    result: FalseFinishResult
    voluntary_termination: bool
    claims_completion: bool
    final_validator_failed: bool
    meaningful_validated_progress: bool
    unused_budget: bool
    reasons: tuple[NonBlankText, ...]


class TerminationReason(StrEnum):
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    EARLY_EXIT = "EARLY_EXIT"
    USER_CANCELLED = "USER_CANCELLED"
    HARNESS_ERROR = "HARNESS_ERROR"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    VALIDATOR_ERROR = "VALIDATOR_ERROR"
    SAFETY_BLOCK = "SAFETY_BLOCK"
    UNRECOVERABLE_STATE = "UNRECOVERABLE_STATE"


class BudgetReserves(_StrictFrozenModel):
    exploration: ResourceBudget
    implementation: ResourceBudget
    verification: ResourceBudget
    recovery: ResourceBudget
    finalization: ResourceBudget


class BudgetUsage(_StrictFrozenModel):
    exploration: ResourceUsage
    implementation: ResourceUsage
    verification: ResourceUsage
    recovery: ResourceUsage
    finalization: ResourceUsage


class ExecutionTelemetry(_StrictFrozenModel):
    episodes: int = Field(strict=True, ge=0)
    model_calls: int = Field(strict=True, ge=0)
    input_tokens: int = Field(strict=True, ge=0)
    output_tokens: int = Field(strict=True, ge=0)
    tool_calls: int = Field(strict=True, ge=0)
    operations: int = Field(strict=True, ge=0)
    files_changed: int = Field(strict=True, ge=0)
    elapsed_seconds: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    verification_seconds: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    repeated_actions: int = Field(strict=True, ge=0)
    reverted_actions: int = Field(strict=True, ge=0)
    checkpoints: int = Field(strict=True, ge=0)
    timed_out: bool
    termination_reason: TerminationReason | None
    estimated_cost_usd: float = Field(strict=True, ge=0.0, allow_inf_nan=False)


class BudgetAllocation(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    budget_id: StableIdentifier
    run_id: StableIdentifier
    plan_version_id: StableIdentifier
    reserves: BudgetReserves
    usage: BudgetUsage
    telemetry: ExecutionTelemetry
    recorded_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def reconcile_usage_with_reserves(self) -> BudgetAllocation:
        for category in BudgetReserves.model_fields:
            if not usage_within_budget(
                getattr(self.usage, category),
                getattr(self.reserves, category),
            ):
                raise ValueError(f"{category} usage exceeds its reserved budget")
        return self


class RunCheckpoint(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    checkpoint_id: StableIdentifier
    run_id: StableIdentifier
    plan_version_id: StableIdentifier
    validated_subtask_ids: tuple[StableIdentifier, ...]
    pending_dependency_ids: tuple[StableIdentifier, ...]
    hypothesis_ids: tuple[StableIdentifier, ...]
    artifact_refs: tuple[ArtifactRef, ...]
    environment_snapshot_id: StableIdentifier
    attempted_operations: tuple[NonBlankText, ...]
    failures: tuple[NonBlankText, ...]
    remaining_budget: BudgetReserves
    next_recommended_action: NonBlankText
    raw_log_refs: tuple[ArtifactRef, ...] = Field(min_length=1)
    raw_transaction_refs: tuple[ArtifactRef, ...] = Field(min_length=1)
    telemetry: ExecutionTelemetry
    occurred_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class CompletionChecklistStep(StrEnum):
    CHARTER_REVIEWED = "CHARTER_REVIEWED"
    DELIVERABLES_ENUMERATED = "DELIVERABLES_ENUMERATED"
    COMPLETION_CRITERIA_CHECKED = "COMPLETION_CRITERIA_CHECKED"
    FINAL_VALIDATOR_RUN = "FINAL_VALIDATOR_RUN"
    FINAL_ARTIFACTS_INSPECTED = "FINAL_ARTIFACTS_INSPECTED"
    UNRESOLVED_ERRORS_SEARCHED = "UNRESOLVED_ERRORS_SEARCHED"
    INTENDED_ACTUAL_COMPARED = "INTENDED_ACTUAL_COMPARED"
    UNCERTAINTY_RECORDED = "UNCERTAINTY_RECORDED"


class CompletionChecklistItem(_StrictFrozenModel):
    step: CompletionChecklistStep
    completed: bool
    detail: NonBlankText
    evidence_ids: tuple[StableIdentifier, ...]


class CompletionProposal(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    completion_proposal_id: StableIdentifier
    run_id: StableIdentifier
    plan_version_id: StableIdentifier
    proposer: ActorIdentity
    voluntary_termination: bool
    claims_completion: bool
    termination_reason: TerminationReason
    checklist: tuple[CompletionChecklistItem, ...]
    final_validation: AssessmentProvenance
    relationship_to_run_creator: ActorRelationship
    relationship_to_completion_proposer: ActorRelationship
    are_independent: bool
    submitted_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_ordered_checklist_and_recomputed_independence(self) -> CompletionProposal:
        _require_ordered_checklist(self.checklist)
        computed = progress_actors_are_independent(
            self.final_validation.actor,
            self.proposer,
        )
        if self.are_independent is not computed:
            raise ValueError("declared independent status does not match recomputed independence")
        if self.final_validation.proposer_relationship is not (
            self.relationship_to_completion_proposer
        ):
            raise ValueError("final validator proposer relationships do not match")
        if (
            not computed
            and self.relationship_to_completion_proposer is ActorRelationship.INDEPENDENT
        ):
            raise ValueError("alias identities cannot claim an independent relationship")
        if self.final_validation.governing_policy_hash != self.governing_policy_hash:
            raise ValueError("final validation must name the proposal governing policy")
        return self


class CompletionDecision(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    completion_decision_id: StableIdentifier
    run_id: StableIdentifier
    plan_version_id: StableIdentifier
    completion_proposal_id: StableIdentifier
    decision_authority: ActorIdentity
    accepted: bool
    checklist: tuple[CompletionChecklistItem, ...]
    final_validator_result: AssessmentOutcome
    false_finish: FalseFinishFinding
    termination_reason: TerminationReason
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_complete_success_decision(self) -> CompletionDecision:
        _require_ordered_checklist(self.checklist)
        if self.accepted and (
            any(not item.completed for item in self.checklist)
            or self.final_validator_result is not AssessmentOutcome.PASSED
            or self.false_finish.result is not FalseFinishResult.NOT_FALSE_FINISH
            or self.termination_reason is not TerminationReason.SUCCESS
        ):
            raise ValueError("accepted completion requires every checklist and validator gate")
        return self


def progress_actors_are_independent(left: ActorIdentity, right: ActorIdentity) -> bool:
    if not are_independent(left, right):
        return False
    return not (
        left.configuration_hash is not None
        and right.configuration_hash is not None
        and left.configuration_hash == right.configuration_hash
    )


def _require_ordered_checklist(checklist: tuple[CompletionChecklistItem, ...]) -> None:
    if tuple(item.step for item in checklist) != tuple(CompletionChecklistStep):
        raise ValueError("completion checklist must contain every ordered step exactly once")
