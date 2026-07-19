from __future__ import annotations

from decimal import Decimal

from super_scientist.domain.improvement.classification import is_authoritative_verification
from super_scientist.domain.improvement.models import ActorRelationship, AssessmentOutcome
from super_scientist.domain.progress.models import (
    FalseFinishFinding,
    FalseFinishResult,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressSummary,
    ProgressValidationEvent,
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
