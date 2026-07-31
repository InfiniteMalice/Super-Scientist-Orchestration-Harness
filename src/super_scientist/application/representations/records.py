from __future__ import annotations

from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import canonical_json_bytes
from super_scientist.domain.representations.models import (
    PrimitiveEvaluation,
    PrimitiveStatus,
    PrimitiveVersion,
)
from super_scientist.providers.storage.domain_records import (
    EvaluationOutcome,
    PrimitiveEvaluationFrame,
    PrimitiveEvaluationRecord,
    PrimitiveVersionRecord,
)
from super_scientist.providers.storage.domain_records import (
    PrimitiveStatus as StoredPrimitiveStatus,
)

_TYPED_EVALUATION_PREFIX = "typed-primitive-evaluation-v1:"


def primitive_version_to_storage(
    primitive: PrimitiveVersion,
    *,
    status: StoredPrimitiveStatus | None = None,
) -> PrimitiveVersionRecord:
    return PrimitiveVersionRecord(
        primitive_version_id=primitive.primitive_version_id,
        primitive_id=primitive.primitive_id,
        semantic_version=primitive.semantic_version,
        transformation_kind=primitive.transformation_kind,
        definition=primitive.definition,
        motivation=primitive.motivation,
        parent_vocabulary=primitive.parent_vocabulary,
        contrasts=primitive.contrasts,
        examples=primitive.examples,
        counterexamples=primitive.counterexamples,
        construction_method=primitive.construction_method,
        expected_uses=primitive.expected_uses,
        predecessor_primitive_version_ids=primitive.predecessor_primitive_version_ids,
        dependency_primitive_version_ids=primitive.dependency_primitive_version_ids,
        measurement_ids=primitive.measurement_ids,
        falsification_tests=primitive.falsification_tests,
        ambiguity=primitive.ambiguity,
        proposer=primitive.proposer,
        proposer_id=primitive.proposer.actor_id,
        status=status or StoredPrimitiveStatus(primitive.status.value),
        created_at=primitive.created_at,
        governing_policy_hash=primitive.governing_policy_hash,
    )


def primitive_version_from_storage(record: PrimitiveVersionRecord) -> PrimitiveVersion:
    primitive = PrimitiveVersion(
        primitive_version_id=record.primitive_version_id,
        primitive_id=record.primitive_id,
        semantic_version=record.semantic_version,
        transformation_kind=record.transformation_kind,
        definition=record.definition,
        motivation=record.motivation,
        parent_vocabulary=record.parent_vocabulary,
        contrasts=record.contrasts,
        examples=record.examples,
        counterexamples=record.counterexamples,
        construction_method=record.construction_method,
        expected_uses=record.expected_uses,
        predecessor_primitive_version_ids=record.predecessor_primitive_version_ids,
        dependency_primitive_version_ids=record.dependency_primitive_version_ids,
        measurement_ids=record.measurement_ids,
        falsification_tests=record.falsification_tests,
        ambiguity=record.ambiguity,
        proposer=record.proposer,
        status=PrimitiveStatus(record.status.value),
        created_at=record.created_at,
        governing_policy_hash=record.governing_policy_hash,
    )
    if primitive_version_to_storage(primitive) != record:
        raise ValueError("primitive version storage record does not round-trip exactly")
    return primitive


def primitive_evaluation_to_storage(
    evaluation: PrimitiveEvaluation,
) -> PrimitiveEvaluationRecord:
    payload = canonical_json_bytes(evaluation.model_dump(mode="json")).decode("utf-8")
    return PrimitiveEvaluationRecord(
        primitive_evaluation_id=evaluation.primitive_evaluation_id,
        primitive_version_id=evaluation.primitive_version_id,
        frame=PrimitiveEvaluationFrame(evaluation.frame_evaluation.frame),
        verification_result_ids=evaluation.verification_result_ids,
        evidence_ids=evaluation.evidence_ids,
        criteria=(f"{_TYPED_EVALUATION_PREFIX}{payload}",),
        findings=evaluation.findings,
        outcome={
            AssessmentOutcome.PASSED: EvaluationOutcome.PASS,
            AssessmentOutcome.FAILED: EvaluationOutcome.FAIL,
            AssessmentOutcome.INCONCLUSIVE: EvaluationOutcome.ABSTAIN,
            AssessmentOutcome.ABSTAINED: EvaluationOutcome.ABSTAIN,
        }[evaluation.outcome],
        evaluator_id=evaluation.provenance.actor.actor_id,
        evaluated_at=evaluation.evaluated_at,
        governing_policy_hash=evaluation.governing_policy_hash,
    )


def primitive_evaluation_from_storage(
    record: PrimitiveEvaluationRecord,
) -> PrimitiveEvaluation:
    if len(record.criteria) != 1 or not record.criteria[0].startswith(_TYPED_EVALUATION_PREFIX):
        raise ValueError("storage record does not contain one typed primitive evaluation")
    payload = record.criteria[0][len(_TYPED_EVALUATION_PREFIX) :]
    evaluation = PrimitiveEvaluation.model_validate_json(payload)
    expected = primitive_evaluation_to_storage(evaluation)
    if expected != record:
        raise ValueError("typed primitive evaluation disagrees with storage columns")
    return evaluation
