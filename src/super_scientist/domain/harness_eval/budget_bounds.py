from __future__ import annotations

from decimal import Decimal
from typing import NoReturn, Self

from pydantic import Field, field_validator, model_validator

from super_scientist.domain.harness_eval.bounds import (
    MAX_PHASE_A_IDENTIFIER_LENGTH,
    MAX_PHASE_A_ITEMS,
    require_bounded_decimal,
    require_bounded_integer,
    require_canonical_byte_limit,
)
from super_scientist.domain.harness_eval.models import EvaluationBudget
from super_scientist.domain.primitives import StableIdentifier

MAX_EVALUATION_BUDGET_CANONICAL_BYTES = 65_536


def _raise_evaluation_budget_error() -> NoReturn:
    """Raise only after unsafe DTO serialization or validation has unwound."""
    raise ValueError("evaluation budget requires canonical validated budget")


class PhaseAEvaluationBudget(EvaluationBudget):
    """Strict bounded Phase A view of the released harness `EvaluationBudget` DTO."""

    tool_ids: tuple[StableIdentifier, ...] = Field(max_length=MAX_PHASE_A_ITEMS)

    @field_validator("tool_ids", mode="before")
    @classmethod
    def require_bounded_tool_count(cls, value: object) -> object:
        if isinstance(value, (tuple, list)) and len(value) > MAX_PHASE_A_ITEMS:
            raise ValueError("evaluation budget tools exceed bound")
        return value

    @field_validator("model_id", "model_version", "adapter_id")
    @classmethod
    def require_bounded_identity(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_PHASE_A_IDENTIFIER_LENGTH:
            raise ValueError("evaluation budget identifiers exceed bound")
        return value

    @field_validator("tool_ids")
    @classmethod
    def require_bounded_canonical_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_PHASE_A_ITEMS:
            raise ValueError("evaluation budget tools exceed bound")
        if any(len(item) > MAX_PHASE_A_IDENTIFIER_LENGTH for item in value):
            raise ValueError("evaluation budget identifiers exceed bound")
        if value != tuple(sorted(value)):
            raise ValueError("evaluation budget tools must be canonically ordered")
        return value

    @field_validator(
        "attempts",
        "token_limit",
        "reasoning_limit",
        "evaluator_call_limit",
        "human_intervention_limit",
    )
    @classmethod
    def require_bounded_budget_integer(cls, value: int) -> int:
        return require_bounded_integer(value, error="evaluation budget integers exceed bound")

    @field_validator("wall_clock_seconds", "cost_limit")
    @classmethod
    def require_bounded_budget_decimal(cls, value: Decimal) -> Decimal:
        return require_bounded_decimal(value)

    @model_validator(mode="after")
    def require_bounded_outer_record(self) -> Self:
        require_canonical_byte_limit(
            self,
            maximum=MAX_EVALUATION_BUDGET_CANONICAL_BYTES,
            error="evaluation budget canonical bytes exceed bound",
        )
        return self

    @classmethod
    def from_evaluation_budget(cls, budget: EvaluationBudget | Self) -> Self:
        if type(budget) not in (EvaluationBudget, cls):
            _raise_evaluation_budget_error()
        try:
            return cls.model_validate(budget.model_dump(mode="python", warnings=False), strict=True)
        except (AttributeError, TypeError, ValueError):
            pass
        _raise_evaluation_budget_error()


__all__ = ["PhaseAEvaluationBudget"]
