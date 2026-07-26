"""Matched-budget, output-only protected harness evaluation."""

from super_scientist.application.harness_eval.capabilities import (
    CampaignCoordinatorCapability,
    CandidateExecutionContext,
    DecisionAuthorityCapability,
    EvaluatorExecutorCapability,
    InMemoryPublicTaskInputReader,
    OutputOnlyEvaluatorExecutor,
    PublicTaskInputReader,
)
from super_scientist.application.harness_eval.service import (
    HarnessEvaluationService,
    campaign_export_bytes,
    compare_budgets,
    decide_campaign,
)

__all__ = [
    "CampaignCoordinatorCapability",
    "CandidateExecutionContext",
    "DecisionAuthorityCapability",
    "EvaluatorExecutorCapability",
    "HarnessEvaluationService",
    "InMemoryPublicTaskInputReader",
    "OutputOnlyEvaluatorExecutor",
    "PublicTaskInputReader",
    "campaign_export_bytes",
    "compare_budgets",
    "decide_campaign",
]
