from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from super_scientist.application.progress.service import progress_authority_rejection
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV1,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
    ResourceBudget,
    ResourceUsage,
)
from super_scientist.domain.progress.calculations import detect_false_finish
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    BudgetReserves,
    BudgetUsage,
    CompletionChecklistItem,
    CompletionChecklistStep,
    CompletionDecision,
    CompletionProposal,
    ExecutionTelemetry,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressValidationEvent,
    RunCheckpoint,
    TerminationReason,
)
from super_scientist.domain.research_runs.models import ResearchRun, RunBudgetAllocation
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AppendProgressEvent,
    Approval,
    CreateResearchRun,
    DecideCompletion,
    RecordProgressPlan,
    RecordRunBudget,
    RecordRunCheckpoint,
    RejectionCode,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    CompletionDecisionRepository,
    ProgressEventRepository,
    ProgressHeadRepository,
    ProgressPlanRepository,
    ProgressSubtaskRepository,
    RunBudgetRepository,
    RunCheckpointRepository,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class ProgressRuntime:
    coordinator: TransactionCoordinator
    uow_factory: Callable[[], DatabaseUnitOfWork]
    policy: PolicySnapshot
    proposer: ActorIdentity
    approver: ActorIdentity
    validator: ActorIdentity
    artifact_store: FileArtifactStore
    database_path: Path

    def approval(self) -> Approval:
        return Approval(approver=self.approver, approved_at=NOW)

    def plan_proposal(
        self,
        *,
        proposal_id: str = "plan-proposal-1",
        idempotency_key: str = "plan-key-1",
        two_subtasks: bool = False,
    ) -> RecordProgressPlan:
        plan_version_id = f"plan-{proposal_id}"
        subtasks = (
            _subtask(
                "collect",
                (),
                1,
                Decimal("0.50") if two_subtasks else Decimal("1.00"),
                plan_version_id,
                self,
            ),
        )
        if two_subtasks:
            subtasks = (
                *subtasks,
                _subtask(
                    "analyze",
                    ("collect",),
                    2,
                    Decimal("0.50"),
                    plan_version_id,
                    self,
                ),
            )
        return RecordProgressPlan(
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            proposer=self.proposer,
            approval=self.approval(),
            plan=ProgressPlan(
                plan_version_id=plan_version_id,
                run_id="run-1",
                version=1,
                subtasks=subtasks,
                created_at=NOW,
                governing_policy_hash=self.policy.policy_hash,
            ),
        )


@pytest.fixture
def v2_runtime(tmp_path: Path) -> Iterator[ProgressRuntime]:
    yield from _runtime(tmp_path, _v2_policy())


@pytest.fixture
def v1_runtime(tmp_path: Path) -> Iterator[ProgressRuntime]:
    yield from _runtime(tmp_path, GovernancePolicyV1(required_claim_checks=("source_exists",)))


