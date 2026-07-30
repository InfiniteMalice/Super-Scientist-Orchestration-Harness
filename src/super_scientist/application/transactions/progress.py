from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Connection

from super_scientist.application.progress.service import (
    AppendProgressEventHandler,
    DecideCompletionHandler,
    RecordProgressPlanHandler,
    RecordRunBudgetHandler,
    RecordRunCheckpointHandler,
)
from super_scientist.application.transactions.contracts import ProposalHandler
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.primitives import UtcTimestamp
from super_scientist.domain.progress.calculations import event_advances_progress_head
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    CompletionDecision,
    ProgressPlan,
    ProgressSubtask,
    ProgressValidationEvent,
    RunCheckpoint,
)
from super_scientist.domain.research_runs.models import ResearchRun
from super_scientist.kernel.transactions.models import (
    AppendProgressEvent,
    DecideCompletion,
    RecordProgressPlan,
    RecordRunBudget,
    RecordRunCheckpoint,
)
from super_scientist.providers.storage.domain_records import (
    CompletionDecisionRepository,
    ProgressEventRepository,
    ProgressHeadRepository,
    ProgressPlanRepository,
    ProgressSubtaskRepository,
    ResearchRunRepository,
    RunBudgetRepository,
    RunCheckpointRepository,
)
from super_scientist.providers.storage.repositories import EvidenceRepository

type FixedProgressHandler = ProposalHandler[BaseModel, BaseModel]


@dataclass(frozen=True)
class _RetainedEvidenceReader:
    """Expose only retained-evidence existence to completion admission."""

    _repository: EvidenceRepository

    def contains(self, evidence_id: str) -> bool:
        return self._repository.get(evidence_id) is not None


@dataclass(frozen=True)
class ProgressPlanCapabilities:
    active_policy: PolicySnapshot
    runs: ResearchRunRepository
    plans: ProgressPlanRepository
    subtasks: ProgressSubtaskRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None:
        return self.plans.get(plan_version_id)

    def list_plans(self, run_id: str) -> tuple[ProgressPlan, ...]:
        return self.plans.list_for_run(run_id)

    def list_subtasks(self, subtask_ids: tuple[str, ...]) -> tuple[ProgressSubtask, ...]:
        return self.subtasks.get_many(subtask_ids)

    def append_authoritative(self, record: BaseModel) -> None:
        if isinstance(record, ProgressPlan):
            self.plans.add(record.plan_version_id, record, record.created_at)
            return
        if isinstance(record, ProgressSubtask):
            self.subtasks.add(record.subtask_id, record, self.created_at)
            return
        raise TypeError(f"unsupported progress-plan authoritative record: {type(record)!r}")

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("progress plans have no mutable projection before an event")


@dataclass(frozen=True)
class ProgressEventCapabilities:
    active_policy: PolicySnapshot
    runs: ResearchRunRepository
    plans: ProgressPlanRepository
    subtasks: ProgressSubtaskRepository
    events: ProgressEventRepository
    head: ProgressHeadRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None:
        return self.plans.get(plan_version_id)

    def get_subtask(self, subtask_id: str) -> ProgressSubtask | None:
        return self.subtasks.get(subtask_id)

    def get_event(self, event_id: str) -> ProgressValidationEvent | None:
        return self.events.get(event_id)

    def list_plans(self, run_id: str) -> tuple[ProgressPlan, ...]:
        return self.plans.list_for_run(run_id)

    def get_progress_head_event(self, run_id: str) -> ProgressValidationEvent | None:
        head = self.head.get(run_id)
        if head is None:
            return None
        return self.events.get(head[1])

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, ProgressValidationEvent):
            raise TypeError(f"unsupported progress event record: {type(record)!r}")
        self.events.add(record.event_id, record, record.occurred_at)

    def update_projection(self, record: BaseModel) -> None:
        if not isinstance(record, ProgressValidationEvent):
            raise TypeError(f"unsupported progress head record: {type(record)!r}")
        if not event_advances_progress_head(
            record,
            self.list_plans(record.run_id),
            self.get_progress_head_event(record.run_id),
        ):
            raise RuntimeError("progress event cannot rewind the durable head")
        self.head.set(record.run_id, record.plan_version_id, record.event_id)


