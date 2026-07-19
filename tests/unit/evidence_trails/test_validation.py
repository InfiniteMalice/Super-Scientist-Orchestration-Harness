from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter, ValidationError

from super_scientist.domain.evidence_trails.models import (
    AssessmentCategory,
    ClaimModality,
    EvidenceTrailNode,
    EvidenceTrailRelation,
    EvidenceTrailSnapshot,
    EvidenceTrailVersion,
    ExactSourceSpan,
    RelationType,
    ReportSentenceBinding,
    ReportSourceSpan,
    RetainedEvidenceSource,
    StructuralLocation,
    StructuralLocationKind,
    TrailAssessment,
    TrailCheckResult,
    TrailNodeRole,
    TrailOrderingConstraint,
    TrailOutcome,
    TrailValidationInputs,
    TrailValidationResult,
)
from super_scientist.domain.evidence_trails.validation import (
    validate_report_binding,
    validate_trail,
)
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import canonical_json_bytes
from super_scientist.kernel.transactions.models import (
    BindReportSentence,
    Proposal,
    RecordEvidenceTrailVersion,
)

if TYPE_CHECKING:
    from conftest import TrailFixture


def test_relation_and_outcome_vocabularies_are_closed() -> None:
    assert tuple(item.value for item in RelationType) == (
        "SUPPORTS",
        "CONTRADICTS",
        "PRECEDES",
        "FOLLOWS",
        "CAUSES_CANDIDATE",
        "ENABLES",
        "PREVENTS",
        "QUALIFIES",
        "EXPLAINS",
        "SAME_ENTITY",
        "SAME_EVENT",
        "DEPENDS_ON",
        "ALTERNATIVE_EXPLANATION",
    )
    assert tuple(item.value for item in TrailOutcome) == (
        "SUFFICIENT",
        "PARTIALLY_SUPPORTING",
        "CONFLICTED",
        "INSUFFICIENT",
        "UNANSWERABLE",
        "INVALID_TRAIL",
    )


def test_location_and_assessment_vocabularies_are_closed() -> None:
    assert tuple(item.value for item in StructuralLocationKind) == (
        "SECTION",
        "SUBSECTION",
        "PAGE",
        "PARAGRAPH",
        "TABLE",
        "FIGURE",
        "FOOTNOTE",
        "TIMESTAMP",
        "SPEAKER",
        "EVENT_SEQUENCE",
        "APPENDIX",
        "REFERENCE_TARGET",
    )
    assert tuple(item.value for item in AssessmentCategory) == (
        "NECESSITY",
        "GROUNDEDNESS",
        "RELATION_FIDELITY",
        "COUNTEREVIDENCE",
        "CAUSAL_OVERCLAIM_RISK",
        "RUBRIC_FIDELITY",
        "CONTAMINATION",
        "ANSWERABILITY",
    )


def test_all_public_records_are_strict_frozen_and_extra_forbidden() -> None:
    record_types = (
        StructuralLocation,
        TrailOrderingConstraint,
        EvidenceTrailVersion,
        EvidenceTrailNode,
        EvidenceTrailRelation,
        TrailCheckResult,
        TrailAssessment,
        ReportSentenceBinding,
        EvidenceTrailSnapshot,
        RetainedEvidenceSource,
        TrailValidationInputs,
        TrailValidationResult,
    )
    for record_type in record_types:
        assert record_type.model_config["frozen"] is True
        assert record_type.model_config["strict"] is True
        assert record_type.model_config["extra"] == "forbid"


def test_relation_rejects_unknown_fields_and_untyped_enum_values() -> None:
    relation_data = {
        "relation_id": "relation-1",
        "trail_version_id": "trail-1:1",
        "source_node_id": "node-1",
        "target_node_id": "node-2",
        "relation_type": RelationType.SUPPORTS,
        "evidence_ids": ("evidence-1",),
        "modality": ClaimModality.ASSERTED,
        "causal_support": (),
    }
    relation = EvidenceTrailRelation(**relation_data)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceTrailRelation(**relation_data, ignored=True)
    with pytest.raises(ValidationError):
        EvidenceTrailRelation(**{**relation_data, "relation_type": "SUPPORTS"})
    with pytest.raises(ValidationError):
        relation.relation_id = "changed"  # type: ignore[misc]


