from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from inspect import signature
from pathlib import Path

import pytest
from sqlalchemy import Connection, insert, text

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    ResourceBudget,
    ResourceUsage,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    BudgetReserves,
    BudgetUsage,
    CompletionChecklistItem,
    CompletionChecklistStep,
    CompletionDecision,
    ExecutionTelemetry,
    FalseFinishFinding,
    FalseFinishResult,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressValidationEvent,
    RunCheckpoint,
    TerminationReason,
)
from super_scientist.providers.storage import domain_records, schema
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.domain_records import (
    CompletionDecisionRepository,
    ProgressEventRepository,
    ProgressPlanRepository,
    ProgressSubtaskRepository,
    RunBudgetRepository,
    RunCheckpointRepository,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
POLICY_HASH = "a" * 64

PROGRESS_REPOSITORIES = (
    ProgressPlanRepository,
    ProgressSubtaskRepository,
    ProgressEventRepository,
    RunBudgetRepository,
    RunCheckpointRepository,
    CompletionDecisionRepository,
)


def test_six_public_progress_repositories_have_connection_only_constructors() -> None:
    for repository_type in PROGRESS_REPOSITORIES:
        assert tuple(signature(repository_type).parameters) == ("connection",)
        assert repository_type.__name__ in domain_records.__all__
    assert "_AppendOnlyRecordRepository" not in domain_records.__all__


@pytest.mark.integration
def test_fixed_progress_repositories_round_trip_all_six_record_types(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "round-trip.db")
    plan, subtask, event, budget, checkpoint, completion = _records()
    try:
        ProgressPlanRepository(connection).add(plan.plan_version_id, plan, plan.created_at)
        ProgressSubtaskRepository(connection).add(
            subtask.subtask_id,
            subtask,
            plan.created_at,
        )
        ProgressEventRepository(connection).add(event.event_id, event, event.occurred_at)
        RunBudgetRepository(connection).add(budget.budget_id, budget, budget.recorded_at)
        RunCheckpointRepository(connection).add(
            checkpoint.checkpoint_id,
            checkpoint,
            checkpoint.occurred_at,
        )
        CompletionDecisionRepository(connection).add(
            completion.completion_decision_id,
            completion,
            completion.decided_at,
        )

        assert ProgressPlanRepository(connection).get(plan.plan_version_id) == plan
        assert ProgressSubtaskRepository(connection).get(subtask.subtask_id) == subtask
        assert ProgressEventRepository(connection).get(event.event_id) == event
        assert RunBudgetRepository(connection).get(budget.budget_id) == budget
        assert RunCheckpointRepository(connection).get(checkpoint.checkpoint_id) == checkpoint
        assert (
            CompletionDecisionRepository(connection).get(completion.completion_decision_id)
            == completion
        )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("table_name", "identifier_column", "identifier"),
    [
        ("progress_plans", "plan_version_id", "plan-1"),
        ("progress_subtasks", "subtask_id", "collect"),
        ("progress_events", "event_id", "event-1"),
        ("run_budgets", "budget_id", "budget-1"),
        ("run_checkpoints", "checkpoint_id", "checkpoint-1"),
        ("completion_decisions", "completion_decision_id", "completion-decision-1"),
    ],
)
def test_fixed_progress_repositories_reject_corrupt_storage(
    tmp_path: Path,
    table_name: str,
    identifier_column: str,
    identifier: str,
) -> None:
    engine, connection = _connection(tmp_path, f"corrupt-{table_name}.db")
    repositories = _seed_records(connection)
    try:
        connection.exec_driver_sql(f"DROP TRIGGER {table_name}_no_update")
        connection.execute(
            text(
                f"UPDATE {table_name} SET content_hash = :bad_hash "
                f"WHERE {identifier_column} = :identifier"
            ),
            {"bad_hash": "f" * 64, "identifier": identifier},
        )

        with pytest.raises(StorageIntegrityError, match="content_hash"):
            repositories[table_name].get(identifier)
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


def _connection(tmp_path: Path, name: str) -> tuple[object, Connection]:
    database_url = f"sqlite:///{(tmp_path / name).as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    connection = engine.connect()
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    connection.execute(
        insert(schema.research_runs).values(
            run_id="run-1",
            record_json="{}",
            content_hash="a" * 64,
            created_at=NOW.isoformat(),
        )
    )
    return engine, connection


def _seed_records(connection: Connection) -> dict[str, object]:
    plan, subtask, event, budget, checkpoint, completion = _records()
    repositories = {
        "progress_plans": ProgressPlanRepository(connection),
        "progress_subtasks": ProgressSubtaskRepository(connection),
        "progress_events": ProgressEventRepository(connection),
        "run_budgets": RunBudgetRepository(connection),
        "run_checkpoints": RunCheckpointRepository(connection),
        "completion_decisions": CompletionDecisionRepository(connection),
    }
    repositories["progress_plans"].add(plan.plan_version_id, plan, plan.created_at)
    repositories["progress_subtasks"].add(subtask.subtask_id, subtask, plan.created_at)
    repositories["progress_events"].add(event.event_id, event, event.occurred_at)
    repositories["run_budgets"].add(budget.budget_id, budget, budget.recorded_at)
    repositories["run_checkpoints"].add(
        checkpoint.checkpoint_id,
        checkpoint,
        checkpoint.occurred_at,
    )
    repositories["completion_decisions"].add(
        completion.completion_decision_id,
        completion,
        completion.decided_at,
    )
    return repositories


