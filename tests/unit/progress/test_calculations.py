from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext, localcontext

import pytest
from pydantic import ValidationError
from test_progress_models import _telemetry

from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    ResourceBudget,
    ResourceUsage,
)
from super_scientist.domain.progress.calculations import calculate_progress, remaining_budget
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    BudgetReserves,
    BudgetUsage,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressValidationEvent,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
POLICY_HASH = "a" * 64


def _actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=NOW)


def _plan() -> ProgressPlan:
    validator = _actor("validator")
    return ProgressPlan(
        plan_version_id="plan-1",
        run_id="run-1",
        version=1,
        subtasks=(
            ProgressSubtask(
                subtask_id="collect",
                plan_version_id="plan-1",
                description="Collect primary evidence",
                dependency_ids=(),
                completion_criteria=("Sources retained",),
                validator=validator,
                validator_version="validator-v1",
                weight=Decimal("0.40"),
                evidence_requirements=("primary-source",),
                order=1,
            ),
            ProgressSubtask(
                subtask_id="analyze",
                plan_version_id="plan-1",
                description="Analyze retained evidence",
                dependency_ids=("collect",),
                completion_criteria=("Analysis independently checked",),
                validator=validator,
                validator_version="validator-v1",
                weight=Decimal("0.60"),
                evidence_requirements=("analysis-artifact",),
                order=2,
            ),
        ),
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


def _mixed_scale_plan() -> ProgressPlan:
    validator = _actor("validator")
    weights = (Decimal("0.1400"), Decimal("0.14"), Decimal("0.720"))
    return ProgressPlan(
        plan_version_id="mixed-plan",
        run_id="run-1",
        version=1,
        subtasks=tuple(
            ProgressSubtask(
                subtask_id=f"step-{index}",
                plan_version_id="mixed-plan",
                description=f"Complete step {index}",
                dependency_ids=(() if index == 1 else (f"step-{index - 1}",)),
                completion_criteria=("Independently accepted",),
                validator=validator,
                validator_version="validator-v1",
                weight=weight,
                evidence_requirements=("retained-evidence",),
                order=index,
            )
            for index, weight in enumerate(weights, start=1)
        ),
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


def _event(
    event_id: str,
    subtask_id: str,
    status: ProgressStatus,
    occurred_at: datetime,
) -> ProgressValidationEvent:
    proposer = _actor("proposer")
    return ProgressValidationEvent(
        event_id=event_id,
        run_id="run-1",
        plan_version_id="plan-1",
        subtask_id=subtask_id,
        requested_status=status,
        completion_proposer=proposer,
        validator=_actor("validator"),
        validator_version="validator-v1",
        validator_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        relationship_to_run_creator=ActorRelationship.INDEPENDENT,
        relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
        are_independent=True,
        evidence_ids=(f"evidence-{event_id}",),
        checks_run=("check-1",),
        assumptions=(),
        limitations=("Limited to retained artifacts",),
        result=(
            AssessmentOutcome.PASSED
            if status is ProgressStatus.VALIDATED
            else AssessmentOutcome.INCONCLUSIVE
        ),
        occurred_at=occurred_at,
        governing_policy_hash=POLICY_HASH,
    )


def test_only_independently_validated_subtasks_count() -> None:
    plan = _plan()
    events = (
        _event("event-1", "collect", ProgressStatus.PROVISIONALLY_COMPLETE, NOW),
        _event(
            "event-2",
            "analyze",
            ProgressStatus.VALIDATED,
            NOW + timedelta(seconds=1),
        ),
    )

    summary = calculate_progress(plan, events)

    assert summary.provisional_weight == Decimal("0.40")
    assert summary.official_weight == Decimal("0.00")


def test_dependency_invalidation_removes_dependent_weight() -> None:
    plan = _plan()
    events = (
        _event("event-1", "collect", ProgressStatus.VALIDATED, NOW),
        _event(
            "event-2",
            "analyze",
            ProgressStatus.VALIDATED,
            NOW + timedelta(seconds=1),
        ),
        _event(
            "event-3",
            "collect",
            ProgressStatus.INVALIDATED,
            NOW + timedelta(seconds=2),
        ),
    )

    summary = calculate_progress(plan, events)

    assert summary.validated_subtask_ids == ()
    assert summary.official_weight == Decimal("0.00")


def test_latest_event_state_is_deterministic_regardless_of_input_order() -> None:
    plan = _plan()
    validated = _event("event-1", "collect", ProgressStatus.VALIDATED, NOW)
    invalidated = _event(
        "event-2",
        "collect",
        ProgressStatus.INVALIDATED,
        NOW + timedelta(seconds=1),
    )

    first = calculate_progress(plan, (invalidated, validated))
    second = calculate_progress(plan, (validated, invalidated))

    assert first == second
    assert first.validated_subtask_ids == ()


def test_progress_weight_arithmetic_is_independent_of_ambient_decimal_precision() -> None:
    plan = _mixed_scale_plan()
    events = tuple(
        _event(
            f"mixed-event-{index}",
            f"step-{index}",
            ProgressStatus.VALIDATED,
            NOW + timedelta(seconds=index),
        ).model_copy(update={"plan_version_id": "mixed-plan"})
        for index in range(1, 4)
    )
    ambient_context = getcontext()
    original_context = ambient_context.copy()
    summaries = []
    for precision in (1, 2, 80):
        context = original_context.copy()
        context.prec = precision
        with localcontext(context):
            summaries.append(calculate_progress(plan, events))

    assert summaries[0] == summaries[1] == summaries[2]
    assert summaries[0].total_weight == Decimal("1.0000")
    assert summaries[0].official_weight == Decimal("1.0000")
    assert getcontext() is ambient_context
    assert (getcontext().prec, getcontext().rounding, getcontext().Emin, getcontext().Emax) == (
        original_context.prec,
        original_context.rounding,
        original_context.Emin,
        original_context.Emax,
    )


def test_remaining_budget_subtraction_is_independent_of_ambient_decimal_precision() -> None:
    reserve = ResourceBudget(
        cost_usd=1.0049,
        compute_units=1.0049,
        tokens=10,
        elapsed_seconds=1.0049,
        tool_calls=10,
        human_interventions=1,
    )
    usage = ResourceUsage(
        cost_usd=0.995,
        compute_units=0.995,
        tokens=5,
        elapsed_seconds=0.995,
        tool_calls=5,
        human_interventions=0,
    )
    allocation = BudgetAllocation(
        budget_id="budget-decimal-context",
        run_id="run-1",
        plan_version_id="plan-1",
        reserves=BudgetReserves(**dict.fromkeys(BudgetReserves.model_fields, reserve)),
        usage=BudgetUsage(**dict.fromkeys(BudgetUsage.model_fields, usage)),
        telemetry=_telemetry(),
        recorded_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    original_context = getcontext().copy()
    results = []
    for precision in (1, 2, 80):
        context = original_context.copy()
        context.prec = precision
        with localcontext(context):
            results.append(remaining_budget(allocation))

    assert results[0] == results[1] == results[2]
    assert results[0].exploration.cost_usd == 0.0099


def test_model_configuration_alias_cannot_claim_independent_validation() -> None:
    shared = {
        "kind": ActorKind.MODEL,
        "provider_id": "provider",
        "model_id": "model",
        "adapter_id": "adapter",
        "configuration_hash": "b" * 64,
        "created_at": NOW,
    }
    proposer = ActorIdentity(actor_id="proposer-alias", **shared)
    validator = ActorIdentity(actor_id="validator-alias", **shared)

    with pytest.raises(ValidationError, match="independent"):
        ProgressValidationEvent(
            event_id="event-alias",
            run_id="run-1",
            plan_version_id="plan-1",
            subtask_id="collect",
            requested_status=ProgressStatus.VALIDATED,
            completion_proposer=proposer,
            validator=validator,
            validator_version="validator-v1",
            validator_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            relationship_to_run_creator=ActorRelationship.INDEPENDENT,
            relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
            are_independent=True,
            evidence_ids=("evidence-1",),
            checks_run=("check-1",),
            assumptions=(),
            limitations=("Limited to retained artifacts",),
            result=AssessmentOutcome.PASSED,
            occurred_at=NOW,
            governing_policy_hash=POLICY_HASH,
        )
