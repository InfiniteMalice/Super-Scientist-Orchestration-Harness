from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.progress.models import (
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    TerminationReason,
)
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.audit.models import json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AppendProgressEvent,
    Proposal,
    RecordProgressPlan,
)
from super_scientist.providers.storage.domain_records import RunBudgetRepository
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import audit_events, run_checkpoints, transactions
from tests.integration.application.test_progress_service import (
    NOW,
    ProgressRuntime,
    _budget_proposal,
    _checkpoint_proposal,
    _completion_transaction,
    _event_proposal,
    _retain_completion_evidence,
)

pytest_plugins = ("tests.integration.application.test_progress_service",)


@pytest.mark.integration
@pytest.mark.parametrize(
    "damage",
    ["missing", "extra", "corrupt", "reparented", "rewound", "cross-plan-head"],
)
def test_progress_tampering_fails_before_exact_replay_can_mutate(
    v2_runtime: ProgressRuntime,
    damage: str,
) -> None:
    plan_proposal, first_event = _seed_progress(v2_runtime, two_subtasks=damage == "reparented")
    if damage == "rewound":
        _append_invalidation(v2_runtime, plan_proposal, first_event)
    elif damage == "cross-plan-head":
        _append_second_plan(v2_runtime)
    _damage(v2_runtime, damage, plan_proposal)
    before = _transaction_audit_counts(v2_runtime)

    with v2_runtime.uow_factory() as unit_of_work:
        result = verify_workspace(unit_of_work.repositories(), v2_runtime.artifact_store)
    assert result.valid is False

    with pytest.raises(StorageIntegrityError):
        v2_runtime.coordinator.submit(plan_proposal)

    assert _transaction_audit_counts(v2_runtime) == before


