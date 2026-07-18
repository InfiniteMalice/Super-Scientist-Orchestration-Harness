from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.evaluators.models import (
    CollapseMetrics,
    EvaluationResult,
    EvaluatorCollapseRecord,
    EvaluatorSuccessionDecision,
    EvaluatorThreshold,
    EvaluatorVersion,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ExternalGrounding,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def test_durable_evaluator_promotion_requires_all_succession_gates() -> None:
    decision = _decision()

    assert decision.accepted is True
    assert decision.predecessor_rollback_target_id == decision.predecessor_evaluator_version_id
    assert decision.protected_evaluation.protected is True
    assert decision.external_evaluation.grounding is ExternalGrounding.EXTERNAL_BENCHMARK


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"evaluator_audit_result": AssessmentOutcome.FAILED}, "passed independent audit"),
        (
            {"protected_evaluation": None},
            "protected evaluation",
        ),
        (
            {"predecessor_rollback_target_id": "different-evaluator"},
            "predecessor rollback target",
        ),
    ],
)
def test_incomplete_evaluator_promotion_is_invalid(
    update: dict[str, object],
    message: str,
) -> None:
    payload = _decision().model_dump(mode="python")
    payload.update(update)

    with pytest.raises(ValidationError, match=message):
        EvaluatorSuccessionDecision.model_validate(payload)


def test_candidate_cannot_authorize_its_own_promotion() -> None:
    payload = _decision().model_dump(mode="python")
    payload["decision_authority"] = _model_actor("candidate-evaluator")

    with pytest.raises(ValidationError, match="candidate cannot authorize"):
        EvaluatorSuccessionDecision.model_validate(payload)


def test_collapse_record_retains_separate_metrics_without_authoritative_aggregate() -> None:
    record = EvaluatorCollapseRecord(
        evaluator_collapse_record_id="collapse-1",
        evaluator_version_id="evaluator-v2",
        metrics=CollapseMetrics(
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
        ),
        evidence_ids=("evaluation-1",),
        findings=("no aggregate authorizes promotion",),
        measured_at=NOW,
        governing_policy_hash=HASH,
    )

    assert "aggregate_score" not in record.model_dump()
    assert len(record.metrics.model_dump()) == 14


def _decision() -> EvaluatorSuccessionDecision:
    return EvaluatorSuccessionDecision(
        evaluator_succession_decision_id="succession-1",
        predecessor_evaluator_version_id="evaluator-v1",
        candidate_evaluator_version_id="evaluator-v2",
        candidate_evaluator=_model_actor("candidate-evaluator"),
        evaluator_audit_id="audit-1",
        evaluator_audit_result=AssessmentOutcome.PASSED,
        protected_evaluation=_evaluation("protected", protected=True),
        external_evaluation=_evaluation("external", protected=False),
        human_review=_provenance("human-review", human=True),
        canary_evaluation=_evaluation("canary", protected=False),
        predecessor_rollback_target_id="evaluator-v1",
        accepted=True,
        rationale=("all independent gates passed",),
        decision_authority=_human_actor("promotion-authority"),
        decided_at=NOW,
        governing_policy_hash=HASH,
    )


def _evaluation(identifier: str, *, protected: bool) -> EvaluationResult:
    return EvaluationResult(
        evaluation_id=identifier,
        provenance=_provenance(f"{identifier}-actor"),
        grounding=(
            ExternalGrounding.INDEPENDENT_TEST_SUITE
            if protected
            else ExternalGrounding.EXTERNAL_BENCHMARK
        ),
        protected=protected,
        passed=True,
    )


def _provenance(identifier: str, *, human: bool = False) -> AssessmentProvenance:
    actor = _human_actor(identifier) if human else _model_actor(identifier)
    return AssessmentProvenance(
        actor=actor,
        actor_version=f"{identifier}-v1",
        category=(
            VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
            if human
            else VerificationLevel.EXTERNAL_EMPIRICAL_MEASUREMENT
        ),
        deterministic_or_learned="HUMAN" if human else "DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("fixture assumptions",),
        evidence_ids=("evidence-1",),
        checks_run=("check-1",),
        limitations=("fixture coverage",),
        result=AssessmentOutcome.PASSED,
        assessed_at=NOW,
        governing_policy_hash=HASH,
    )


def _version(identifier: str, predecessor: str | None) -> EvaluatorVersion:
    return EvaluatorVersion(
        evaluator_version_id=identifier,
        evaluator=_model_actor(f"{identifier}-actor"),
        configuration_hash=HASH,
        threshold_history=(
            EvaluatorThreshold(
                threshold_id=f"{identifier}-threshold",
                metric_id="accuracy",
                value=0.8,
                effective_at=NOW,
            ),
        ),
        benchmark_version_ids=("benchmark-v1",),
        predecessor_evaluator_version_id=predecessor,
        rollback_evaluator_version_id=predecessor,
        candidate_producer=_model_actor("producer"),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def _human_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity(actor_id=identifier, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity.model(identifier, "provider", identifier, None, NOW)