def test_node_confidence_must_be_a_finite_strict_float() -> None:
    node_data = {
        "node_id": "node-1",
        "trail_version_id": "trail-1:1",
        "source_id": "source-1",
        "evidence_id": "evidence-1",
        "exact_span": ExactSourceSpan(start=0, end=4, text="text"),
        "structural_location": StructuralLocation(
            kind=StructuralLocationKind.PARAGRAPH,
            locator="paragraph-1",
            start=0,
            end=4,
        ),
        "content_hash": "982d9e3eb996f559e633f4d194def3761d909f5a3b4dcb5e8a2e0cf1c72751e2",
        "role": TrailNodeRole.REQUIRED,
        "temporal_position": 0,
        "causal_position": 0,
        "confidence": 1.0,
        "necessity": True,
    }
    assert EvidenceTrailNode(**node_data).confidence == 1.0
    for invalid in (1, float("nan"), float("inf"), -0.1, 1.1):
        with pytest.raises(ValidationError):
            EvidenceTrailNode(**{**node_data, "confidence": invalid})


def test_valid_complete_source_first_graph_is_sufficient(trail_fixture: TrailFixture) -> None:
    result = validate_trail(trail_fixture.snapshot, trail_fixture.inputs)

    assert result == TrailValidationResult(
        trail_version_id="trail-version-1",
        outcome=TrailOutcome.SUFFICIENT,
        finding_codes=(),
        required_node_ids=("node-required",),
        opposing_node_ids=(),
        assessment_ids=trail_fixture.snapshot.version.assessment_ids,
    )


def test_modified_source_invalidates_exact_span(trail_fixture: TrailFixture) -> None:
    source = trail_fixture.inputs.sources[0]
    modified_inputs = trail_fixture.inputs.model_copy(
        update={
            "sources": (
                source.model_copy(update={"artifact_bytes": b"changed source"}),
            )
        }
    )

    result = validate_trail(trail_fixture.snapshot, modified_inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CONTENT_HASH_MISMATCH" in result.finding_codes


def test_exact_utf8_span_and_structural_bounds_are_recomputed(
    trail_fixture: TrailFixture,
) -> None:
    node = trail_fixture.snapshot.nodes[0]
    wrong_span = node.exact_span.model_copy(update={"text": "Wrong content!"})
    wrong_location = node.structural_location.model_copy(update={"start": node.exact_span.end})
    changed = node.model_copy(
        update={
            "exact_span": wrong_span,
            "structural_location": wrong_location,
        }
    )
    snapshot = trail_fixture.with_snapshot(nodes=(changed, *trail_fixture.snapshot.nodes[1:]))

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "EXACT_SPAN_MISMATCH" in result.finding_codes
    assert "STRUCTURAL_BOUNDS_INVALID" in result.finding_codes


def test_duplicate_and_unknown_graph_identifiers_fail_closed(
    trail_fixture: TrailFixture,
) -> None:
    required = trail_fixture.snapshot.nodes[0]
    relation = trail_fixture.snapshot.relations[0].model_copy(
        update={"target_node_id": "unknown-node"}
    )
    snapshot = trail_fixture.with_snapshot(
        nodes=(required, required),
        relations=(relation, *trail_fixture.snapshot.relations[1:]),
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "DUPLICATE_NODE_ID" in result.finding_codes
    assert "UNKNOWN_RELATION_ENDPOINT" in result.finding_codes


def test_order_constraints_and_temporal_relations_must_be_acyclic_and_consistent(
    trail_fixture: TrailFixture,
) -> None:
    version = trail_fixture.snapshot.version
    reverse = TrailOrderingConstraint(
        constraint_id="order-reverse",
        before_node_id="node-supporting",
        after_node_id="node-required",
    )
    wrong_temporal = trail_fixture.snapshot.relations[1].model_copy(
        update={
            "source_node_id": "node-supporting",
            "target_node_id": "node-required",
        }
    )
    snapshot = trail_fixture.with_snapshot(
        version=version.model_copy(
            update={"ordering_constraints": (*version.ordering_constraints, reverse)}
        ),
        relations=(trail_fixture.snapshot.relations[0], wrong_temporal),
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "ORDERING_CYCLE" in result.finding_codes
    assert "TEMPORAL_ORDER_INVALID" in result.finding_codes


def test_temporal_order_does_not_authorize_causality(trail_fixture: TrailFixture) -> None:
    causal_relation = trail_fixture.snapshot.relations[1].model_copy(
        update={"relation_type": RelationType.CAUSES_CANDIDATE, "causal_support": ()}
    )
    snapshot = trail_fixture.with_snapshot(
        relations=(trail_fixture.snapshot.relations[0], causal_relation)
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CAUSAL_OVERCLAIM" in result.finding_codes


def test_explicit_causal_support_and_independent_causal_assessment_are_required(
    trail_fixture: TrailFixture,
) -> None:
    causal_relation = trail_fixture.snapshot.relations[1].model_copy(
        update={
            "relation_type": RelationType.CAUSES_CANDIDATE,
            "causal_support": ("evidence-1",),
        }
    )
    snapshot = trail_fixture.with_snapshot(
        relations=(trail_fixture.snapshot.relations[0], causal_relation)
    )

    assert validate_trail(snapshot, trail_fixture.inputs).outcome is TrailOutcome.SUFFICIENT

    assessments = tuple(
        assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={"result": AssessmentOutcome.INCONCLUSIVE}
                )
            }
        )
        if assessment.category is AssessmentCategory.CAUSAL_OVERCLAIM_RISK
        else assessment
        for assessment in snapshot.assessments
    )
    invalid = snapshot.model_copy(update={"assessments": assessments})
    result = validate_trail(invalid, trail_fixture.inputs)
    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CAUSAL_OVERCLAIM" in result.finding_codes


@pytest.mark.parametrize(
    ("category", "assessment_result", "status", "expected"),
    [
        (
            AssessmentCategory.GROUNDEDNESS,
            AssessmentOutcome.INCONCLUSIVE,
            TrailOutcome.PARTIALLY_SUPPORTING,
            TrailOutcome.PARTIALLY_SUPPORTING,
        ),
        (
            AssessmentCategory.NECESSITY,
            AssessmentOutcome.FAILED,
            TrailOutcome.INSUFFICIENT,
            TrailOutcome.INSUFFICIENT,
        ),
        (
            AssessmentCategory.ANSWERABILITY,
            AssessmentOutcome.ABSTAINED,
            TrailOutcome.UNANSWERABLE,
            TrailOutcome.UNANSWERABLE,
        ),
    ],
)
def test_non_success_outcomes_are_never_collapsed(
    trail_fixture: TrailFixture,
    category: AssessmentCategory,
    assessment_result: AssessmentOutcome,
    status: TrailOutcome,
    expected: TrailOutcome,
) -> None:
    assessments = tuple(
        assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={"result": assessment_result}
                )
            }
        )
        if assessment.category is category
        else assessment
        for assessment in trail_fixture.snapshot.assessments
    )
    snapshot = trail_fixture.with_snapshot(
        version=trail_fixture.snapshot.version.model_copy(update={"status": status}),
        assessments=assessments,
    )

    assert validate_trail(snapshot, trail_fixture.inputs).outcome is expected