@pytest.mark.integration
def test_progress_replay_rejects_accepted_completion_without_retained_evidence(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal, _ = _seed_progress(v2_runtime, two_subtasks=False)
    completion = _completion_transaction(
        v2_runtime,
        plan_proposal.plan,
        final_result=AssessmentOutcome.PASSED,
        voluntary=False,
        termination_reason=TerminationReason.SUCCESS,
        decision_accepted=True,
    )
    final_validation = completion.completion_proposal.final_validation.model_copy(
        update={"evidence_ids": ()}
    )
    forged = completion.model_copy(
        update={
            "completion_proposal": completion.completion_proposal.model_copy(
                update={"final_validation": final_validation}
            )
        }
    )
    _replace_last_accepted_proposal(v2_runtime, completion, forged)

    with v2_runtime.uow_factory() as unit_of_work:
        result = verify_workspace(unit_of_work.repositories(), v2_runtime.artifact_store)

    assert result.valid is False
    assert result.reason is not None
    assert "completion" in result.reason or "evidence" in result.reason


@pytest.mark.integration
def test_progress_replay_rejects_accepted_checkpoint_with_noncanonical_artifact(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    event = _event_proposal(v2_runtime, plan_proposal.plan)
    assert v2_runtime.coordinator.submit(event).accepted is True
    assert v2_runtime.coordinator.submit(_budget_proposal(v2_runtime, plan_proposal.plan)).accepted
    checkpoint = _checkpoint_proposal(v2_runtime, plan_proposal.plan, event.event)
    assert v2_runtime.coordinator.submit(checkpoint).accepted is True
    artifact = checkpoint.checkpoint.artifact_refs[0].model_copy(
        update={"relative_path": "forged/noncanonical-artifact"}
    )
    forged_record = checkpoint.checkpoint.model_copy(update={"artifact_refs": (artifact,)})
    forged = checkpoint.model_copy(update={"checkpoint": forged_record})
    _replace_last_accepted_proposal(v2_runtime, checkpoint, forged)
    record_json = canonical_json_bytes(forged_record.model_dump(mode="json")).decode("utf-8")
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        connection.exec_driver_sql("DROP TRIGGER run_checkpoints_no_update")
        connection.execute(
            update(run_checkpoints)
            .where(run_checkpoints.c.checkpoint_id == forged_record.checkpoint_id)
            .values(
                record_json=record_json,
                content_hash=sha256_hex(record_json.encode("utf-8")),
            )
        )

    with v2_runtime.uow_factory() as unit_of_work:
        result = verify_workspace(unit_of_work.repositories(), v2_runtime.artifact_store)

    assert result.valid is False
    assert result.reason is not None
    assert "checkpoint" in result.reason or "artifact" in result.reason


def _seed_progress(
    runtime: ProgressRuntime,
    *,
    two_subtasks: bool,
) -> tuple[RecordProgressPlan, AppendProgressEvent]:
    plan_proposal = runtime.plan_proposal(two_subtasks=two_subtasks)
    assert runtime.coordinator.submit(plan_proposal).accepted is True
    event_proposal = _event_proposal(runtime, plan_proposal.plan)
    assert runtime.coordinator.submit(event_proposal).accepted is True
    budget_proposal = _budget_proposal(runtime, plan_proposal.plan)
    assert runtime.coordinator.submit(budget_proposal).accepted is True
    checkpoint_proposal = _checkpoint_proposal(
        runtime,
        plan_proposal.plan,
        event_proposal.event,
    )
    assert runtime.coordinator.submit(checkpoint_proposal).accepted is True
    completion = _completion_transaction(
        runtime,
        plan_proposal.plan,
        final_result=AssessmentOutcome.PASSED,
        voluntary=False,
        termination_reason=TerminationReason.SUCCESS,
        decision_accepted=True,
    )
    _retain_completion_evidence(runtime, completion)
    assert runtime.coordinator.submit(completion).accepted is True
    return plan_proposal, event_proposal


def _append_invalidation(
    runtime: ProgressRuntime,
    plan_proposal: RecordProgressPlan,
    first_event: AppendProgressEvent,
) -> None:
    invalidated = first_event.event.model_copy(
        update={
            "event_id": "event-invalidation",
            "requested_status": ProgressStatus.INVALIDATED,
            "result": AssessmentOutcome.INCONCLUSIVE,
            "occurred_at": NOW + timedelta(seconds=20),
        }
    )
    proposal = AppendProgressEvent(
        proposal_id="invalidation-proposal",
        idempotency_key="invalidation-key",
        proposer=runtime.proposer,
        approval=runtime.approval(),
        event=invalidated,
    )
    assert proposal.event.plan_version_id == plan_proposal.plan.plan_version_id
    assert runtime.coordinator.submit(proposal).accepted is True


def _append_second_plan(runtime: ProgressRuntime) -> None:
    plan_version_id = "plan-version-2"
    subtask = ProgressSubtask(
        subtask_id="collect-v2",
        plan_version_id=plan_version_id,
        description="Collect second-plan evidence",
        dependency_ids=(),
        completion_criteria=("Second plan checked",),
        validator=runtime.validator,
        validator_version="final-validator-v1",
        weight=Decimal("1.00"),
        evidence_requirements=("second-plan-evidence",),
        order=1,
    )
    proposal = RecordProgressPlan(
        proposal_id="plan-proposal-2",
        idempotency_key="plan-key-2",
        proposer=runtime.proposer,
        approval=runtime.approval(),
        plan=ProgressPlan(
            plan_version_id=plan_version_id,
            run_id="run-1",
            version=2,
            subtasks=(subtask,),
            created_at=NOW + timedelta(seconds=20),
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )
    assert runtime.coordinator.submit(proposal).accepted is True
    event = _event_proposal(
        runtime,
        proposal.plan,
        proposal_id="event-proposal-2",
    )
    assert runtime.coordinator.submit(event).accepted is True


def _damage(
    runtime: ProgressRuntime,
    damage: str,
    plan_proposal: RecordProgressPlan,
) -> None:
    if damage == "extra":
        extra = _budget_proposal(runtime, plan_proposal.plan).budget.model_copy(
            update={"budget_id": "extra-budget"}
        )
        with runtime.uow_factory() as unit_of_work:
            connection = unit_of_work.connection
            assert connection is not None
            RunBudgetRepository(connection).add(extra.budget_id, extra, extra.recorded_at)
        return

    with sqlite3.connect(runtime.database_path) as connection:
        if damage == "missing":
            connection.execute("DROP TRIGGER completion_decisions_no_delete")
            connection.execute("DELETE FROM completion_decisions")
        elif damage == "corrupt":
            connection.execute("DROP TRIGGER progress_events_no_update")
            connection.execute(
                "UPDATE progress_events SET content_hash = ?",
                ("f" * 64,),
            )
        elif damage == "reparented":
            connection.execute("DROP TRIGGER progress_events_no_update")
            connection.execute(
                "UPDATE progress_events SET subtask_id = 'analyze'",
            )
        elif damage == "rewound":
            connection.execute(
                "UPDATE progress_heads SET last_event_id = ?",
                ("event-event-proposal-1",),
            )
        elif damage == "cross-plan-head":
            connection.execute(
                "UPDATE progress_heads SET plan_version_id = ?",
                (plan_proposal.plan.plan_version_id,),
            )
        else:
            raise AssertionError(f"unknown damage: {damage}")


def _transaction_audit_counts(runtime: ProgressRuntime) -> tuple[int, int]:
    with sqlite3.connect(runtime.database_path) as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
        )


def _replace_last_accepted_proposal(
    runtime: ProgressRuntime,
    original: Proposal,
    replacement: Proposal,
) -> None:
    with runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_proposal_id(original.proposal_id)
        assert stored is not None
        audit_history = repositories.audit.list_all()
        assert len(audit_history) >= 2
        last = audit_history[-1]
        payload = dict(json_compatible_payload(last.payload))
        assert payload["proposal"] == original.model_dump(mode="json")
        payload["proposal"] = replacement.model_dump(mode="json")
        rewritten_audit = append_event(
            audit_history[-2],
            last.event_type,
            payload,
            last.occurred_at,
        )
        proposal_json = canonical_json_bytes(replacement.model_dump(mode="json")).decode("utf-8")
        connection.exec_driver_sql("DROP TRIGGER transactions_no_update")
        connection.exec_driver_sql("DROP TRIGGER audit_events_no_update")
        connection.execute(
            update(transactions)
            .where(transactions.c.proposal_id == original.proposal_id)
            .values(
                proposal_json=proposal_json,
                proposal_hash=sha256_hex(proposal_json.encode("utf-8")),
            )
        )
        connection.execute(
            update(audit_events)
            .where(audit_events.c.sequence == last.sequence)
            .values(
                event_id=rewritten_audit.event_id,
                previous_hash=rewritten_audit.previous_hash,
                payload_hash=rewritten_audit.payload_hash,
                event_hash=rewritten_audit.event_hash,
                event_json=rewritten_audit.model_dump_json(),
            )
        )
