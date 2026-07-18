from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    AssessmentProvenance,
    ResourceBudget,
)
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ResearchRunEventType(StrEnum):
    STARTED = "STARTED"
    PAUSED = "PAUSED"
    RESUMED = "RESUMED"
    FINAL_VALIDATION_ACCEPTED = "FINAL_VALIDATION_ACCEPTED"
    FINAL_VALIDATION_REJECTED = "FINAL_VALIDATION_REJECTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunBudgetAllocation(_StrictFrozenModel):
    execution: ResourceBudget
    search: ResourceBudget
    evaluation: ResourceBudget
    judging: ResourceBudget
    human: ResourceBudget


class ResearchRun(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: StableIdentifier
    charter: NonBlankText
    scope: tuple[NonBlankText, ...] = Field(min_length=1)
    creator: ActorIdentity
    created_at: UtcTimestamp
    active_governance_policy_hash: Sha256Hex
    model_configuration_version_id: StableIdentifier | None
    scaffold_configuration_version_id: StableIdentifier | None
    budget_allocation: RunBudgetAllocation
    final_validator: ActorIdentity
    final_validator_version: StableIdentifier
    environment_snapshot_id: StableIdentifier


class ResearchRunEvent(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_event_id: StableIdentifier
    run_id: StableIdentifier
    sequence: int = Field(strict=True, ge=1)
    event_type: ResearchRunEventType
    actor: ActorIdentity
    detail: NonBlankText
    final_validation: AssessmentProvenance | None
    occurred_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_final_event(self) -> ResearchRunEvent:
        if self.event_type is ResearchRunEventType.FINAL_VALIDATION_ACCEPTED:
            if (
                self.final_validation is None
                or self.final_validation.result is not AssessmentOutcome.PASSED
            ):
                raise ValueError("accepted final validation requires a passed assessment")
        elif self.event_type is ResearchRunEventType.FINAL_VALIDATION_REJECTED:
            if self.final_validation is None or self.final_validation.result not in {
                AssessmentOutcome.FAILED,
                AssessmentOutcome.INCONCLUSIVE,
                AssessmentOutcome.ABSTAINED,
            }:
                raise ValueError("rejected final validation requires a non-passed assessment")
        elif self.final_validation is not None:
            raise ValueError("only final-validation events may embed final validation")
        return self
