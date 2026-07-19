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
from super_scientist.domain.evidence.models import ArtifactRef
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
        assert len(repositories.transactions.list_all()) == 6
        assert len(repositories.audit.list_all()) == 6


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

    decision = v2_runtime.coordinator.submit(incomplete)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
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


def _event_proposal(
    runtime: ProgressRuntime,
    plan: ProgressPlan,
    *,
    proposal_id: str = "event-proposal-1",
    proposer: ActorIdentity | None = None,
    validator: ActorIdentity | None = None,
) -> AppendProgressEvent:
    completion_proposer = proposer or runtime.proposer
    declared_validator = validator or plan.subtasks[0].validator
    return AppendProgressEvent(
        proposal_id=proposal_id,
        idempotency_key=f"{proposal_id}-key",
        proposer=completion_proposer,
        approval=runtime.approval(),
        event=ProgressValidationEvent(
            event_id=f"event-{proposal_id}",
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
            occurred_at=NOW + timedelta(seconds=1),
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )


def _budget_proposal(
    runtime: ProgressRuntime,
    plan: ProgressPlan,
) -> RecordRunBudget:
    return RecordRunBudget(
        proposal_id="budget-proposal-1",
        idempotency_key="budget-key-1",
        proposer=runtime.proposer,
        approval=runtime.approval(),
        budget=BudgetAllocation(
            budget_id="budget-1",
            run_id=plan.run_id,
            plan_version_id=plan.plan_version_id,
            reserves=_progress_reserves(),
            usage=_progress_usage(),
            telemetry=_telemetry(),
            recorded_at=NOW + timedelta(seconds=2),
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )


def _checkpoint_proposal(
    runtime: ProgressRuntime,
    plan: ProgressPlan,
    event: ProgressValidationEvent,
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
            validated_subtask_ids=(event.subtask_id,),
            pending_dependency_ids=(),
            hypothesis_ids=("hypothesis-1",),
            artifact_refs=(_artifact("b", "application/json"),),
            environment_snapshot_id="environment-1",
            attempted_operations=("operation-1",),
            failures=(),
            remaining_budget=_progress_reserves(),
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


def _v2_policy() -> GovernancePolicyV2:
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
                protected_evaluation_required=False,
                rollback_required=False,
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
