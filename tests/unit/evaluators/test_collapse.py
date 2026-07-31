from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.evaluators.models import (
    CollapseFinding,
    CollapseFindingCode,
    CollapseMetrics,
    EvaluatorCollapseReport,
    assess_collapse_report_only,
)

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def test_collapse_report_preserves_every_design_dimension_separately() -> None:
    report = _report()

    assert tuple(CollapseMetrics.model_fields) == (
        "protected_performance",
        "external_performance",
        "calibration",
        "response_diversity",
        "hypothesis_diversity",
        "source_diversity",
        "experiment_diversity",
        "adapter_output_entropy",
        "repeated_error_rate",
        "confidence_error_coupling",
        "evaluator_disagreement",
        "catastrophic_regression",
        "task_distribution_narrowing",
        "externally_grounded_data_proportion",
    )
    assert "aggregate_score" not in report.model_dump()
    assert "promotion_score" not in report.model_dump()


def test_high_apparent_average_cannot_mask_catastrophic_regression() -> None:
    metrics = _metrics().model_copy(
        update={
            "protected_performance": 0.99,
            "external_performance": 0.99,
            "calibration": 0.99,
            "catastrophic_regression": 1.0,
        }
    )
    report = _report(metrics=metrics)

    assessment = assess_collapse_report_only(report)

    assert report.catastrophic_regression is True
    assert assessment.accepted is False
    assert "catastrophic regression" in assessment.reasons[0]


def test_clean_collapse_report_still_cannot_authorize_promotion_by_itself() -> None:
    assessment = assess_collapse_report_only(_report())

    assert assessment.accepted is False
    assert assessment.authoritative is False
    assert "non-authoritative" in assessment.reasons[0]


@pytest.mark.parametrize(
    "code",
    tuple(CollapseFindingCode),
)
def test_every_prohibited_pattern_remains_an_explicit_finding(
    code: CollapseFindingCode,
) -> None:
    finding = CollapseFinding(
        code=code,
        evidence_ids=(f"evidence-{code.value.lower()}",),
        detail="retained explicit collapse finding",
    )
    report = _report(findings=(finding,))

    assert report.findings == (finding,)
    assert assess_collapse_report_only(report).accepted is False


def test_collapse_metric_values_are_finite_normalized_and_strict() -> None:
    payload = _metrics().model_dump(mode="python")
    for invalid in (-0.01, 1.01, float("inf"), float("nan"), "0.5"):
        payload["calibration"] = invalid
        with pytest.raises(ValidationError):
            CollapseMetrics.model_validate(payload)


def test_catastrophic_flag_must_exactly_match_the_retained_dimension() -> None:
    with pytest.raises(ValidationError, match="catastrophic"):
        EvaluatorCollapseReport.model_validate(
            _report().model_dump(mode="python") | {"catastrophic_regression": True}
        )


def test_findings_and_evidence_are_unique_and_nonempty() -> None:
    finding = CollapseFinding(
        code=CollapseFindingCode.CONFIDENCE_AS_REWARD,
        evidence_ids=("evidence-1",),
        detail="confidence became a reward",
    )
    with pytest.raises(ValidationError, match="unique"):
        EvaluatorCollapseReport.model_validate(
            _report(findings=(finding,)).model_dump(mode="python")
            | {"findings": (finding, finding)}
        )
    with pytest.raises(ValidationError):
        CollapseFinding(
            code=CollapseFindingCode.CONFIDENCE_AS_REWARD,
            evidence_ids=(),
            detail="missing evidence",
        )


def _metrics() -> CollapseMetrics:
    return CollapseMetrics(
        protected_performance=0.8,
        external_performance=0.7,
        calibration=0.6,
        response_diversity=0.5,
        hypothesis_diversity=0.5,
        source_diversity=0.5,
        experiment_diversity=0.5,
        adapter_output_entropy=0.5,
        repeated_error_rate=0.1,
        confidence_error_coupling=0.1,
        evaluator_disagreement=0.2,
        catastrophic_regression=0.0,
        task_distribution_narrowing=0.1,
        externally_grounded_data_proportion=0.9,
    )


def _report(
    *,
    metrics: CollapseMetrics | None = None,
    findings: tuple[CollapseFinding, ...] = (),
) -> EvaluatorCollapseReport:
    selected = _metrics() if metrics is None else metrics
    return EvaluatorCollapseReport(
        evaluator_collapse_report_id="collapse-report-1",
        evaluator_version_id="evaluator-v2",
        metrics=selected,
        catastrophic_regression=selected.catastrophic_regression > 0,
        findings=findings,
        evidence_ids=(
            "protected-eval",
            "external-eval",
            *(evidence_id for finding in findings for evidence_id in finding.evidence_ids),
        ),
        measured_at=NOW,
        governing_policy_hash=HASH,
    )
