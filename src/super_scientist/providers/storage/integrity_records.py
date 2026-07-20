from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ReviewerAssessment,
    RuleConsolidationDecision,
    RuleIncident,
    RuleRegressionCase,
    RuleStatus,
)
from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorCollapseRecord,
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.evidence_trails.models import (
    EvidenceTrailNode,
    EvidenceTrailRelation,
    EvidenceTrailVersion,
    ReportSentenceBinding,
    TrailAssessment,
    TrailCheckResult,
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

if TYPE_CHECKING:
    from super_scientist.providers.storage.domain_records import (
        PrimitiveEvaluationRecord,
        PrimitiveStatus,
        PrimitiveVersionRecord,
        VerificationMechanismSpecRecord,
        VerificationResultRecord,
    )


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


@dataclass(frozen=True)
class TrailIntegritySnapshot:
    """Fixed read-only evidence-trail view used only for transaction replay."""

    versions: tuple[EvidenceTrailVersion, ...]
    nodes: tuple[EvidenceTrailNode, ...]
    relations: tuple[EvidenceTrailRelation, ...]
    checks: tuple[TrailCheckResult, ...]
    assessments: tuple[TrailAssessment, ...]
    bindings: tuple[ReportSentenceBinding, ...]
    heads: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True)
class RuleIntegritySnapshot:
    """Fixed read-only behavioral-rule view used only for transaction replay."""

    incidents: tuple[RuleIncident, ...]
    versions: tuple[BehavioralRuleVersion, ...]
    assessments: tuple[ReviewerAssessment, ...]
    decisions: tuple[RuleConsolidationDecision, ...]
    regressions: tuple[RuleRegressionCase, ...]
    heads: tuple[tuple[str, str, str, RuleStatus], ...]


@dataclass(frozen=True)
class RepresentationIntegritySnapshot:
    """Fixed read-only representation view used only for transaction replay."""

    versions: tuple[PrimitiveVersionRecord, ...]
    evaluations: tuple[PrimitiveEvaluationRecord, ...]
    verification_mechanisms: tuple[VerificationMechanismSpecRecord, ...]
    verification_results: tuple[VerificationResultRecord, ...]
    heads: tuple[tuple[str, str, str, PrimitiveStatus], ...]