def _records() -> tuple[
    ProgressPlan,
    ProgressSubtask,
    ProgressValidationEvent,
    BudgetAllocation,
    RunCheckpoint,
    CompletionDecision,
]:
    validator = _actor("validator")
    subtask = ProgressSubtask(
        subtask_id="collect",
        plan_version_id="plan-1",
        description="Collect evidence",
        dependency_ids=(),
        completion_criteria=("Evidence retained",),
        validator=validator,
        validator_version="validator-v1",
        weight=Decimal("1.00"),
        evidence_requirements=("primary-source",),
        order=1,
    )
    plan = ProgressPlan(
        plan_version_id="plan-1",
        run_id="run-1",
        version=1,
        subtasks=(subtask,),
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    event = ProgressValidationEvent(
        event_id="event-1",
        run_id="run-1",
        plan_version_id="plan-1",
        subtask_id="collect",
        requested_status=ProgressStatus.VALIDATED,
        completion_proposer=_actor("proposer"),
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
    budget = BudgetAllocation(
        budget_id="budget-1",
        run_id="run-1",
        plan_version_id="plan-1",
        reserves=_reserves(),
        usage=_usage(),
        telemetry=_telemetry(),
        recorded_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    raw_log = _artifact("b", "application/jsonl", 12)
    raw_transaction = _artifact("c", "application/json", 13)
    checkpoint = RunCheckpoint(
        checkpoint_id="checkpoint-1",
        run_id="run-1",
        plan_version_id="plan-1",
        validated_subtask_ids=("collect",),
        pending_dependency_ids=(),
        hypothesis_ids=("hypothesis-1",),
        artifact_refs=(raw_log,),
        environment_snapshot_id="environment-1",
        attempted_operations=("operation-1",),
        failures=(),
        remaining_budget=_reserves(),
        next_recommended_action="Run final validation",
        raw_log_refs=(raw_log,),
        raw_transaction_refs=(raw_transaction,),
        telemetry=_telemetry(),
        occurred_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    checklist = tuple(
        CompletionChecklistItem(
            step=step,
            completed=True,
            detail=f"Completed {step.value}",
            evidence_ids=(f"evidence-{index}",),
        )
        for index, step in enumerate(CompletionChecklistStep, start=1)
    )
    completion = CompletionDecision(
        completion_decision_id="completion-decision-1",
        run_id="run-1",
        plan_version_id="plan-1",
        completion_proposal_id="completion-proposal-1",
        decision_authority=validator,
        accepted=True,
        checklist=checklist,
        final_validator_result=AssessmentOutcome.PASSED,
        false_finish=FalseFinishFinding(
            result=FalseFinishResult.NOT_FALSE_FINISH,
            voluntary_termination=False,
            claims_completion=True,
            final_validator_failed=False,
            meaningful_validated_progress=True,
            unused_budget=True,
            reasons=(),
        ),
        termination_reason=TerminationReason.SUCCESS,
        decided_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    return plan, subtask, event, budget, checkpoint, completion


def _actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=NOW)


def _artifact(character: str, media_type: str, size: int) -> ArtifactRef:
    digest = character * 64
    return ArtifactRef(
        sha256=digest,
        size_bytes=size,
        media_type=media_type,
        relative_path=f"sha256/{character * 2}/{digest}",
    )


def _resource_budget() -> ResourceBudget:
    return ResourceBudget(
        cost_usd=10.0,
        compute_units=10.0,
        tokens=100,
        elapsed_seconds=100.0,
        tool_calls=10,
        human_interventions=1,
    )


def _resource_usage() -> ResourceUsage:
    return ResourceUsage(
        cost_usd=1.0,
        compute_units=1.0,
        tokens=10,
        elapsed_seconds=10.0,
        tool_calls=1,
        human_interventions=0,
    )


def _reserves() -> BudgetReserves:
    budget = _resource_budget()
    return BudgetReserves(
        exploration=budget,
        implementation=budget,
        verification=budget,
        recovery=budget,
        finalization=budget,
    )


def _usage() -> BudgetUsage:
    usage = _resource_usage()
    return BudgetUsage(
        exploration=usage,
        implementation=usage,
        verification=usage,
        recovery=usage,
        finalization=usage,
    )


def _telemetry() -> ExecutionTelemetry:
    return ExecutionTelemetry(
        episodes=1,
        model_calls=1,
        input_tokens=10,
        output_tokens=10,
        tool_calls=1,
        operations=1,
        files_changed=1,
        elapsed_seconds=10.0,
        verification_seconds=2.0,
        repeated_actions=0,
        reverted_actions=0,
        checkpoints=1,
        timed_out=False,
        termination_reason=None,
        estimated_cost_usd=1.0,
    )
