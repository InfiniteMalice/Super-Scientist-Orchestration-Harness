from __future__ import annotations

from decimal import Decimal

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.improvement.classification import is_authoritative_verification
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    ResourceBudget,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    BudgetReserves,
    FalseFinishFinding,
    FalseFinishResult,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressSummary,
    ProgressValidationEvent,
    RunCheckpoint,
    progress_actors_are_independent,
)


def calculate_progress(
    plan: ProgressPlan,
    events: tuple[ProgressValidationEvent, ...],
) -> ProgressSummary:
    ordered_subtasks = _topological_subtasks(plan)
    latest_by_subtask: dict[str, ProgressValidationEvent] = {}
    for candidate_event in sorted(events, key=lambda item: (item.occurred_at, item.event_id)):
        if (
            candidate_event.run_id != plan.run_id
            or candidate_event.plan_version_id != plan.plan_version_id
        ):
            raise ValueError("progress event does not belong to the supplied plan")
        latest_by_subtask[candidate_event.subtask_id] = candidate_event

    eligible: set[str] = set()
    provisional_ids: list[str] = []
    official_ids: list[str] = []
    provisional_weight = Decimal("0")
    official_weight = Decimal("0")
    for subtask in ordered_subtasks:
        latest_event = latest_by_subtask.get(subtask.subtask_id)
        if latest_event is None:
            continue
        if latest_event.requested_status is ProgressStatus.PROVISIONALLY_COMPLETE:
            provisional_ids.append(subtask.subtask_id)
            provisional_weight += subtask.weight
        if (
            latest_event.requested_status is ProgressStatus.VALIDATED
            and _is_independently_accepted(latest_event, subtask)
            and all(dependency_id in eligible for dependency_id in subtask.dependency_ids)
        ):
            eligible.add(subtask.subtask_id)
            official_ids.append(subtask.subtask_id)
            official_weight += subtask.weight

    return ProgressSummary(
        plan_version_id=plan.plan_version_id,
        total_weight=sum((subtask.weight for subtask in ordered_subtasks), Decimal("0")),
        provisional_weight=provisional_weight,
        official_weight=official_weight,
        provisional_subtask_ids=tuple(provisional_ids),
        validated_subtask_ids=tuple(official_ids),
    )


def event_advances_progress_head(
    event: ProgressValidationEvent,
    plans: tuple[ProgressPlan, ...],
    current_head_event: ProgressValidationEvent | None,
) -> bool:
    highest_plan = current_progress_plan(plans, event.run_id)
    if highest_plan is None:
        return False
    if event.plan_version_id != highest_plan.plan_version_id:
        return False
    if current_head_event is None:
        return True
    if current_head_event.run_id != event.run_id:
        return False
    if current_head_event.plan_version_id != event.plan_version_id:
        return True
    return (event.occurred_at, event.event_id) > (
        current_head_event.occurred_at,
        current_head_event.event_id,
    )


def current_progress_plan(
    plans: tuple[ProgressPlan, ...],
    run_id: str,
) -> ProgressPlan | None:
    run_plans = tuple(plan for plan in plans if plan.run_id == run_id)
    if not run_plans:
        return None
    return max(run_plans, key=lambda plan: (plan.version, plan.plan_version_id))


def replay_pending_dependency_ids(
    plan: ProgressPlan,
    validated_subtask_ids: tuple[str, ...],
) -> tuple[str, ...]:
    ordered_subtasks = _topological_subtasks(plan)
    validated = frozenset(validated_subtask_ids)
    unmet = {
        dependency_id
        for subtask in ordered_subtasks
        for dependency_id in subtask.dependency_ids
        if dependency_id not in validated
    }
    return tuple(subtask.subtask_id for subtask in ordered_subtasks if subtask.subtask_id in unmet)


def select_checkpoint_budget(
    checkpoint: RunCheckpoint,
    budgets: tuple[BudgetAllocation, ...],
) -> BudgetAllocation | None:
    applicable = tuple(
        budget
        for budget in budgets
        if budget.run_id == checkpoint.run_id
        and budget.plan_version_id == checkpoint.plan_version_id
        and budget.governing_policy_hash == checkpoint.governing_policy_hash
    )
    if not applicable:
        return None
    return max(applicable, key=lambda budget: (budget.recorded_at, budget.budget_id))


def remaining_budget(allocation: BudgetAllocation) -> BudgetReserves:
    def remaining(category: str) -> ResourceBudget:
        reserve = getattr(allocation.reserves, category)
        usage = getattr(allocation.usage, category)
        return ResourceBudget(
            cost_usd=_subtract_finite_float(reserve.cost_usd, usage.cost_usd),
            compute_units=_subtract_finite_float(
                reserve.compute_units,
                usage.compute_units,
            ),
            tokens=reserve.tokens - usage.tokens,
            elapsed_seconds=_subtract_finite_float(
                reserve.elapsed_seconds,
                usage.elapsed_seconds,
            ),
            tool_calls=reserve.tool_calls - usage.tool_calls,
            human_interventions=reserve.human_interventions - usage.human_interventions,
        )

    return BudgetReserves(
        exploration=remaining("exploration"),
        implementation=remaining("implementation"),
        verification=remaining("verification"),
        recovery=remaining("recovery"),
        finalization=remaining("finalization"),
    )