@dataclass(frozen=True)
class RunBudgetCapabilities:
    active_policy: PolicySnapshot
    runs: ResearchRunRepository
    plans: ProgressPlanRepository
    budgets: RunBudgetRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None:
        return self.plans.get(plan_version_id)

    def get_budget(self, budget_id: str) -> BudgetAllocation | None:
        return self.budgets.get(budget_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, BudgetAllocation):
            raise TypeError(f"unsupported run budget record: {type(record)!r}")
        self.budgets.add(record.budget_id, record, record.recorded_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("run budgets have no mutable projection")


@dataclass(frozen=True)
class RunCheckpointCapabilities:
    active_policy: PolicySnapshot
    runs: ResearchRunRepository
    plans: ProgressPlanRepository
    events: ProgressEventRepository
    budgets: RunBudgetRepository
    checkpoints: RunCheckpointRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None:
        return self.plans.get(plan_version_id)

    def get_checkpoint(self, checkpoint_id: str) -> RunCheckpoint | None:
        return self.checkpoints.get(checkpoint_id)

    def list_events(self, plan_version_id: str) -> tuple[ProgressValidationEvent, ...]:
        return self.events.list_for_plan(plan_version_id)

    def list_plans(self, run_id: str) -> tuple[ProgressPlan, ...]:
        return self.plans.list_for_run(run_id)

    def list_budgets(self, plan_version_id: str) -> tuple[BudgetAllocation, ...]:
        return self.budgets.list_for_plan(plan_version_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, RunCheckpoint):
            raise TypeError(f"unsupported run checkpoint record: {type(record)!r}")
        self.checkpoints.add(record.checkpoint_id, record, record.occurred_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("run checkpoints have no mutable projection")


@dataclass(frozen=True)
class CompletionCapabilities:
    active_policy: PolicySnapshot
    runs: ResearchRunRepository
    plans: ProgressPlanRepository
    events: ProgressEventRepository
    budgets: RunBudgetRepository
    decisions: CompletionDecisionRepository
    evidence: _RetainedEvidenceReader

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None:
        return self.plans.get(plan_version_id)

    def get_completion_decision(
        self,
        completion_decision_id: str,
    ) -> CompletionDecision | None:
        return self.decisions.get(completion_decision_id)

    def list_events(self, plan_version_id: str) -> tuple[ProgressValidationEvent, ...]:
        return self.events.list_for_plan(plan_version_id)

    def list_budgets(self, plan_version_id: str) -> tuple[BudgetAllocation, ...]:
        return self.budgets.list_for_plan(plan_version_id)

    def has_retained_evidence(self, evidence_id: str) -> bool:
        return self.evidence.contains(evidence_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, CompletionDecision):
            raise TypeError(f"unsupported completion decision record: {type(record)!r}")
        self.decisions.add(
            record.completion_decision_id,
            record,
            record.decided_at,
        )

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("completion decisions have no mutable projection")


def fixed_progress_handlers() -> tuple[FixedProgressHandler, ...]:
    return (  # type: ignore[return-value]
        RecordProgressPlanHandler(),
        AppendProgressEventHandler(),
        RecordRunBudgetHandler(),
        RecordRunCheckpointHandler(),
        DecideCompletionHandler(),
    )


def progress_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
) -> (
    ProgressPlanCapabilities
    | ProgressEventCapabilities
    | RunBudgetCapabilities
    | RunCheckpointCapabilities
    | CompletionCapabilities
):
    if isinstance(proposal, RecordProgressPlan):
        return ProgressPlanCapabilities(
            active_policy=active_policy,
            runs=ResearchRunRepository(connection),
            plans=ProgressPlanRepository(connection),
            subtasks=ProgressSubtaskRepository(connection),
            created_at=proposal.plan.created_at,
        )
    if isinstance(proposal, AppendProgressEvent):
        return ProgressEventCapabilities(
            active_policy=active_policy,
            runs=ResearchRunRepository(connection),
            plans=ProgressPlanRepository(connection),
            subtasks=ProgressSubtaskRepository(connection),
            events=ProgressEventRepository(connection),
            head=ProgressHeadRepository(connection),
        )
    if isinstance(proposal, RecordRunBudget):
        return RunBudgetCapabilities(
            active_policy=active_policy,
            runs=ResearchRunRepository(connection),
            plans=ProgressPlanRepository(connection),
            budgets=RunBudgetRepository(connection),
        )
    if isinstance(proposal, RecordRunCheckpoint):
        return RunCheckpointCapabilities(
            active_policy=active_policy,
            runs=ResearchRunRepository(connection),
            plans=ProgressPlanRepository(connection),
            events=ProgressEventRepository(connection),
            budgets=RunBudgetRepository(connection),
            checkpoints=RunCheckpointRepository(connection),
        )
    if isinstance(proposal, DecideCompletion):
        return CompletionCapabilities(
            active_policy=active_policy,
            runs=ResearchRunRepository(connection),
            plans=ProgressPlanRepository(connection),
            events=ProgressEventRepository(connection),
            budgets=RunBudgetRepository(connection),
            decisions=CompletionDecisionRepository(connection),
            evidence=_RetainedEvidenceReader(EvidenceRepository(connection)),
        )
    raise TypeError(f"no fixed progress capability for proposal: {type(proposal)!r}")
