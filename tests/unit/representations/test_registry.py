from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import signature

import pytest
from pydantic import ValidationError

from super_scientist.application.representations import records as representation_records
from super_scientist.application.representations.records import (
    primitive_evaluation_from_storage,
    primitive_evaluation_to_storage,
    primitive_version_to_storage,
)
from super_scientist.application.representations.service import primitive_use_rejection
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.representations.models import (
    NewFrameEvaluation,
    OldFrameEvaluation,
    PrimitiveEvaluation,
    PrimitiveStatus,
    PrimitiveUse,
    PrimitiveVersion,
    SemanticVersionChange,
    TransformationKind,
    classify_concept_overlap,
    validate_semantic_version_change,
)
from super_scientist.kernel.transactions.models import RejectionCode
from super_scientist.providers.storage.domain_records import PrimitiveVersionRecord

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
POLICY_HASH = sha256_hex(b"task-11-policy")


def _actor(actor_id: str, *, model: bool = False) -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.MODEL if model else ActorKind.HUMAN,
        provider_id=f"provider-{actor_id}" if model else None,
        model_id=f"model-{actor_id}" if model else None,
        adapter_id=f"adapter-{actor_id}" if model else None,
        configuration_hash=sha256_hex(f"config-{actor_id}".encode()) if model else None,
        created_at=NOW,
    )