def test_opposing_evidence_produces_conflicted_without_discarding_nodes(
    trail_fixture: TrailFixture,
) -> None:
    required, supporting = trail_fixture.snapshot.nodes
    opposing = supporting.model_copy(update={"role": TrailNodeRole.OPPOSING, "necessity": True})
    contradiction = trail_fixture.snapshot.relations[0].model_copy(
        update={
            "relation_type": RelationType.CONTRADICTS,
            "source_node_id": opposing.node_id,
            "target_node_id": required.node_id,
        }
    )
    snapshot = trail_fixture.with_snapshot(
        version=trail_fixture.snapshot.version.model_copy(
            update={
                "supporting_node_ids": (),
                "opposing_node_ids": (opposing.node_id,),
                "status": TrailOutcome.CONFLICTED,
            }
        ),
        nodes=(required, opposing),
        relations=(contradiction, trail_fixture.snapshot.relations[1]),
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.CONFLICTED
    assert result.opposing_node_ids == (opposing.node_id,)


def test_declared_status_cannot_override_recomputed_outcome(trail_fixture: TrailFixture) -> None:
    snapshot = trail_fixture.with_snapshot(
        version=trail_fixture.snapshot.version.model_copy(
            update={"status": TrailOutcome.CONFLICTED}
        )
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "STATUS_MISMATCH" in result.finding_codes


def test_assessments_require_exact_categories_ids_and_independent_authority(
    trail_fixture: TrailFixture,
) -> None:
    assessment = trail_fixture.snapshot.assessments[0]
    forged = assessment.model_copy(
        update={
            "provenance": assessment.provenance.model_copy(
                update={
                    "actor": trail_fixture.snapshot.version.constructed_by,
                    "category": VerificationLevel.SELF_CRITIQUE,
                }
            )
        }
    )
    snapshot = trail_fixture.with_snapshot(
        assessments=(forged, *trail_fixture.snapshot.assessments[1:-1])
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "ASSESSMENT_ID_MISMATCH" in result.finding_codes
    assert "ASSESSMENT_CATEGORY_MISSING" in result.finding_codes
    assert "ASSESSMENT_NOT_INDEPENDENT" in result.finding_codes
    assert "ASSESSMENT_NOT_AUTHORITATIVE" in result.finding_codes


def test_closed_proposal_union_round_trips_complete_snapshot_and_report_binding(
    trail_fixture: TrailFixture,
) -> None:
    snapshot = trail_fixture.snapshot
    record = RecordEvidenceTrailVersion(
        proposal_id="proposal-trail-1",
        idempotency_key="intent-trail-1",
        proposer=snapshot.version.constructed_by,
        trail_version=snapshot.version,
        nodes=snapshot.nodes,
        relations=snapshot.relations,
        checks=snapshot.checks,
        assessments=snapshot.assessments,
    )
    node = snapshot.nodes[0]
    binding = ReportSentenceBinding(
        binding_id="binding-1",
        trail_version_id=snapshot.version.trail_version_id,
        claim_version_id=snapshot.version.claim_version_id,
        sentence="The cause preceded the effect.",
        outcome=TrailOutcome.SUFFICIENT,
        source_node_ids=(node.node_id,),
        source_spans=(
            ReportSourceSpan(
                node_id=node.node_id,
                source_id=node.source_id,
                evidence_id=node.evidence_id,
                start=node.exact_span.start,
                end=node.exact_span.end,
                text=node.exact_span.text,
                content_hash=node.content_hash,
            ),
        ),
        contradiction_node_ids=(),
        opposing_node_ids=(),
        uncertainty="The sentence is bounded to the exact retained source span.",
        modality=ClaimModality.ASSERTED,
        created_at=snapshot.version.created_at,
        governing_policy_hash=snapshot.version.governing_policy_hash,
    )
    bind = BindReportSentence(
        proposal_id="proposal-binding-1",
        idempotency_key="intent-binding-1",
        proposer=snapshot.version.constructed_by,
        binding=binding,
    )
    adapter: TypeAdapter[Proposal] = TypeAdapter(Proposal)

    for proposal in (record, bind):
        encoded = canonical_json_bytes(proposal.model_dump(mode="json"))
        assert adapter.validate_json(encoded) == proposal

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RecordEvidenceTrailVersion(
            **record.model_dump(mode="python"),
            caller_classification="OUTPUT",
        )


def test_report_binding_recomputes_exact_retained_nodes_and_spans(
    trail_fixture: TrailFixture,
) -> None:
    node = trail_fixture.snapshot.nodes[0]
    binding = _binding_for(trail_fixture, (node,))

    assert validate_report_binding(binding, trail_fixture.snapshot, trail_fixture.inputs) == ()

    forged_span = binding.source_spans[0].model_copy(update={"text": "Forged passage!"})
    forged = binding.model_copy(update={"source_spans": (forged_span,)})
    assert "REPORT_SPAN_MISMATCH" in validate_report_binding(
        forged,
        trail_fixture.snapshot,
        trail_fixture.inputs,
    )


def test_conflicted_report_binding_must_preserve_all_opposing_and_contradiction_nodes(
    trail_fixture: TrailFixture,
) -> None:
    required, supporting = trail_fixture.snapshot.nodes
    opposing = supporting.model_copy(update={"role": TrailNodeRole.OPPOSING, "necessity": True})
    contradiction = trail_fixture.snapshot.relations[0].model_copy(
        update={
            "relation_type": RelationType.CONTRADICTS,
            "source_node_id": opposing.node_id,
            "target_node_id": required.node_id,
        }
    )
    snapshot = trail_fixture.with_snapshot(
        version=trail_fixture.snapshot.version.model_copy(
            update={
                "supporting_node_ids": (),
                "opposing_node_ids": (opposing.node_id,),
                "status": TrailOutcome.CONFLICTED,
            }
        ),
        nodes=(required, opposing),
        relations=(contradiction, trail_fixture.snapshot.relations[1]),
    )
    binding = _binding_for(
        trail_fixture.__class__(snapshot=snapshot, inputs=trail_fixture.inputs),
        (required,),
        outcome=TrailOutcome.CONFLICTED,
    )

    findings = validate_report_binding(binding, snapshot, trail_fixture.inputs)

    assert "REPORT_OPPOSING_NODES_MISMATCH" in findings
    assert "REPORT_CONTRADICTIONS_MISMATCH" in findings


def _binding_for(
    fixture: TrailFixture,
    nodes: tuple[EvidenceTrailNode, ...],
    *,
    outcome: TrailOutcome = TrailOutcome.SUFFICIENT,
) -> ReportSentenceBinding:
    version = fixture.snapshot.version
    return ReportSentenceBinding(
        binding_id="binding-validation",
        trail_version_id=version.trail_version_id,
        claim_version_id=version.claim_version_id,
        sentence="The retained evidence supports this bounded sentence.",
        outcome=outcome,
        source_node_ids=tuple(node.node_id for node in nodes),
        source_spans=tuple(
            ReportSourceSpan(
                node_id=node.node_id,
                source_id=node.source_id,
                evidence_id=node.evidence_id,
                start=node.exact_span.start,
                end=node.exact_span.end,
                text=node.exact_span.text,
                content_hash=node.content_hash,
            )
            for node in nodes
        ),
        contradiction_node_ids=(),
        opposing_node_ids=(),
        uncertainty="The wording is limited to the retained source spans.",
        modality=ClaimModality.ASSERTED,
        created_at=version.created_at,
        governing_policy_hash=version.governing_policy_hash,
    )
