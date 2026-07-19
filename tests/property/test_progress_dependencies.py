from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.progress.calculations import calculate_progress
from super_scientist.domain.progress.models import ProgressPlan, ProgressSubtask

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def _subtask(subtask_id: str, dependencies: tuple[str, ...], order: int) -> ProgressSubtask:
    return ProgressSubtask(
        subtask_id=subtask_id,
        plan_version_id="plan-1",
        description=f"Complete {subtask_id}",
        dependency_ids=dependencies,
        completion_criteria=("Criterion checked",),
        validator=ActorIdentity(
            actor_id="validator",
            kind=ActorKind.HUMAN,
            created_at=NOW,
        ),
        validator_version="validator-v1",
        weight=Decimal("0.50"),
        evidence_requirements=("retained-evidence",),
        order=order,
    )


@pytest.mark.property
def test_calculation_rejects_a_cyclic_plan_even_if_storage_bypassed_validation() -> None:
    plan = ProgressPlan.model_construct(
        plan_version_id="plan-1",
        run_id="run-1",
        version=1,
        subtasks=(
            _subtask("first", ("second",), 1),
            _subtask("second", ("first",), 2),
        ),
        created_at=NOW,
        governing_policy_hash="a" * 64,
    )

    with pytest.raises(ValueError, match="cycle"):
        calculate_progress(plan, ())


@pytest.mark.property
@pytest.mark.parametrize(
    ("subtasks", "message"),
    [
        ((_subtask("first", ("missing",), 1), _subtask("second", (), 2)), "unknown"),
        ((_subtask("same", (), 1), _subtask("same", (), 2)), "duplicate"),
        (
            (_subtask("first", (), 1), _subtask("second", ("first", "first"), 2)),
            "duplicate",
        ),
        ((_subtask("first", ("first",), 1), _subtask("second", (), 2)), "cycle"),
    ],
)
def test_calculation_rejects_invalid_dependency_graphs(
    subtasks: tuple[ProgressSubtask, ...],
    message: str,
) -> None:
    plan = ProgressPlan.model_construct(
        plan_version_id="plan-1",
        run_id="run-1",
        version=1,
        subtasks=subtasks,
        created_at=NOW,
        governing_policy_hash="a" * 64,
    )

    with pytest.raises(ValueError, match=message):
        calculate_progress(plan, ())


@pytest.mark.property
def test_calculation_rejects_weights_that_do_not_reconcile_to_one() -> None:
    plan = ProgressPlan.model_construct(
        plan_version_id="plan-1",
        run_id="run-1",
        version=1,
        subtasks=(_subtask("first", (), 1),),
        created_at=NOW,
        governing_policy_hash="a" * 64,
    )

    with pytest.raises(ValueError, match="weight"):
        calculate_progress(plan, ())
