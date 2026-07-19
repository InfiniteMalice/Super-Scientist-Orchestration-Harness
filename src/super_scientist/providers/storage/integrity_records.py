from __future__ import annotations

from dataclasses import dataclass

from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorCollapseRecord,
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.improvement.models import (
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    CompletionDecision,
    ProgressPlan,
    ProgressSubtask,
    ProgressValidationEvent,
    RunCheckpoint,
)
from super_scientist.domain.research_runs.models import ResearchRun, ResearchRunEvent


@dataclass(frozen=True)
class AdaptationIntegritySnapshot:
    """Fixed read-only storage view used only for whole-workspace reconstruction."""

    research_runs: tuple[ResearchRun, ...]
    research_run_events: tuple[ResearchRunEvent, ...]
    configuration_versions: tuple[ConfigurationVersion, ...]
    evaluator_audits: tuple[EvaluatorAuditRecord, ...]
    measurements: tuple[SelfImprovementMeasurementRecord, ...]
    evaluator_versions: tuple[EvaluatorVersion, ...]
    evaluator_succession_decisions: tuple[EvaluatorSuccessionDecision, ...]
    evaluator_collapse_records: tuple[EvaluatorCollapseRecord, ...]
    research_run_heads: tuple[tuple[str, str], ...]
    evaluator_head: str | None


@dataclass(frozen=True)
class ProgressIntegritySnapshot:
    """Fixed read-only progress storage view used only for transaction replay."""

    plans: tuple[ProgressPlan, ...]
    subtasks: tuple[ProgressSubtask, ...]
    events: tuple[ProgressValidationEvent, ...]
    budgets: tuple[BudgetAllocation, ...]
    checkpoints: tuple[RunCheckpoint, ...]
    completion_decisions: tuple[CompletionDecision, ...]
    heads: tuple[tuple[str, str, str], ...]