def _version(
    *,
    primitive_version_id: str = "primitive-temperature-gradient-v1",
    primitive_id: str = "primitive-temperature-gradient",
    semantic_version: str = "1.0.0",
    definition: str = "A directional change in a measured quantity over time.",
    status: PrimitiveStatus = PrimitiveStatus.EXPERIMENTAL,
    predecessors: tuple[str, ...] = (),
) -> PrimitiveVersion:
    return PrimitiveVersion(
        primitive_version_id=primitive_version_id,
        primitive_id=primitive_id,
        semantic_version=semantic_version,
        transformation_kind=TransformationKind.GENERATIVE_REPRESENTATION_PROPOSAL,
        definition=definition,
        motivation="Make a proposed explanatory distinction testable.",
        parent_vocabulary=("measured-quantity", "time"),
        contrasts=("constant quantity",),
        examples=("Temperature rises during a bounded heating interval.",),
        counterexamples=("A constant calibrated reading has no gradient.",),
        construction_method="Derived from retained measurements without executable input.",
        expected_uses=("Construct bounded hypotheses.",),
        predecessor_primitive_version_ids=predecessors,
        dependency_primitive_version_ids=(),
        measurement_ids=(),
        falsification_tests=("gradient-zero-check",),
        ambiguity=("Sampling resolution can hide short transitions.",),
        proposer=_actor("primitive-author", model=True),
        status=status,
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


def _provenance(frame: str) -> AssessmentProvenance:
    evaluator = _actor(f"{frame}-evaluator", model=True)
    return AssessmentProvenance(
        actor=evaluator,
        actor_version=f"{frame}-evaluator-v1",
        category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        deterministic_or_learned="DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("The retained fixtures cover the declared bounded scope.",),
        evidence_ids=(f"evidence-{frame}",),
        checks_run=(f"verification-{frame}",),
        limitations=("The check does not establish universal scientific truth.",),
        result=AssessmentOutcome.PASSED,
        meaningful_confidence=None,
        assessed_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


def _evaluation(*, old_frame: bool) -> PrimitiveEvaluation:
    frame = "old" if old_frame else "new"
    detail = (
        OldFrameEvaluation(
            preserved_constraints=("Calibration bounds remain unchanged.",),
            established_test_ids=("established-calibration-test",),
            regression_findings=("No retained baseline regression was observed.",),
        )
        if old_frame
        else NewFrameEvaluation(
            novel_predictions=("Heating predicts a positive bounded gradient.",),
            independent_operationalization="A separately authored finite-difference check.",
            non_circular_test_ids=("independent-gradient-test",),
            later_reuse_evidence_ids=("evidence-new",),
        )
    )
    return PrimitiveEvaluation(
        primitive_evaluation_id=f"evaluation-{frame}",
        primitive_version_id="primitive-temperature-gradient-v1",
        frame_evaluation=detail,
        verification_result_ids=(f"verification-{frame}",),
        evidence_ids=(f"evidence-{frame}",),
        check_actors=(_actor(f"{frame}-checker", model=True),),
        provenance=_provenance(frame),
        findings=(f"The {frame}-frame criterion passed.",),
        outcome=AssessmentOutcome.PASSED,
        evaluated_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


@pytest.mark.parametrize(
    "value",
    (
        "1.0",
        "01.0.0",
        "1.01.0",
        "1.0.01",
        "v1.0.0",
        "1.0.0-",
        "1.0.0+",
        "1.0.0-01",
    ),
)
def test_primitive_semantic_version_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(ValidationError):
        _version(semantic_version=value)


def test_incompatible_meaning_requires_major_version() -> None:
    prior = _version(semantic_version="1.2.0")
    candidate = _version(
        primitive_version_id="primitive-temperature-gradient-v2",
        semantic_version="1.3.0",
        definition="A causally sufficient directional change in a measured quantity.",
        predecessors=(prior.primitive_version_id,),
    )

    result = validate_semantic_version_change(
        prior.semantic_version,
        candidate.semantic_version,
        change=SemanticVersionChange.MEANING_INCOMPATIBLE,
    )

    assert result.accepted is False
    assert result.code == "INCOMPATIBLE_MEANING_REQUIRES_MAJOR"


@pytest.mark.parametrize(
    ("current", "change", "accepted"),
    (
        ("1.2.1", SemanticVersionChange.CLARIFICATION, True),
        ("1.3.0", SemanticVersionChange.COMPATIBLE_EXPANSION, True),
        ("2.0.0", SemanticVersionChange.MEANING_INCOMPATIBLE, True),
        ("1.2.0", SemanticVersionChange.CLARIFICATION, False),
        ("1.2.0+rebuilt", SemanticVersionChange.CLARIFICATION, False),
    ),
)
def test_semantic_version_change_requires_strict_monotonicity(
    current: str,
    change: SemanticVersionChange,
    accepted: bool,
) -> None:
    assert validate_semantic_version_change("1.2.0", current, change=change).accepted is accepted


@pytest.mark.parametrize(
    ("current", "change", "code"),
    (
        (
            "1.3.0",
            SemanticVersionChange.CLARIFICATION,
            "CLARIFICATION_REQUIRES_PATCH",
        ),
        (
            "1.2.1",
            SemanticVersionChange.COMPATIBLE_EXPANSION,
            "COMPATIBLE_EXPANSION_REQUIRES_MINOR",
        ),
    ),
)
def test_semantic_change_kind_requires_its_declared_version_component(
    current: str,
    change: SemanticVersionChange,
    code: str,
) -> None:
    result = validate_semantic_version_change("1.2.0", current, change=change)

    assert result.accepted is False
    assert result.code == code


def test_exact_and_semantic_duplicates_are_classified_without_learned_authority() -> None:
    original = _version()
    exact = _version(
        primitive_version_id="primitive-gradient-copy-v1",
        primitive_id="primitive-gradient-copy",
    )
    semantic = _version(
        primitive_version_id="primitive-gradient-reordered-v1",
        primitive_id="primitive-gradient-reordered",
        definition="Over time, a measured quantity has a directional change.",
    )

    assert classify_concept_overlap(exact, (original,)).value == "EXACT_DUPLICATE"
    assert classify_concept_overlap(semantic, (original,)).value == "SEMANTIC_DUPLICATE"


def test_primitive_version_storage_contract_retains_full_domain_identity() -> None:
    primitive = _version()

    stored = primitive_version_to_storage(primitive)
    payload = stored.model_dump(mode="json")

    assert "transformation_kind" in payload
    assert payload["transformation_kind"] == primitive.transformation_kind.value
    assert "proposer" in payload
    assert payload["proposer"] == primitive.proposer.model_dump(mode="json")
    inverse = getattr(representation_records, "primitive_version_from_storage", None)
    assert callable(inverse)
    assert inverse(stored) == primitive


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("actor_id", "alternate-primitive-author"),
        ("kind", ActorKind.TOOL),
        ("created_at", NOW + timedelta(seconds=1)),
        ("provider_id", "alternate-provider"),
        ("model_id", "alternate-model"),
        ("adapter_id", "alternate-adapter"),
        ("configuration_hash", sha256_hex(b"alternate-configuration")),
    ),
)
def test_primitive_storage_hash_distinguishes_every_proposer_identity_dimension(
    field: str,
    value: object,
) -> None:
    primitive = _version()
    changed = primitive.model_copy(
        update={"proposer": primitive.proposer.model_copy(update={field: value})}
    )

    original_record = primitive_version_to_storage(primitive)
    changed_record = primitive_version_to_storage(changed)

    assert changed_record != original_record
    assert sha256_hex(canonical_json_bytes(changed_record.model_dump(mode="json"))) != sha256_hex(
        canonical_json_bytes(original_record.model_dump(mode="json"))
    )


def test_primitive_storage_hash_distinguishes_transformation_kind() -> None:
    primitive = _version()
    changed = primitive.model_copy(
        update={"transformation_kind": TransformationKind.INTRA_SPACE_TRANSFORMATION}
    )

    original_record = primitive_version_to_storage(primitive)
    changed_record = primitive_version_to_storage(changed)

    assert changed_record != original_record
    assert sha256_hex(canonical_json_bytes(changed_record.model_dump(mode="json"))) != sha256_hex(
        canonical_json_bytes(original_record.model_dump(mode="json"))
    )


@pytest.mark.parametrize("tamper_nested_identity", (False, True))
def test_primitive_storage_contract_reconciles_redundant_proposer_id(
    tamper_nested_identity: bool,
) -> None:
    stored = primitive_version_to_storage(_version())
    payload = stored.model_dump(mode="python")
    if tamper_nested_identity:
        payload["proposer"] = stored.proposer.model_copy(update={"actor_id": "forged-author"})
    else:
        payload["proposer_id"] = "forged-author"

    with pytest.raises(ValidationError, match="proposer_id"):
        type(stored).model_validate(payload)


@pytest.mark.parametrize("old_frame", (True, False))
def test_typed_evaluations_round_trip_exactly_through_0005_storage(old_frame: bool) -> None:
    evaluation = _evaluation(old_frame=old_frame)

    stored = primitive_evaluation_to_storage(evaluation)

    assert primitive_evaluation_from_storage(stored) == evaluation


def test_storage_round_trip_rejects_typed_payload_column_disagreement() -> None:
    evaluation = _evaluation(old_frame=True)
    stored = primitive_evaluation_to_storage(evaluation).model_copy(
        update={"evaluator_id": "forged-evaluator"}
    )

    with pytest.raises(ValueError, match="typed primitive evaluation"):
        primitive_evaluation_from_storage(stored)


@dataclass(frozen=True)
class _PrimitiveResolver:
    record: PrimitiveVersionRecord | None
    head: tuple[str, str, PrimitiveStatus] | None

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        if self.record is None or self.record.primitive_version_id != version_id:
            return None
        return self.record

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        if self.record is None or self.record.primitive_id != primitive_id:
            return None
        return self.head


@pytest.mark.parametrize("use", tuple(PrimitiveUse))
def test_storage_resolved_quarantine_rejects_orphan_fabricated_and_tampered_state(
    use: PrimitiveUse,
) -> None:
    assert "resolver" in signature(primitive_use_rejection).parameters
    primitive = _version(status=PrimitiveStatus.STABILIZED)
    stored = primitive_version_to_storage(primitive)
    exact_head = (
        primitive.primitive_version_id,
        primitive.semantic_version,
        primitive.status,
    )
    orphan = _PrimitiveResolver(record=None, head=exact_head)
    fabricated_head = _PrimitiveResolver(
        record=stored,
        head=(primitive.primitive_version_id, "2.0.0", primitive.status),
    )
    tampered_record = _PrimitiveResolver(
        record=stored.model_copy(update={"proposer_id": "forged-author"}),
        head=exact_head,
    )

    for resolver in (orphan, fabricated_head, tampered_record):
        assert (
            primitive_use_rejection(
                primitive.primitive_version_id,
                resolver=resolver,
                use=use,
            )
            is RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED
        )


@pytest.mark.parametrize("use", tuple(PrimitiveUse))
def test_caller_supplied_head_tuple_is_never_primitive_use_authority(use: PrimitiveUse) -> None:
    primitive = _version(status=PrimitiveStatus.STABILIZED)

    with pytest.raises(TypeError):
        primitive_use_rejection(  # type: ignore[call-arg,arg-type]
            primitive,
            head=(primitive.primitive_version_id, primitive.semantic_version, primitive.status),
            use=use,
        )


@pytest.mark.parametrize("use", tuple(PrimitiveUse))
def test_storage_resolved_exact_promotable_head_can_leave_quarantine(use: PrimitiveUse) -> None:
    assert "resolver" in signature(primitive_use_rejection).parameters
    primitive = _version(status=PrimitiveStatus.STABILIZED)
    stored = primitive_version_to_storage(primitive)
    resolver = _PrimitiveResolver(
        record=stored,
        head=(primitive.primitive_version_id, primitive.semantic_version, primitive.status),
    )

    assert (
        primitive_use_rejection(
            primitive.primitive_version_id,
            resolver=resolver,
            use=use,
        )
        is None
    )


def test_primitive_contract_is_strict_and_frozen() -> None:
    primitive = _version()
    with pytest.raises(ValidationError):
        PrimitiveVersion.model_validate({**primitive.model_dump(), "runtime_import": "unsafe.mod"})
    with pytest.raises(ValidationError):
        primitive.status = PrimitiveStatus.STABILIZED  # type: ignore[misc]