@pytest.mark.integration
def test_v1_all_five_progress_proposals_fail_closed_durably_and_audited(
    v1_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v1_runtime.plan_proposal()
    event_proposal = _event_proposal(v1_runtime, plan_proposal.plan)
    proposals = (
        plan_proposal,
        event_proposal,
        _budget_proposal(v1_runtime, plan_proposal.plan),
        _checkpoint_proposal(v1_runtime, plan_proposal.plan, event_proposal.event),
        _completion_transaction(
            v1_runtime,
            plan_proposal.plan,
            final_result=AssessmentOutcome.PASSED,
            voluntary=False,
            termination_reason=TerminationReason.SUCCESS,
            decision_accepted=True,
        ),
    )

    decisions = tuple(v1_runtime.coordinator.submit(proposal) for proposal in proposals)

    assert all(not decision.accepted for decision in decisions)
    assert all(
        decision.reasons[0].code is RejectionCode.PERMISSION_DENIED for decision in decisions
    )
    with v1_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        repositories = unit_of_work.repositories()
        assert len(repositories.transactions.list_all()) == 5
        assert len(repositories.audit.list_all()) == 5
        assert ProgressPlanRepository(connection).list_all() == ()
        assert ProgressSubtaskRepository(connection).list_all() == ()
        assert ProgressEventRepository(connection).list_all() == ()
        assert RunBudgetRepository(connection).list_all() == ()
        assert RunCheckpointRepository(connection).list_all() == ()
        assert CompletionDecisionRepository(connection).list_all() == ()


@pytest.mark.integration
def test_v2_plan_projects_plan_then_subtasks_and_exact_replay_is_stable(
    v2_runtime: ProgressRuntime,
) -> None:
    proposal = v2_runtime.plan_proposal(two_subtasks=True)

    first = v2_runtime.coordinator.submit(proposal)
    second = v2_runtime.coordinator.submit(proposal)

    assert first.accepted is True
    assert second == first.model_copy(update={"replayed": True})
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        repositories = unit_of_work.repositories()
        assert ProgressPlanRepository(connection).list_all() == (proposal.plan,)
        assert ProgressSubtaskRepository(connection).list_all() == tuple(
            sorted(proposal.plan.subtasks, key=lambda item: item.subtask_id)
        )
        assert len(repositories.transactions.list_all()) == 2
        assert len(repositories.audit.list_all()) == 2


@pytest.mark.integration
def test_plan_and_subtasks_roll_back_atomically_when_subtask_projection_fails(
    v2_runtime: ProgressRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = v2_runtime.plan_proposal(
        proposal_id="rollback-plan-proposal",
        idempotency_key="rollback-plan-key",
        two_subtasks=True,
    )
    original_add = ProgressSubtaskRepository.add

    def fail_second_subtask(
        repository: ProgressSubtaskRepository,
        record_id: str,
        record: ProgressSubtask,
        created_at: datetime,
    ) -> None:
        if record_id == "analyze":
            raise RuntimeError("injected subtask projection failure")
        original_add(repository, record_id, record, created_at)

    monkeypatch.setattr(ProgressSubtaskRepository, "add", fail_second_subtask)

    with pytest.raises(RuntimeError, match="injected"):
        v2_runtime.coordinator.submit(proposal)

    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        repositories = unit_of_work.repositories()
        assert ProgressPlanRepository(connection).list_all() == ()
        assert ProgressSubtaskRepository(connection).list_all() == ()
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 1


@pytest.mark.integration
def test_router_registers_all_five_fixed_progress_handlers(v2_runtime: ProgressRuntime) -> None:
    proposal_types = (
        "record_progress_plan",
        "append_progress_event",
        "record_run_budget",
        "record_run_checkpoint",
        "decide_completion",
    )

    assert (
        tuple(v2_runtime.coordinator.router.resolve(item).proposal_type for item in proposal_types)
        == proposal_types
    )


@pytest.mark.integration
def test_progress_semantics_are_fixed_and_a_weaker_v2_requirement_cannot_authorize(
    v2_runtime: ProgressRuntime,
) -> None:
    proposal = v2_runtime.plan_proposal()
    weak_requirement = (
        _v2_policy()
        .adaptation_requirements[0]
        .model_copy(update={"minimum_verification": VerificationLevel.MODEL_CONFIDENCE})
    )
    weak_policy = _v2_policy().model_copy(update={"adaptation_requirements": (weak_requirement,)})
    weak_snapshot = PolicySnapshot(
        policy_hash=policy_hash(weak_policy),
        policy=weak_policy,
    )

    decision = progress_authority_rejection(proposal, weak_snapshot)

    assert {"change_target", "persistence", "classification"}.isdisjoint(
        type(proposal).model_fields
    )
    assert decision is not None
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INSUFFICIENT_GROUNDING


@pytest.mark.integration
@pytest.mark.parametrize(
    ("protected_evaluation_required", "rollback_required"),
    ((True, False), (False, True)),
    ids=("protected-evaluation", "rollback"),
)
@pytest.mark.parametrize(
    "proposal_kind",
    (
        "record_progress_plan",
        "append_progress_event",
        "record_run_budget",
        "record_run_checkpoint",
        "decide_completion",
    ),
)
def test_progress_proposals_reject_unsupported_v2_policy_flags_before_projection(
    tmp_path: Path,
    protected_evaluation_required: bool,
    rollback_required: bool,
    proposal_kind: str,
) -> None:
    policy = _v2_policy(
        protected_evaluation_required=protected_evaluation_required,
        rollback_required=rollback_required,
    )
    runtime_iterator = _runtime(tmp_path, policy)
    runtime = next(runtime_iterator)
    plan_proposal = runtime.plan_proposal()
    event_proposal = _event_proposal(runtime, plan_proposal.plan)
    proposals = {
        "record_progress_plan": plan_proposal,
        "append_progress_event": event_proposal,
        "record_run_budget": _budget_proposal(runtime, plan_proposal.plan),
        "record_run_checkpoint": _checkpoint_proposal(
            runtime,
            plan_proposal.plan,
            event_proposal.event,
        ),
        "decide_completion": _completion_transaction(
            runtime,
            plan_proposal.plan,
            final_result=AssessmentOutcome.PASSED,
            voluntary=False,
            termination_reason=TerminationReason.SUCCESS,
            decision_accepted=True,
        ),
    }

    try:
        decision = runtime.coordinator.submit(proposals[proposal_kind])

        assert decision.accepted is False
        assert decision.reasons[0].code is RejectionCode.INSUFFICIENT_GROUNDING
        assert decision.reasons[0].message == (
            "progress admission cannot satisfy protected-evaluation or rollback requirements"
        )
        with runtime.uow_factory() as unit_of_work:
            connection = unit_of_work.connection
            assert connection is not None
            assert ProgressPlanRepository(connection).list_all() == ()
            assert ProgressSubtaskRepository(connection).list_all() == ()
            assert ProgressEventRepository(connection).list_all() == ()
            assert ProgressHeadRepository(connection).get("run-1") is None
            assert RunBudgetRepository(connection).list_all() == ()
            assert RunCheckpointRepository(connection).list_all() == ()
            assert CompletionDecisionRepository(connection).list_all() == ()
    finally:
        with pytest.raises(StopIteration):
            next(runtime_iterator)


@pytest.mark.integration
def test_v2_progress_workflow_projects_events_head_budget_checkpoint_and_completion(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    _assert_accepted_exact_replay(v2_runtime, plan_proposal)
    event_proposal = _event_proposal(v2_runtime, plan_proposal.plan)
    _assert_accepted_exact_replay(v2_runtime, event_proposal)
    budget_proposal = _budget_proposal(v2_runtime, plan_proposal.plan)
    _assert_accepted_exact_replay(v2_runtime, budget_proposal)
    checkpoint_proposal = _checkpoint_proposal(
        v2_runtime,
        plan_proposal.plan,
        event_proposal.event,
    )
    _assert_accepted_exact_replay(v2_runtime, checkpoint_proposal)
    completion_proposal = _completion_transaction(
        v2_runtime,
        plan_proposal.plan,
        final_result=AssessmentOutcome.PASSED,
        voluntary=False,
        termination_reason=TerminationReason.SUCCESS,
        decision_accepted=True,
    )
    _retain_completion_evidence(v2_runtime, completion_proposal)

    _assert_accepted_exact_replay(v2_runtime, completion_proposal)
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert ProgressEventRepository(connection).list_all() == (event_proposal.event,)
        assert ProgressHeadRepository(connection).get("run-1") == (
            plan_proposal.plan.plan_version_id,
            event_proposal.event.event_id,
        )
        assert RunBudgetRepository(connection).list_all() == (budget_proposal.budget,)
        assert RunCheckpointRepository(connection).list_all() == (checkpoint_proposal.checkpoint,)
        assert CompletionDecisionRepository(connection).list_all() == (
            completion_proposal.completion_decision,
        )
        repositories = unit_of_work.repositories()
        assert len(repositories.transactions.list_all()) == 15
        assert len(repositories.audit.list_all()) == 15


@pytest.mark.integration
def test_all_progress_records_require_the_exact_active_policy_hash(
    v2_runtime: ProgressRuntime,
) -> None:
    bad_hash = "f" * 64
    plan_proposal = v2_runtime.plan_proposal()
    bad_plan = plan_proposal.model_copy(
        update={
            "proposal_id": "bad-policy-plan-proposal",
            "idempotency_key": "bad-policy-plan-key",
            "plan": plan_proposal.plan.model_copy(update={"governing_policy_hash": bad_hash}),
        }
    )
    assert _rejection_code(v2_runtime, bad_plan) is RejectionCode.POLICY_HASH_MISMATCH
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True

    event_proposal = _event_proposal(v2_runtime, plan_proposal.plan)
    bad_event = event_proposal.model_copy(
        update={
            "proposal_id": "bad-policy-event-proposal",
            "idempotency_key": "bad-policy-event-key",
            "event": event_proposal.event.model_copy(update={"governing_policy_hash": bad_hash}),
        }
    )
    assert _rejection_code(v2_runtime, bad_event) is RejectionCode.POLICY_HASH_MISMATCH
    assert v2_runtime.coordinator.submit(event_proposal).accepted is True

    budget_proposal = _budget_proposal(v2_runtime, plan_proposal.plan)
    bad_budget = budget_proposal.model_copy(
        update={
            "proposal_id": "bad-policy-budget-proposal",
            "idempotency_key": "bad-policy-budget-key",
            "budget": budget_proposal.budget.model_copy(update={"governing_policy_hash": bad_hash}),
        }
    )
    assert _rejection_code(v2_runtime, bad_budget) is RejectionCode.POLICY_HASH_MISMATCH
    assert v2_runtime.coordinator.submit(budget_proposal).accepted is True

    checkpoint_proposal = _checkpoint_proposal(
        v2_runtime,
        plan_proposal.plan,
        event_proposal.event,
    )
    bad_checkpoint = checkpoint_proposal.model_copy(
        update={
            "proposal_id": "bad-policy-checkpoint-proposal",
            "idempotency_key": "bad-policy-checkpoint-key",
            "checkpoint": checkpoint_proposal.checkpoint.model_copy(
                update={"governing_policy_hash": bad_hash}
            ),
        }
    )
    assert _rejection_code(v2_runtime, bad_checkpoint) is RejectionCode.POLICY_HASH_MISMATCH

    completion = _completion_transaction(
        v2_runtime,
        plan_proposal.plan,
        final_result=AssessmentOutcome.PASSED,
        voluntary=False,
        termination_reason=TerminationReason.SUCCESS,
        decision_accepted=True,
    )
    final_validation = completion.completion_proposal.final_validation.model_copy(
        update={"governing_policy_hash": bad_hash}
    )
    bad_completion = completion.model_copy(
        update={
            "proposal_id": "bad-policy-completion-proposal",
            "idempotency_key": "bad-policy-completion-key",
            "completion_proposal": completion.completion_proposal.model_copy(
                update={
                    "final_validation": final_validation,
                    "governing_policy_hash": bad_hash,
                }
            ),
            "completion_decision": completion.completion_decision.model_copy(
                update={"governing_policy_hash": bad_hash}
            ),
        }
    )
    assert _rejection_code(v2_runtime, bad_completion) is RejectionCode.POLICY_HASH_MISMATCH

    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert RunCheckpointRepository(connection).list_all() == ()
        assert CompletionDecisionRepository(connection).list_all() == ()


@pytest.mark.integration
def test_validation_alias_of_run_creator_is_rejected_and_projects_nothing(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = _plan_proposal_with_validator(v2_runtime, v2_runtime.proposer)
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    worker = _actor("completion-worker")
    event_proposal = _event_proposal(
        v2_runtime,
        plan_proposal.plan,
        proposal_id="creator-alias-event-proposal",
        proposer=worker,
        validator=v2_runtime.proposer,
    )

    decision = v2_runtime.coordinator.submit(event_proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert ProgressEventRepository(connection).list_all() == ()
        assert ProgressHeadRepository(connection).get("run-1") is None


@pytest.mark.integration
def test_progress_event_older_than_current_head_is_rejected_without_rewinding(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    newer = _event_proposal(
        v2_runtime,
        plan_proposal.plan,
        proposal_id="newer-event-proposal",
        event_id="event-e2",
        occurred_at=NOW + timedelta(seconds=2),
    )
    older = _event_proposal(
        v2_runtime,
        plan_proposal.plan,
        proposal_id="older-event-proposal",
        event_id="event-e1",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert v2_runtime.coordinator.submit(newer).accepted is True

    decision = v2_runtime.coordinator.submit(older)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert ProgressEventRepository(connection).list_all() == (newer.event,)
        assert ProgressHeadRepository(connection).get("run-1") == (
            plan_proposal.plan.plan_version_id,
            newer.event.event_id,
        )


@pytest.mark.integration
def test_progress_event_for_obsolete_plan_version_is_rejected(
    v2_runtime: ProgressRuntime,
) -> None:
    first_plan = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(first_plan).accepted is True
    second_plan = _second_plan_proposal(v2_runtime)
    assert v2_runtime.coordinator.submit(second_plan).accepted is True
    obsolete_event = _event_proposal(
        v2_runtime,
        first_plan.plan,
        proposal_id="obsolete-plan-event-proposal",
    )

    decision = v2_runtime.coordinator.submit(obsolete_event)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert ProgressEventRepository(connection).list_all() == ()
        assert ProgressHeadRepository(connection).get("run-1") is None


@pytest.mark.integration
def test_progress_event_same_timestamp_requires_increasing_event_id(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    occurred_at = NOW + timedelta(seconds=1)
    middle = _event_proposal(
        v2_runtime,
        plan_proposal.plan,
        proposal_id="middle-event-proposal",
        event_id="event-b",
        occurred_at=occurred_at,
    )
    lower = _event_proposal(
        v2_runtime,
        plan_proposal.plan,
        proposal_id="lower-event-proposal",
        event_id="event-a",
        occurred_at=occurred_at,
    )
    higher = _event_proposal(
        v2_runtime,
        plan_proposal.plan,
        proposal_id="higher-event-proposal",
        event_id="event-c",
        occurred_at=occurred_at,
    )
    assert v2_runtime.coordinator.submit(middle).accepted is True

    lower_decision = v2_runtime.coordinator.submit(lower)
    higher_decision = v2_runtime.coordinator.submit(higher)

    assert lower_decision.accepted is False
    assert lower_decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    assert higher_decision.accepted is True
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert ProgressEventRepository(connection).list_all() == (middle.event, higher.event)
        assert ProgressHeadRepository(connection).get("run-1") == (
            plan_proposal.plan.plan_version_id,
            higher.event.event_id,
        )


@pytest.mark.integration
def test_checkpoint_requires_a_durable_budget(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    event_proposal = _event_proposal(v2_runtime, plan_proposal.plan)
    assert v2_runtime.coordinator.submit(event_proposal).accepted is True
    checkpoint = _checkpoint_proposal(v2_runtime, plan_proposal.plan, event_proposal.event)

    decision = v2_runtime.coordinator.submit(checkpoint)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.MISSING_ENTITY
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert RunCheckpointRepository(connection).list_all() == ()


@pytest.mark.integration
def test_checkpoint_for_obsolete_plan_version_is_rejected(
    v2_runtime: ProgressRuntime,
) -> None:
    first_plan = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(first_plan).accepted is True
    first_event = _event_proposal(v2_runtime, first_plan.plan)
    assert v2_runtime.coordinator.submit(first_event).accepted is True
    assert v2_runtime.coordinator.submit(_budget_proposal(v2_runtime, first_plan.plan)).accepted
    assert v2_runtime.coordinator.submit(_second_plan_proposal(v2_runtime)).accepted is True
    checkpoint = _checkpoint_proposal(v2_runtime, first_plan.plan, first_event.event)

    decision = v2_runtime.coordinator.submit(checkpoint)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert RunCheckpointRepository(connection).list_all() == ()


@pytest.mark.integration
@pytest.mark.parametrize(
    "pending_dependency_ids",
    ((), ("collect", "collect"), ("collect", "analyze")),
    ids=("missing", "duplicate", "extra"),
)
def test_checkpoint_pending_dependencies_must_match_replay_exactly(
    v2_runtime: ProgressRuntime,
    pending_dependency_ids: tuple[str, ...],
) -> None:
    plan_proposal = v2_runtime.plan_proposal(two_subtasks=True)
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    budget = _budget_proposal(v2_runtime, plan_proposal.plan)
    assert v2_runtime.coordinator.submit(budget).accepted is True
    checkpoint = _checkpoint_proposal(
        v2_runtime,
        plan_proposal.plan,
        None,
        pending_dependency_ids=pending_dependency_ids,
        remaining_budget=_remaining_budget_for(budget.budget),
    )

    decision = v2_runtime.coordinator.submit(checkpoint)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_DEPENDENCY
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert RunCheckpointRepository(connection).list_all() == ()


@pytest.mark.integration
@pytest.mark.parametrize(
    "mismatch",
    ("underreported", "overreported", "cross-category"),
)
def test_checkpoint_remaining_budget_must_reconcile_exactly(
    v2_runtime: ProgressRuntime,
    mismatch: str,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    event_proposal = _event_proposal(v2_runtime, plan_proposal.plan)
    assert v2_runtime.coordinator.submit(event_proposal).accepted is True
    budget = _budget_proposal(
        v2_runtime,
        plan_proposal.plan,
        reserves=_distinct_reserves(),
        usage=_distinct_usage(),
    )
    assert v2_runtime.coordinator.submit(budget).accepted is True
    expected = _remaining_budget_for(budget.budget)
    if mismatch == "underreported":
        exploration = expected.exploration.model_copy(
            update={"cost_usd": expected.exploration.cost_usd - 0.1}
        )
        remaining = expected.model_copy(update={"exploration": exploration})
    elif mismatch == "overreported":
        exploration = expected.exploration.model_copy(
            update={"tokens": expected.exploration.tokens + 1}
        )
        remaining = expected.model_copy(update={"exploration": exploration})
    else:
        remaining = expected.model_copy(
            update={
                "exploration": expected.implementation,
                "implementation": expected.exploration,
            }
        )
    checkpoint = _checkpoint_proposal(
        v2_runtime,
        plan_proposal.plan,
        event_proposal.event,
        remaining_budget=remaining,
    )

    decision = v2_runtime.coordinator.submit(checkpoint)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.UNMATCHED_BUDGETS
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert RunCheckpointRepository(connection).list_all() == ()


@pytest.mark.integration
def test_checkpoint_uses_latest_budget_by_timestamp_then_identifier(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    event_proposal = _event_proposal(v2_runtime, plan_proposal.plan)
    assert v2_runtime.coordinator.submit(event_proposal).accepted is True
    recorded_at = NOW + timedelta(seconds=2)
    earlier_identifier = _budget_proposal(
        v2_runtime,
        plan_proposal.plan,
        proposal_id="budget-a-proposal",
        budget_id="budget-a",
        recorded_at=recorded_at,
        usage=_progress_usage(),
    )
    later_identifier = _budget_proposal(
        v2_runtime,
        plan_proposal.plan,
        proposal_id="budget-b-proposal",
        budget_id="budget-b",
        recorded_at=recorded_at,
        usage=_distinct_usage(),
    )
    assert v2_runtime.coordinator.submit(earlier_identifier).accepted is True
    assert v2_runtime.coordinator.submit(later_identifier).accepted is True
    checkpoint = _checkpoint_proposal(
        v2_runtime,
        plan_proposal.plan,
        event_proposal.event,
        remaining_budget=_remaining_budget_for(earlier_identifier.budget),
    )

    decision = v2_runtime.coordinator.submit(checkpoint)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.UNMATCHED_BUDGETS
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert RunCheckpointRepository(connection).list_all() == ()


@pytest.mark.integration
def test_progress_percent_alone_cannot_authorize_completion_and_false_finish_is_not_projected(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    assert (
        v2_runtime.coordinator.submit(_event_proposal(v2_runtime, plan_proposal.plan)).accepted
        is True
    )
    assert (
        v2_runtime.coordinator.submit(_budget_proposal(v2_runtime, plan_proposal.plan)).accepted
        is True
    )
    false_finish = _completion_transaction(
        v2_runtime,
        plan_proposal.plan,
        final_result=AssessmentOutcome.FAILED,
        voluntary=True,
        termination_reason=TerminationReason.EARLY_EXIT,
        decision_accepted=False,
    )
    _retain_completion_evidence(v2_runtime, false_finish)

    decision = v2_runtime.coordinator.submit(false_finish)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.FALSE_FINISH
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert CompletionDecisionRepository(connection).list_all() == ()


@pytest.mark.integration
def test_incomplete_ordered_checklist_cannot_authorize_success(
    v2_runtime: ProgressRuntime,
) -> None:
    plan_proposal = v2_runtime.plan_proposal()
    assert v2_runtime.coordinator.submit(plan_proposal).accepted is True
    assert (
        v2_runtime.coordinator.submit(_event_proposal(v2_runtime, plan_proposal.plan)).accepted
        is True
    )
    assert (
        v2_runtime.coordinator.submit(_budget_proposal(v2_runtime, plan_proposal.plan)).accepted
        is True
    )
    completion = _completion_transaction(
        v2_runtime,
        plan_proposal.plan,
        final_result=AssessmentOutcome.PASSED,
        voluntary=False,
        termination_reason=TerminationReason.SUCCESS,
        decision_accepted=True,
    )
    checklist = (
        completion.completion_proposal.checklist[0].model_copy(update={"completed": False}),
        *completion.completion_proposal.checklist[1:],
    )
    incomplete = completion.model_copy(
        update={
            "completion_proposal": completion.completion_proposal.model_copy(
                update={"checklist": checklist}
            ),
            "completion_decision": completion.completion_decision.model_copy(
                update={"accepted": False, "checklist": checklist}
            ),
        }
    )
    _retain_completion_evidence(v2_runtime, incomplete)

    decision = v2_runtime.coordinator.submit(incomplete)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert CompletionDecisionRepository(connection).list_all() == ()


@pytest.mark.integration
def test_completion_rejects_empty_final_validation_evidence(
    v2_runtime: ProgressRuntime,
) -> None:
    plan = _submit_completion_prerequisites(v2_runtime)
    completion = _completion_transaction(
        v2_runtime,
        plan,
        final_result=AssessmentOutcome.PASSED,
        voluntary=False,
        termination_reason=TerminationReason.SUCCESS,
        decision_accepted=True,
    )
    _retain_evidence(
        v2_runtime,
        tuple(
            evidence_id
            for item in completion.completion_proposal.checklist
            for evidence_id in item.evidence_ids
        ),
    )
    empty_final = completion.completion_proposal.final_validation.model_copy(
        update={"evidence_ids": ()}
    )
    invalid = completion.model_copy(
        update={
            "completion_proposal": completion.completion_proposal.model_copy(
                update={"final_validation": empty_final}
            )
        }
    )

    decision = v2_runtime.coordinator.submit(invalid)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert CompletionDecisionRepository(connection).list_all() == ()


@pytest.mark.integration
def test_completion_rejects_empty_evidence_on_a_completed_checklist_step(
    v2_runtime: ProgressRuntime,
) -> None:
    plan = _submit_completion_prerequisites(v2_runtime)
    completion = _completion_transaction(
        v2_runtime,
        plan,
        final_result=AssessmentOutcome.PASSED,
        voluntary=False,
        termination_reason=TerminationReason.SUCCESS,
        decision_accepted=True,
    )
    _retain_completion_evidence(v2_runtime, completion)
    checklist = (
        completion.completion_proposal.checklist[0].model_copy(update={"evidence_ids": ()}),
        *completion.completion_proposal.checklist[1:],
    )
    invalid = completion.model_copy(
        update={
            "completion_proposal": completion.completion_proposal.model_copy(
                update={"checklist": checklist}
            ),
            "completion_decision": completion.completion_decision.model_copy(
                update={"checklist": checklist}
            ),
        }
    )

    decision = v2_runtime.coordinator.submit(invalid)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert CompletionDecisionRepository(connection).list_all() == ()


@pytest.mark.integration
@pytest.mark.parametrize("evidence_location", ("checklist", "final_validation"))
def test_completion_rejects_nonexistent_evidence_ids(
    v2_runtime: ProgressRuntime,
    evidence_location: str,
) -> None:
    plan = _submit_completion_prerequisites(v2_runtime)
    completion = _completion_transaction(
        v2_runtime,
        plan,
        final_result=AssessmentOutcome.PASSED,
        voluntary=False,
        termination_reason=TerminationReason.SUCCESS,
        decision_accepted=True,
    )
    _retain_completion_evidence(v2_runtime, completion)
    if evidence_location == "checklist":
        checklist = (
            completion.completion_proposal.checklist[0].model_copy(
                update={"evidence_ids": ("missing-completion-evidence",)}
            ),
            *completion.completion_proposal.checklist[1:],
        )
        invalid = completion.model_copy(
            update={
                "completion_proposal": completion.completion_proposal.model_copy(
                    update={"checklist": checklist}
                ),
                "completion_decision": completion.completion_decision.model_copy(
                    update={"checklist": checklist}
                ),
            }
        )
    else:
        final_validation = completion.completion_proposal.final_validation.model_copy(
            update={"evidence_ids": ("missing-final-evidence",)}
        )
        invalid = completion.model_copy(
            update={
                "completion_proposal": completion.completion_proposal.model_copy(
                    update={"final_validation": final_validation}
                )
            }
        )

    decision = v2_runtime.coordinator.submit(invalid)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert CompletionDecisionRepository(connection).list_all() == ()


def _assert_accepted_exact_replay(runtime: ProgressRuntime, proposal: object) -> None:
    first = runtime.coordinator.submit(proposal)
    second = runtime.coordinator.submit(proposal)

    assert first.accepted is True
    assert second == first.model_copy(update={"replayed": True})


def _rejection_code(runtime: ProgressRuntime, proposal: object) -> RejectionCode:
    decision = runtime.coordinator.submit(proposal)

    assert decision.accepted is False
    assert decision.reasons
    return decision.reasons[0].code


def _submit_completion_prerequisites(runtime: ProgressRuntime) -> ProgressPlan:
    plan_proposal = runtime.plan_proposal()
    assert runtime.coordinator.submit(plan_proposal).accepted is True
    assert runtime.coordinator.submit(_event_proposal(runtime, plan_proposal.plan)).accepted is True
    budget = _budget_proposal(runtime, plan_proposal.plan)
    assert runtime.coordinator.submit(budget).accepted is True
    return plan_proposal.plan


def _retain_completion_evidence(runtime: ProgressRuntime, completion: DecideCompletion) -> None:
    evidence_ids = (
        *(
            evidence_id
            for item in completion.completion_proposal.checklist
            for evidence_id in item.evidence_ids
        ),
        *completion.completion_proposal.final_validation.evidence_ids,
    )
    _retain_evidence(runtime, evidence_ids)


def _retain_evidence(runtime: ProgressRuntime, evidence_ids: tuple[str, ...]) -> None:
    for evidence_id in evidence_ids:
        artifact = runtime.artifact_store.put(evidence_id.encode("utf-8"), "text/plain")
        decision = runtime.coordinator.submit(
            AddEvidence(
                proposal_id=f"retain-{evidence_id}",
                idempotency_key=f"retain-{evidence_id}-key",
                proposer=runtime.proposer,
                evidence=EvidenceRecord(
                    evidence_id=evidence_id,
                    evidence_type="completion-gate",
                    source_locator=f"fixture://{evidence_id}",
                    retrieved_at=NOW,
                    artifact=artifact,
                    provenance={"collector": "progress-service-test"},
                    ingestion_actor_id=runtime.proposer.actor_id,
                ),
            )
        )
        assert decision.accepted is True


def _plan_proposal_with_validator(
    runtime: ProgressRuntime,
    validator: ActorIdentity,
) -> RecordProgressPlan:
    plan_version_id = "plan-creator-alias"
    return RecordProgressPlan(
        proposal_id="creator-alias-plan-proposal",
        idempotency_key="creator-alias-plan-key",
        proposer=runtime.proposer,
        approval=runtime.approval(),
        plan=ProgressPlan(
            plan_version_id=plan_version_id,
            run_id="run-1",
            version=1,
            subtasks=(
                ProgressSubtask(
                    subtask_id="creator-alias-subtask",
                    plan_version_id=plan_version_id,
                    description="Validate creator alias handling",
                    dependency_ids=(),
                    completion_criteria=("Alias rejected",),
                    validator=validator,
                    validator_version="creator-validator-v1",
                    weight=Decimal("1.00"),
                    evidence_requirements=("alias-evidence",),
                    order=1,
                ),
            ),
            created_at=NOW,
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )


def _second_plan_proposal(runtime: ProgressRuntime) -> RecordProgressPlan:
    plan_version_id = "plan-version-2"
    return RecordProgressPlan(
        proposal_id="plan-proposal-2",
        idempotency_key="plan-key-2",
        proposer=runtime.proposer,
        approval=runtime.approval(),
        plan=ProgressPlan(
            plan_version_id=plan_version_id,
            run_id="run-1",
            version=2,
            subtasks=(
                _subtask(
                    "collect-v2",
                    (),
                    1,
                    Decimal("1.00"),
                    plan_version_id,
                    runtime,
                ),
            ),
            created_at=NOW + timedelta(seconds=2),
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )


def _event_proposal(
    runtime: ProgressRuntime,
    plan: ProgressPlan,
    *,
    proposal_id: str = "event-proposal-1",
    proposer: ActorIdentity | None = None,
    validator: ActorIdentity | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
) -> AppendProgressEvent:
    completion_proposer = proposer or runtime.proposer
    declared_validator = validator or plan.subtasks[0].validator
    return AppendProgressEvent(
        proposal_id=proposal_id,
        idempotency_key=f"{proposal_id}-key",
        proposer=completion_proposer,
        approval=runtime.approval(),
        event=ProgressValidationEvent(
            event_id=event_id or f"event-{proposal_id}",
            run_id=plan.run_id,
            plan_version_id=plan.plan_version_id,
            subtask_id=plan.subtasks[0].subtask_id,
            requested_status=ProgressStatus.VALIDATED,
            completion_proposer=completion_proposer,
            validator=declared_validator,
            validator_version=plan.subtasks[0].validator_version,
            validator_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            relationship_to_run_creator=ActorRelationship.INDEPENDENT,
            relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
            are_independent=True,
            evidence_ids=("evidence-validated",),
            checks_run=("progress-check",),
            assumptions=(),
            limitations=("Limited to retained artifacts",),
            result=AssessmentOutcome.PASSED,
            occurred_at=occurred_at or NOW + timedelta(seconds=1),
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )


def _budget_proposal(
    runtime: ProgressRuntime,
    plan: ProgressPlan,
    *,
    proposal_id: str = "budget-proposal-1",
    budget_id: str = "budget-1",
    recorded_at: datetime | None = None,
    reserves: BudgetReserves | None = None,
    usage: BudgetUsage | None = None,
) -> RecordRunBudget:
    return RecordRunBudget(
        proposal_id=proposal_id,
        idempotency_key=f"{proposal_id}-key",
        proposer=runtime.proposer,
        approval=runtime.approval(),
        budget=BudgetAllocation(
            budget_id=budget_id,
            run_id=plan.run_id,
            plan_version_id=plan.plan_version_id,
            reserves=reserves or _progress_reserves(),
            usage=usage or _progress_usage(),
            telemetry=_telemetry(),
            recorded_at=recorded_at or NOW + timedelta(seconds=2),
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )


def _checkpoint_proposal(
    runtime: ProgressRuntime,
    plan: ProgressPlan,
    event: ProgressValidationEvent | None,
    *,
    pending_dependency_ids: tuple[str, ...] = (),
    remaining_budget: BudgetReserves | None = None,
) -> RecordRunCheckpoint:
    return RecordRunCheckpoint(
        proposal_id="checkpoint-proposal-1",
        idempotency_key="checkpoint-key-1",
        proposer=runtime.proposer,
        approval=runtime.approval(),
        checkpoint=RunCheckpoint(
            checkpoint_id="checkpoint-1",
            run_id=plan.run_id,
            plan_version_id=plan.plan_version_id,
            validated_subtask_ids=() if event is None else (event.subtask_id,),
            pending_dependency_ids=pending_dependency_ids,
            hypothesis_ids=("hypothesis-1",),
            artifact_refs=(_artifact("b", "application/json"),),
            environment_snapshot_id="environment-1",
            attempted_operations=("operation-1",),
            failures=(),
            remaining_budget=remaining_budget
            or _remaining_budget_for(_budget_proposal(runtime, plan).budget),
            next_recommended_action="Run final validation",
            raw_log_refs=(_artifact("c", "application/jsonl"),),
            raw_transaction_refs=(_artifact("d", "application/json"),),
            telemetry=_telemetry(),
            occurred_at=NOW + timedelta(seconds=3),
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )


def _completion_transaction(
    runtime: ProgressRuntime,
    plan: ProgressPlan,
    *,
    final_result: AssessmentOutcome,
    voluntary: bool,
    termination_reason: TerminationReason,
    decision_accepted: bool,
) -> DecideCompletion:
    checklist = tuple(
        CompletionChecklistItem(
            step=step,
            completed=True,
            detail=f"Completed {step.value}",
            evidence_ids=(f"completion-evidence-{index}",),
        )
        for index, step in enumerate(CompletionChecklistStep, start=1)
    )
    final_validation = AssessmentProvenance(
        actor=runtime.validator,
        actor_version="final-validator-v1",
        category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        deterministic_or_learned="DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=(),
        evidence_ids=("final-evidence",),
        checks_run=("final-check",),
        limitations=("Limited to retained artifacts",),
        result=final_result,
        meaningful_confidence=None,
        assessed_at=NOW + timedelta(seconds=4),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    proposal = CompletionProposal(
        completion_proposal_id="completion-proposal-1",
        run_id=plan.run_id,
        plan_version_id=plan.plan_version_id,
        proposer=runtime.proposer,
        voluntary_termination=voluntary,
        claims_completion=True,
        termination_reason=termination_reason,
        checklist=checklist,
        final_validation=final_validation,
        relationship_to_run_creator=ActorRelationship.INDEPENDENT,
        relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
        are_independent=True,
        submitted_at=NOW + timedelta(seconds=5),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    finding = detect_false_finish(
        voluntary_termination=voluntary,
        claims_completion=True,
        final_validator_result=final_result,
        validated_weight=Decimal("1.00"),
        unused_budget=True,
    )
    decision = CompletionDecision(
        completion_decision_id="completion-decision-1",
        run_id=plan.run_id,
        plan_version_id=plan.plan_version_id,
        completion_proposal_id=proposal.completion_proposal_id,
        decision_authority=runtime.validator,
        accepted=decision_accepted,
        checklist=checklist,
        final_validator_result=final_result,
        false_finish=finding,
        termination_reason=termination_reason,
        decided_at=NOW + timedelta(seconds=6),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    return DecideCompletion(
        proposal_id="completion-transaction-1",
        idempotency_key="completion-key-1",
        proposer=runtime.proposer,
        approval=runtime.approval(),
        completion_proposal=proposal,
        completion_decision=decision,
    )


def _artifact(character: str, media_type: str) -> ArtifactRef:
    digest = character * 64
    return ArtifactRef(
        sha256=digest,
        size_bytes=1,
        media_type=media_type,
        relative_path=f"sha256/{character * 2}/{digest}",
    )


def _progress_reserves() -> BudgetReserves:
    budget = _resource_budget()
    return BudgetReserves(
        exploration=budget,
        implementation=budget,
        verification=budget,
        recovery=budget,
        finalization=budget,
    )


def _progress_usage() -> BudgetUsage:
    usage = ResourceUsage(
        cost_usd=1.0,
        compute_units=1.0,
        tokens=10,
        elapsed_seconds=10.0,
        tool_calls=1,
        human_interventions=0,
    )
    return BudgetUsage(
        exploration=usage,
        implementation=usage,
        verification=usage,
        recovery=usage,
        finalization=usage,
    )


def _distinct_reserves() -> BudgetReserves:
    budget = _resource_budget()
    return BudgetReserves(
        exploration=budget.model_copy(update={"cost_usd": 100.1}),
        implementation=budget.model_copy(update={"cost_usd": 80.2}),
        verification=budget.model_copy(update={"cost_usd": 60.3}),
        recovery=budget.model_copy(update={"cost_usd": 40.4}),
        finalization=budget.model_copy(update={"cost_usd": 20.5}),
    )


def _distinct_usage() -> BudgetUsage:
    usage = _progress_usage().exploration
    return BudgetUsage(
        exploration=usage.model_copy(update={"cost_usd": 0.2}),
        implementation=usage.model_copy(update={"cost_usd": 1.1}),
        verification=usage.model_copy(update={"cost_usd": 2.2}),
        recovery=usage.model_copy(update={"cost_usd": 3.3}),
        finalization=usage.model_copy(update={"cost_usd": 4.4}),
    )


def _remaining_budget_for(allocation: BudgetAllocation) -> BudgetReserves:
    def remaining(category: str) -> ResourceBudget:
        reserve = getattr(allocation.reserves, category)
        usage = getattr(allocation.usage, category)
        return ResourceBudget(
            cost_usd=float(Decimal(str(reserve.cost_usd)) - Decimal(str(usage.cost_usd))),
            compute_units=float(
                Decimal(str(reserve.compute_units)) - Decimal(str(usage.compute_units))
            ),
            tokens=reserve.tokens - usage.tokens,
            elapsed_seconds=float(
                Decimal(str(reserve.elapsed_seconds)) - Decimal(str(usage.elapsed_seconds))
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


def _runtime(
    tmp_path: Path,
    policy: GovernancePolicyV1 | GovernancePolicyV2,
) -> Iterator[ProgressRuntime]:
    database_path = tmp_path / "progress.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    snapshot = PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    proposer = _actor("run-creator")
    approver = _actor("human-approver")
    validator = _actor("final-validator")

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(snapshot, NOW)
    coordinator = TransactionCoordinator(uow_factory, snapshot, FixedClock(), artifact_store)
    runtime = ProgressRuntime(
        coordinator=coordinator,
        uow_factory=uow_factory,
        policy=snapshot,
        proposer=proposer,
        approver=approver,
        validator=validator,
        artifact_store=artifact_store,
        database_path=database_path,
    )
    if isinstance(policy, GovernancePolicyV2):
        run_decision = coordinator.submit(
            CreateResearchRun(
                proposal_id="run-proposal-1",
                idempotency_key="run-key-1",
                proposer=proposer,
                approval=runtime.approval(),
                run=_research_run(runtime),
            )
        )
        assert run_decision.accepted is True
    yield runtime
    engine.dispose()


def _v2_policy(
    *,
    protected_evaluation_required: bool = False,
    rollback_required: bool = False,
) -> GovernancePolicyV2:
    return GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset(),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.RESEARCH_PROCESS,
                persistence=PersistenceScope.RUN_LOCAL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=protected_evaluation_required,
                rollback_required=rollback_required,
            ),
        ),
    )


def _actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=NOW)


def _resource_budget() -> ResourceBudget:
    return ResourceBudget(
        cost_usd=100.0,
        compute_units=100.0,
        tokens=10_000,
        elapsed_seconds=1_000.0,
        tool_calls=100,
        human_interventions=10,
    )


def _research_run(runtime: ProgressRuntime) -> ResearchRun:
    budget = _resource_budget()
    return ResearchRun(
        run_id="run-1",
        charter="Complete the governed research task",
        scope=("Task 6",),
        creator=runtime.proposer,
        created_at=NOW,
        active_governance_policy_hash=runtime.policy.policy_hash,
        model_configuration_version_id=None,
        scaffold_configuration_version_id=None,
        budget_allocation=RunBudgetAllocation(
            execution=budget,
            search=budget,
            evaluation=budget,
            judging=budget,
            human=budget,
        ),
        final_validator=runtime.validator,
        final_validator_version="final-validator-v1",
        environment_snapshot_id="environment-1",
    )


def _subtask(
    subtask_id: str,
    dependency_ids: tuple[str, ...],
    order: int,
    weight: Decimal,
    plan_version_id: str,
    runtime: ProgressRuntime,
) -> ProgressSubtask:
    return ProgressSubtask(
        subtask_id=subtask_id,
        plan_version_id=plan_version_id,
        description=f"Complete {subtask_id}",
        dependency_ids=dependency_ids,
        completion_criteria=(f"{subtask_id} independently checked",),
        validator=runtime.validator,
        validator_version="final-validator-v1",
        weight=weight,
        evidence_requirements=(f"evidence-{subtask_id}",),
        order=order,
    )
