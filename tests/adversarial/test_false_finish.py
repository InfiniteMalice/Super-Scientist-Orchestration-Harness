from __future__ import annotations

from decimal import Decimal

from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.progress.calculations import detect_false_finish
from super_scientist.domain.progress.models import FalseFinishResult


def test_voluntary_failed_validation_with_progress_and_budget_is_false_finish() -> None:
    finding = detect_false_finish(
        voluntary_termination=True,
        claims_completion=True,
        final_validator_result=AssessmentOutcome.FAILED,
        validated_weight=Decimal("0.40"),
        unused_budget=True,
    )

    assert finding.result is FalseFinishResult.FALSE_FINISH
    assert finding.final_validator_failed is True
    assert finding.meaningful_validated_progress is True


def test_false_finish_requires_every_conjunct() -> None:
    baseline = {
        "voluntary_termination": True,
        "claims_completion": True,
        "final_validator_result": AssessmentOutcome.FAILED,
        "validated_weight": Decimal("0.40"),
        "unused_budget": True,
    }
    counterexamples = (
        {"voluntary_termination": False},
        {"claims_completion": False},
        {"final_validator_result": AssessmentOutcome.PASSED},
        {"validated_weight": Decimal("0.00")},
        {"unused_budget": False},
    )

    for override in counterexamples:
        finding = detect_false_finish(**(baseline | override))
        assert finding.result is FalseFinishResult.NOT_FALSE_FINISH
