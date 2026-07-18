from __future__ import annotations

import pytest

from super_scientist.application.improvement.service import AdaptationAuthority
from super_scientist.kernel.transactions.models import RejectionCode


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("adapter_self_promotion", RejectionCode.PERMISSION_DENIED),
        ("rule_proposer_self_approval", RejectionCode.PERMISSION_DENIED),
        ("harness_optimizer_altering_evaluation", RejectionCode.PERMISSION_DENIED),
        ("evaluator_threshold_rewrite", RejectionCode.PROHIBITED_CLOSED_LOOP),
        ("automatic_evaluator_replacement", RejectionCode.PROHIBITED_CLOSED_LOOP),
        ("protected_holdout_access", RejectionCode.PROTECTED_DATA_ACCESS),
        ("failed_experiment_omission", RejectionCode.PERMISSION_DENIED),
        (
            "self_declared_independent_verification",
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
        ),
        ("closed_loop_governance", RejectionCode.PROHIBITED_CLOSED_LOOP),
        ("closed_loop_quality_gate", RejectionCode.PROHIBITED_CLOSED_LOOP),
        ("direct_rule_edit", RejectionCode.PERMISSION_DENIED),
        ("benchmark_specific_admission", RejectionCode.BENCHMARK_SPECIFIC_ADMISSION),
        ("false_finish", RejectionCode.FALSE_FINISH),
        ("summary_for_raw_evidence", RejectionCode.MISSING_EVIDENCE),
        ("confidence_as_evidence", RejectionCode.INSUFFICIENT_GROUNDING),
        ("likelihood_as_evidence", RejectionCode.INSUFFICIENT_GROUNDING),
        ("self_consistency_as_evidence", RejectionCode.INSUFFICIENT_GROUNDING),
        ("textual_agreement_as_evidence", RejectionCode.INSUFFICIENT_GROUNDING),
    ],
)
def test_every_prohibited_adaptive_operation_fails_closed(
    operation: str,
    expected: RejectionCode,
) -> None:
    decision = AdaptationAuthority().attempt(operation, proposal_id=f"attempt-{operation}")

    assert decision.accepted is False
    assert decision.reasons[0].code is expected


def test_unknown_adaptive_operation_has_no_ambient_authority() -> None:
    decision = AdaptationAuthority().attempt("future_unknown_operation", proposal_id="unknown-1")

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.PERMISSION_DENIED
    assert "not source-controlled" in decision.reasons[0].message