def has_unused_budget(allocation: BudgetAllocation) -> bool:
    resource_fields = (
        "cost_usd",
        "compute_units",
        "tokens",
        "elapsed_seconds",
        "tool_calls",
        "human_interventions",
    )
    return any(
        getattr(getattr(allocation.usage, category), field_name)
        < getattr(getattr(allocation.reserves, category), field_name)
        for category in BudgetReserves.model_fields
        for field_name in resource_fields
    )


def is_canonical_artifact_ref(reference: ArtifactRef) -> bool:
    return reference.relative_path == f"sha256/{reference.sha256[:2]}/{reference.sha256}"


def detect_false_finish(
    *,
    voluntary_termination: bool,
    claims_completion: bool,
    final_validator_result: AssessmentOutcome,
    validated_weight: Decimal,
    unused_budget: bool,
) -> FalseFinishFinding:
    is_false_finish = (
        voluntary_termination
        and claims_completion
        and final_validator_result is not AssessmentOutcome.PASSED
        and validated_weight > Decimal("0")
        and unused_budget
    )
    if is_false_finish:
        return FalseFinishFinding(
            result=FalseFinishResult.FALSE_FINISH,
            voluntary_termination=voluntary_termination,
            claims_completion=claims_completion,
            final_validator_failed=True,
            meaningful_validated_progress=True,
            unused_budget=unused_budget,
            reasons=(
                "voluntary completion followed meaningful validated progress but failed final "
                "validation while budget remained",
            ),
        )
    return FalseFinishFinding(
        result=FalseFinishResult.NOT_FALSE_FINISH,
        voluntary_termination=voluntary_termination,
        claims_completion=claims_completion,
        final_validator_failed=final_validator_result is not AssessmentOutcome.PASSED,
        meaningful_validated_progress=validated_weight > Decimal("0"),
        unused_budget=unused_budget,
        reasons=(),
    )


def _topological_subtasks(plan: ProgressPlan) -> tuple[ProgressSubtask, ...]:
    subtasks_by_id: dict[str, ProgressSubtask] = {}
    for subtask in plan.subtasks:
        if subtask.subtask_id in subtasks_by_id:
            raise ValueError("progress plan contains duplicate subtask identifiers")
        if subtask.plan_version_id != plan.plan_version_id:
            raise ValueError("progress subtask does not belong to the supplied plan")
        subtasks_by_id[subtask.subtask_id] = subtask

    indegree = {subtask_id: 0 for subtask_id in subtasks_by_id}
    dependents: dict[str, list[str]] = {subtask_id: [] for subtask_id in subtasks_by_id}
    for subtask in plan.subtasks:
        if len(set(subtask.dependency_ids)) != len(subtask.dependency_ids):
            raise ValueError("progress subtask contains duplicate dependencies")
        for dependency_id in subtask.dependency_ids:
            if dependency_id not in subtasks_by_id:
                raise ValueError("progress plan contains an unknown dependency")
            indegree[subtask.subtask_id] += 1
            dependents[dependency_id].append(subtask.subtask_id)

    if sum((subtask.weight for subtask in plan.subtasks), Decimal("0")) != Decimal("1"):
        raise ValueError("progress plan weights must reconcile exactly to one")

    def order_key(subtask_id: str) -> tuple[int, str]:
        return subtasks_by_id[subtask_id].order, subtask_id

    ready = sorted(
        (subtask_id for subtask_id, degree in indegree.items() if degree == 0),
        key=order_key,
    )
    ordered_ids: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered_ids.append(current)
        for dependent_id in sorted(dependents[current], key=order_key):
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                ready.append(dependent_id)
                ready.sort(key=order_key)
    if len(ordered_ids) != len(subtasks_by_id):
        raise ValueError("progress plan dependency cycle detected")
    return tuple(subtasks_by_id[subtask_id] for subtask_id in ordered_ids)


def _is_independently_accepted(
    event: ProgressValidationEvent,
    subtask: ProgressSubtask,
) -> bool:
    return (
        event.validator == subtask.validator
        and event.validator_version == subtask.validator_version
        and event.result is AssessmentOutcome.PASSED
        and is_authoritative_verification(event.validator_category)
        and event.relationship_to_run_creator is ActorRelationship.INDEPENDENT
        and event.relationship_to_completion_proposer is ActorRelationship.INDEPENDENT
        and event.are_independent
        and progress_actors_are_independent(event.validator, event.completion_proposer)
    )


def _subtract_finite_float(reserved: float, used: float) -> float:
    return float(Decimal(str(reserved)) - Decimal(str(used)))
