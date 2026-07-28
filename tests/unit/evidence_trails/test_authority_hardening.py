from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from super_scientist.domain import evidence_trails as trail_models
from super_scientist.domain.evidence_trails import authority as trail_authority
from super_scientist.domain.evidence_trails.models import (
    AddEvidenceReceiptRef,
    AssessmentCategory,
    ClaimModality,
    EvidenceTrailNodeStageReceiptRef,
    EvidenceTrailRelationStageReceiptRef,
    EvidenceTrailSnapshot,
    ExactSourceSpan,
    ProposeClaimReceiptRef,
    RelationType,
    ReportSentenceBinding,
    ReportSourceSpan,
    SourceFirstProvenance,
    TrailAssessment,
    TrailCheckResult,
    TrailGeometry,
    TrailNodeRole,
    TrailOutcome,
)
from super_scientist.domain.evidence_trails.validation import validate_trail
from super_scientist.domain.improvement.models import AssessmentOutcome
from tests.unit.evidence_trails.conftest import TrailFixture, with_fresh_source_first


def _receipt_fields(proposal_id: str) -> dict[str, str]:
    return {
        "proposal_id": proposal_id,
        "proposal_hash": "a" * 64,
        "audit_event_id": f"audit-{proposal_id}",
        "audit_event_hash": "b" * 64,
    }


def test_source_first_provenance_exposes_only_durable_receipt_references() -> None:
    assert tuple(SourceFirstProvenance.model_fields) == (
        "schema_version",
        "source_receipts",
        "node_stage_receipt",
        "relation_stage_receipt",
        "claim_stage_receipt",
    )


def test_source_first_provenance_builder_accepts_no_caller_chronology() -> None:
    source = AddEvidenceReceiptRef(**_receipt_fields("proposal-source"))
    node = EvidenceTrailNodeStageReceiptRef(**_receipt_fields("proposal-nodes"))
    relation = EvidenceTrailRelationStageReceiptRef(**_receipt_fields("proposal-relations"))
    claim = ProposeClaimReceiptRef(**_receipt_fields("proposal-claim"))

    provenance = trail_authority.build_source_first_provenance(
        source_receipts=(source,),
        node_stage_receipt=node,
        relation_stage_receipt=relation,
        claim_stage_receipt=claim,
    )

    assert provenance == SourceFirstProvenance(
        source_receipts=(source,),
        node_stage_receipt=node,
        relation_stage_receipt=relation,
        claim_stage_receipt=claim,
    )


def _replace_assessment(
    fixture: TrailFixture,
    category: AssessmentCategory,
    transform: Callable[[TrailAssessment], TrailAssessment],
    *,
    status: TrailOutcome | None = None,
) -> EvidenceTrailSnapshot:
    snapshot = fixture.snapshot
    assessments = tuple(
        transform(assessment) if assessment.category is category else assessment
        for assessment in snapshot.assessments
    )
    version = snapshot.version
    if status is not None:
        version = version.model_copy(update={"status": status})
    return snapshot.model_copy(update={"version": version, "assessments": assessments})


@pytest.mark.parametrize("category", tuple(AssessmentCategory))
@pytest.mark.parametrize(
    ("assessment_outcome", "expected_default"),
    [
        (AssessmentOutcome.PASSED, TrailOutcome.SUFFICIENT),
        (AssessmentOutcome.FAILED, TrailOutcome.INSUFFICIENT),
        (AssessmentOutcome.INCONCLUSIVE, TrailOutcome.PARTIALLY_SUPPORTING),
        (AssessmentOutcome.ABSTAINED, TrailOutcome.UNANSWERABLE),
    ],
)
def test_every_required_assessment_outcome_has_explicit_non_success_semantics(
    trail_fixture: TrailFixture,
    category: AssessmentCategory,
    assessment_outcome: AssessmentOutcome,
    expected_default: TrailOutcome,
) -> None:
    expected = (
        TrailOutcome.UNANSWERABLE
        if category is AssessmentCategory.ANSWERABILITY
        and assessment_outcome is AssessmentOutcome.FAILED
        else expected_default
    )
    snapshot = _replace_assessment(
        trail_fixture,
        category,
        lambda assessment: assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={"result": assessment_outcome}
                )
            }
        ),
        status=expected,
    )

    assert validate_trail(snapshot, trail_fixture.inputs).outcome is expected


@pytest.mark.parametrize(
    "scope_update",
    [
        {"node_ids": (), "relation_ids": (), "evidence_ids": ()},
        {"node_ids": ("node-required",), "relation_ids": ("relation-support",)},
        {
            "node_ids": ("node-supporting", "node-required"),
            "relation_ids": ("relation-order", "relation-support"),
        },
        {
            "node_ids": ("node-required", "node-supporting", "node-required"),
            "relation_ids": (
                "relation-support",
                "relation-order",
                "relation-support",
            ),
            "evidence_ids": ("evidence-1", "evidence-1"),
        },
    ],
)
def test_assessment_scopes_must_equal_category_canonical_tuples(
    trail_fixture: TrailFixture,
    scope_update: dict[str, tuple[str, ...]],
) -> None:
    def mutate(assessment: TrailAssessment) -> TrailAssessment:
        evidence_ids = scope_update.get("evidence_ids", assessment.evidence_ids)
        return assessment.model_copy(
            update={
                **scope_update,
                "provenance": assessment.provenance.model_copy(
                    update={"evidence_ids": evidence_ids}
                ),
            }
        )

    snapshot = _replace_assessment(
        trail_fixture,
        AssessmentCategory.GROUNDEDNESS,
        mutate,
    )

    assert validate_trail(snapshot, trail_fixture.inputs).outcome is TrailOutcome.INVALID_TRAIL


def test_assessment_category_and_id_order_is_canonical(trail_fixture: TrailFixture) -> None:
    snapshot = trail_fixture.snapshot.model_copy(
        update={"assessments": tuple(reversed(trail_fixture.snapshot.assessments))}
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "ASSESSMENT_ORDER_MISMATCH" in result.finding_codes


def test_assessment_ids_are_source_controlled(trail_fixture: TrailFixture) -> None:
    snapshot = trail_fixture.snapshot
    assessment = snapshot.assessments[0].model_copy(
        update={"assessment_id": "caller-selected-assessment"}
    )
    assessments = (assessment, *snapshot.assessments[1:])
    version = snapshot.version.model_copy(
        update={
            "assessment_ids": (
                assessment.assessment_id,
                *snapshot.version.assessment_ids[1:],
            )
        }
    )

    result = validate_trail(
        snapshot.model_copy(update={"version": version, "assessments": assessments}),
        trail_fixture.inputs,
    )

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "ASSESSMENT_ID_NOT_TRUSTED" in result.finding_codes


def _replace_check(
    snapshot: EvidenceTrailSnapshot,
    check: TrailCheckResult,
) -> EvidenceTrailSnapshot:
    return snapshot.model_copy(update={"checks": (check, *snapshot.checks[1:])})


@pytest.mark.parametrize(
    "check_update",
    [
        {"node_ids": ("node-supporting", "node-required")},
        {"node_ids": ("node-required", "node-supporting", "node-required")},
        {"relation_ids": ("relation-order", "relation-support")},
        {"relation_ids": ("relation-support", "relation-order", "relation-support")},
        {"evidence_ids": ("evidence-1", "evidence-1")},
        {"checker_id": "caller-selected-checker"},
        {"checker_version": "caller-selected-version"},
    ],
)
def test_check_scope_and_checker_identity_are_exact_and_trusted(
    trail_fixture: TrailFixture,
    check_update: dict[str, object],
) -> None:
    snapshot = trail_fixture.snapshot
    mutated = _replace_check(snapshot, snapshot.checks[0].model_copy(update=check_update))

    assert validate_trail(mutated, trail_fixture.inputs).outcome is TrailOutcome.INVALID_TRAIL


def test_check_category_and_id_order_is_canonical(trail_fixture: TrailFixture) -> None:
    snapshot = trail_fixture.snapshot.model_copy(
        update={"checks": tuple(reversed(trail_fixture.snapshot.checks))}
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CHECK_ORDER_MISMATCH" in result.finding_codes


def test_check_ids_are_source_controlled(trail_fixture: TrailFixture) -> None:
    snapshot = trail_fixture.snapshot
    check = snapshot.checks[0].model_copy(update={"check_id": "caller-selected-check"})
    checks = (check, *snapshot.checks[1:])
    version = snapshot.version.model_copy(
        update={"check_ids": (check.check_id, *snapshot.version.check_ids[1:])}
    )
    assessments = tuple(
        assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={"checks_run": version.check_ids}
                )
            }
        )
        for assessment in snapshot.assessments
    )

    result = validate_trail(
        snapshot.model_copy(
            update={"version": version, "checks": checks, "assessments": assessments}
        ),
        trail_fixture.inputs,
    )

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CHECK_ID_NOT_TRUSTED" in result.finding_codes


def _with_causal_relation(
    fixture: TrailFixture,
    relation_type: RelationType,
    causal_support: tuple[object, ...],
    *,
    co_occurring: bool = False,
) -> EvidenceTrailSnapshot:
    snapshot = fixture.snapshot
    required, supporting = snapshot.nodes
    required = required.model_copy(update={"causal_position": 0})
    supporting = supporting.model_copy(update={"causal_position": 1})
    if co_occurring:
        supporting = supporting.model_copy(
            update={
                "temporal_position": required.temporal_position,
            }
        )
    causal = snapshot.relations[1].model_copy(
        update={
            "relation_type": relation_type,
            "modality": ClaimModality.QUALIFIED,
            "causal_support": causal_support,
        }
    )
    updated = snapshot.model_copy(
        update={"nodes": (required, supporting), "relations": (snapshot.relations[0], causal)}
    )
    return with_fresh_source_first(fixture, updated)


@pytest.mark.parametrize(
    "relation_type",
    (
        RelationType.CAUSES_CANDIDATE,
        RelationType.ENABLES,
        RelationType.PREVENTS,
    ),
)
def test_evidence_id_only_never_authorizes_any_causal_relation(
    trail_fixture: TrailFixture,
    relation_type: RelationType,
) -> None:
    snapshot = _with_causal_relation(
        trail_fixture,
        relation_type,
        ("evidence-1",),
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CAUSAL_SUPPORT_MISMATCH" in result.finding_codes


@pytest.mark.parametrize(
    "relation_type",
    (
        RelationType.CAUSES_CANDIDATE,
        RelationType.ENABLES,
        RelationType.PREVENTS,
    ),
)
def test_temporal_co_occurrence_never_authorizes_any_causal_relation(
    trail_fixture: TrailFixture,
    relation_type: RelationType,
) -> None:
    snapshot = _with_causal_relation(
        trail_fixture,
        relation_type,
        ("evidence-1",),
        co_occurring=True,
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert "CAUSAL_OVERCLAIM" in result.finding_codes


def _exact_causal_support(fixture: TrailFixture) -> tuple[object, ...]:
    support_type = trail_models.CausalSupport
    relation = fixture.snapshot.relations[1]
    nodes = fixture.snapshot.nodes
    return tuple(
        support_type(
            support_id=f"{relation.relation_id}:causal:{node.node_id}",
            trail_version_id=relation.trail_version_id,
            relation_id=relation.relation_id,
            node_id=node.node_id,
            evidence_id=node.evidence_id,
            exact_span=node.exact_span,
            content_hash=node.content_hash,
        )
        for node in nodes
    )


def test_causal_support_is_bound_to_exact_endpoint_spans(
    trail_fixture: TrailFixture,
) -> None:
    supports = _exact_causal_support(trail_fixture)
    valid = _with_causal_relation(
        trail_fixture,
        RelationType.CAUSES_CANDIDATE,
        supports,
    )
    assert validate_trail(valid, trail_fixture.inputs).outcome is TrailOutcome.SUFFICIENT

    first = supports[0]
    forged = first.model_copy(
        update={"exact_span": ExactSourceSpan(start=16, end=31, text="Effect followed")}
    )
    invalid = _with_causal_relation(
        trail_fixture,
        RelationType.CAUSES_CANDIDATE,
        (forged, supports[1]),
    )
    result = validate_trail(invalid, trail_fixture.inputs)
    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CAUSAL_SUPPORT_MISMATCH" in result.finding_codes


def test_stale_causal_assessment_cannot_authorize_new_relation(
    trail_fixture: TrailFixture,
) -> None:
    snapshot = _with_causal_relation(
        trail_fixture,
        RelationType.CAUSES_CANDIDATE,
        _exact_causal_support(trail_fixture),
    )
    assessments = tuple(
        assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={"assessed_at": snapshot.version.created_at - timedelta(days=1)}
                )
            }
        )
        if assessment.category is AssessmentCategory.CAUSAL_OVERCLAIM_RISK
        else assessment
        for assessment in snapshot.assessments
    )

    result = validate_trail(
        snapshot.model_copy(update={"assessments": assessments}),
        trail_fixture.inputs,
    )

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "STALE_CAUSAL_ASSESSMENT" in result.finding_codes


@pytest.mark.parametrize(
    "grounding",
    (None, "INDEPENDENT_MODEL", "NONE", "synthetic", "UNKNOWN_GROUNDING"),
)
def test_every_retained_source_must_declare_exact_primary_source_grounding(
    trail_fixture: TrailFixture,
    grounding: str | None,
) -> None:
    source = trail_fixture.inputs.sources[0]
    provenance = {"fixture": "real artifact bytes"}
    if grounding is not None:
        provenance["external_grounding"] = grounding
    evidence = source.evidence.model_copy(update={"provenance": provenance})
    inputs = trail_fixture.inputs.model_copy(
        update={"sources": (source.model_copy(update={"evidence": evidence}),)}
    )

    result = validate_trail(trail_fixture.snapshot, inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "PRIMARY_SOURCE_GROUNDING_REQUIRED" in result.finding_codes


def test_mixed_primary_and_model_sources_fail_every_source_requirement(
    trail_fixture: TrailFixture,
) -> None:
    primary = trail_fixture.inputs.sources[0]
    model_evidence = primary.evidence.model_copy(
        update={
            "evidence_id": "evidence-model",
            "provenance": {
                "fixture": "model output",
                "external_grounding": "INDEPENDENT_MODEL",
            },
            "ingestion_actor_id": "model-ingestor",
        }
    )
    mixed = trail_fixture.inputs.model_copy(
        update={
            "sources": (
                primary,
                primary.model_copy(
                    update={"source_id": "source-model", "evidence": model_evidence}
                ),
            )
        }
    )

    result = validate_trail(trail_fixture.snapshot, mixed)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "PRIMARY_SOURCE_GROUNDING_REQUIRED" in result.finding_codes


def _source_first_provenance(trail_fixture: TrailFixture) -> object:
    return trail_fixture.snapshot.version.source_first_provenance


def _with_source_first(
    trail_fixture: TrailFixture,
    provenance: object | None,
) -> EvidenceTrailSnapshot:
    version = trail_fixture.snapshot.version.model_copy(
        update={"source_first_provenance": provenance}
    )
    return trail_fixture.snapshot.model_copy(update={"version": version})


def test_missing_source_first_process_provenance_fails_closed(
    trail_fixture: TrailFixture,
) -> None:
    result = validate_trail(_with_source_first(trail_fixture, None), trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "SOURCE_FIRST_PROVENANCE_REQUIRED" in result.finding_codes


def test_exact_source_first_process_provenance_is_accepted(
    trail_fixture: TrailFixture,
) -> None:
    result = validate_trail(
        _with_source_first(trail_fixture, _source_first_provenance(trail_fixture)),
        trail_fixture.inputs,
    )

    assert result.outcome is TrailOutcome.SUFFICIENT


def _two_source_fixture(fixture: TrailFixture) -> TrailFixture:
    first_source = fixture.inputs.sources[0]
    second_evidence = first_source.evidence.model_copy(
        update={
            "evidence_id": "evidence-2",
            "source_locator": "fixture://source-2",
            "ingestion_actor_id": "ingestor-2",
        }
    )
    second_source = first_source.model_copy(
        update={"source_id": "source-2", "evidence": second_evidence}
    )
    required, supporting = fixture.snapshot.nodes
    supporting = supporting.model_copy(
        update={"source_id": "source-2", "evidence_id": "evidence-2"}
    )
    nodes = (required, supporting)
    support, ordering = fixture.snapshot.relations
    relations = (
        support.model_copy(update={"evidence_ids": ("evidence-2", "evidence-1")}),
        ordering.model_copy(update={"evidence_ids": ("evidence-1", "evidence-2")}),
    )
    evidence_ids = ("evidence-1", "evidence-2")
    checks = tuple(
        check.model_copy(update={"evidence_ids": evidence_ids}) for check in fixture.snapshot.checks
    )
    assessments = tuple(
        assessment.model_copy(
            update={
                "node_ids": trail_authority.required_assessment_scope(
                    assessment.category,
                    nodes,
                    relations,
                ).node_ids,
                "relation_ids": trail_authority.required_assessment_scope(
                    assessment.category,
                    nodes,
                    relations,
                ).relation_ids,
                "evidence_ids": trail_authority.required_assessment_scope(
                    assessment.category,
                    nodes,
                    relations,
                ).evidence_ids,
                "provenance": assessment.provenance.model_copy(
                    update={
                        "evidence_ids": trail_authority.required_assessment_scope(
                            assessment.category,
                            nodes,
                            relations,
                        ).evidence_ids
                    }
                ),
            }
        )
        for assessment in fixture.snapshot.assessments
    )
    inputs = fixture.inputs.model_copy(update={"sources": (first_source, second_source)})
    prior = fixture.snapshot.version.source_first_provenance
    second_receipt = AddEvidenceReceiptRef(**_receipt_fields("proposal-source-2"))
    provenance = trail_authority.build_source_first_provenance(
        source_receipts=(*prior.source_receipts, second_receipt),
        node_stage_receipt=prior.node_stage_receipt,
        relation_stage_receipt=prior.relation_stage_receipt,
        claim_stage_receipt=prior.claim_stage_receipt,
    )
    version = fixture.snapshot.version.model_copy(
        update={
            "source_ids": ("source-1", "source-2"),
            "source_first_provenance": provenance,
        }
    )
    snapshot = fixture.snapshot.model_copy(
        update={
            "version": version,
            "nodes": nodes,
            "relations": relations,
            "checks": checks,
            "assessments": assessments,
        }
    )
    return TrailFixture(snapshot=snapshot, inputs=inputs)


def _identity_relation_fixture(
    fixture: TrailFixture,
    relation_type: RelationType,
    *,
    first_entity_id: object = "entity-shared",
    second_entity_id: object = "entity-shared",
    first_event_id: object = "event-shared",
    second_event_id: object = "event-shared",
) -> TrailFixture:
    fixture = _two_source_fixture(fixture)
    sources = []
    for source, entity_id, event_id in zip(
        fixture.inputs.sources,
        (first_entity_id, second_entity_id),
        (first_event_id, second_event_id),
        strict=True,
    ):
        provenance: dict[str, object] = dict(source.evidence.provenance)
        if entity_id is not None:
            provenance["entity_id"] = entity_id
        if event_id is not None:
            provenance["event_id"] = event_id
        sources.append(
            source.model_copy(
                update={"evidence": source.evidence.model_copy(update={"provenance": provenance})}
            )
        )
    nodes = fixture.snapshot.nodes
    if relation_type is RelationType.SAME_EVENT:
        nodes = (
            nodes[0],
            nodes[1].model_copy(update={"temporal_position": nodes[0].temporal_position}),
        )
    identity_relation = fixture.snapshot.relations[0].model_copy(
        update={"relation_type": relation_type}
    )
    neutral_relation = fixture.snapshot.relations[1].model_copy(
        update={"relation_type": RelationType.QUALIFIES}
    )
    relations = (identity_relation, neutral_relation)
    version = fixture.snapshot.version.model_copy(
        update={
            "ordering_constraints": (),
            "geometry": trail_authority.derive_geometry_from_graph(nodes, relations),
        }
    )
    snapshot = fixture.snapshot.model_copy(
        update={"version": version, "nodes": nodes, "relations": relations}
    )
    return TrailFixture(
        snapshot=snapshot,
        inputs=fixture.inputs.model_copy(update={"sources": tuple(sources)}),
    )


def test_same_entity_uses_typed_evidence_identity_not_content_hash(
    trail_fixture: TrailFixture,
) -> None:
    fixture = _identity_relation_fixture(trail_fixture, RelationType.SAME_ENTITY)
    assert fixture.snapshot.nodes[0].content_hash != fixture.snapshot.nodes[1].content_hash

    result = validate_trail(fixture.snapshot, fixture.inputs)

    assert result.outcome is TrailOutcome.SUFFICIENT


def test_same_event_requires_event_identity_in_addition_to_equal_time(
    trail_fixture: TrailFixture,
) -> None:
    fixture = _identity_relation_fixture(
        trail_fixture,
        RelationType.SAME_EVENT,
        second_event_id="event-different",
    )
    assert (
        fixture.snapshot.nodes[0].temporal_position == fixture.snapshot.nodes[1].temporal_position
    )

    result = validate_trail(fixture.snapshot, fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "SAME_EVENT_IDENTITY_UNPROVEN" in result.finding_codes


@pytest.mark.parametrize("malformed", (None, "", "   ", 7))
def test_identity_relation_rejects_missing_or_malformed_exact_key(
    trail_fixture: TrailFixture,
    malformed: object,
) -> None:
    fixture = _identity_relation_fixture(
        trail_fixture,
        RelationType.SAME_ENTITY,
        second_entity_id=malformed,
    )

    result = validate_trail(fixture.snapshot, fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "SAME_ENTITY_IDENTITY_UNPROVEN" in result.finding_codes


def test_assessor_cannot_alias_claim_author(trail_fixture: TrailFixture) -> None:
    snapshot = _replace_assessment(
        trail_fixture,
        AssessmentCategory.NECESSITY,
        lambda assessment: assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={
                        "actor": assessment.provenance.actor.model_copy(
                            update={"actor_id": trail_fixture.inputs.claim.created_by}
                        )
                    }
                )
            }
        ),
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "ASSESSMENT_NOT_INDEPENDENT" in result.finding_codes


def test_assessor_cannot_alias_second_ingestor(trail_fixture: TrailFixture) -> None:
    fixture = _two_source_fixture(trail_fixture)
    assert validate_trail(fixture.snapshot, fixture.inputs).outcome is TrailOutcome.SUFFICIENT
    snapshot = _replace_assessment(
        fixture,
        AssessmentCategory.NECESSITY,
        lambda assessment: assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={
                        "actor": assessment.provenance.actor.model_copy(
                            update={"actor_id": "ingestor-2"}
                        )
                    }
                )
            }
        ),
    )

    result = validate_trail(snapshot, fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "ASSESSMENT_NOT_INDEPENDENT" in result.finding_codes


def test_assessor_cannot_share_builder_model_or_configuration(
    trail_fixture: TrailFixture,
) -> None:
    stage_actor = trail_fixture.snapshot.version.constructed_by
    snapshot = _replace_assessment(
        trail_fixture,
        AssessmentCategory.NECESSITY,
        lambda assessment: assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={
                        "actor": stage_actor.model_copy(update={"actor_id": "stage-model-alias"})
                    }
                )
            }
        ),
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "ASSESSMENT_NOT_INDEPENDENT" in result.finding_codes


@pytest.mark.parametrize(
    "structure",
    (
        None,
        {"source_structure": {"schema_version": 2, "locations": ()}},
        {
            "source_structure": {
                "schema_version": 1,
                "locations": (
                    {
                        "kind": "SECTION",
                        "locator": "wrong-location",
                        "start": 0,
                        "end": 64,
                    },
                ),
            }
        },
    ),
)
def test_nodes_must_match_exact_retained_source_structure(
    trail_fixture: TrailFixture,
    structure: dict[str, object] | None,
) -> None:
    source = trail_fixture.inputs.sources[0]
    evidence = source.evidence.model_copy(update={"structured_observation": structure})
    inputs = trail_fixture.inputs.model_copy(
        update={"sources": (source.model_copy(update={"evidence": evidence}),)}
    )

    result = validate_trail(trail_fixture.snapshot, inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert (
        "SOURCE_STRUCTURE_INVALID" in result.finding_codes
        or "STRUCTURAL_LOCATION_MISMATCH" in result.finding_codes
    )


def test_declared_geometry_must_equal_topology_derived_geometry(
    trail_fixture: TrailFixture,
) -> None:
    assert trail_authority.derive_geometry(trail_fixture.snapshot) is TrailGeometry.LINEAR
    forged = trail_fixture.snapshot.model_copy(
        update={
            "version": trail_fixture.snapshot.version.model_copy(
                update={"geometry": TrailGeometry.NETWORK}
            )
        }
    )

    result = validate_trail(forged, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "GEOMETRY_MISMATCH" in result.finding_codes


def test_noncausal_nodes_cannot_carry_causal_positions(
    trail_fixture: TrailFixture,
) -> None:
    required, supporting = trail_fixture.snapshot.nodes
    forged = trail_fixture.snapshot.model_copy(
        update={
            "nodes": (
                required.model_copy(update={"causal_position": 0}),
                supporting,
            )
        }
    )
    result = validate_trail(forged, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CAUSAL_POSITION_MISMATCH" in result.finding_codes


def test_causal_positions_must_equal_deterministic_dag_layers(
    trail_fixture: TrailFixture,
) -> None:
    snapshot = _with_causal_relation(
        trail_fixture,
        RelationType.CAUSES_CANDIDATE,
        _exact_causal_support(trail_fixture),
    )
    required, supporting = snapshot.nodes
    forged = snapshot.model_copy(
        update={
            "nodes": (
                required.model_copy(update={"causal_position": 0}),
                supporting.model_copy(update={"causal_position": 2}),
            )
        }
    )

    result = validate_trail(forged, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CAUSAL_POSITION_MISMATCH" in result.finding_codes


@pytest.mark.parametrize("relation_type", tuple(RelationType))
def test_every_relation_type_has_one_complete_explicit_schema(
    relation_type: RelationType,
) -> None:
    schema = trail_authority.RELATION_SCHEMAS[relation_type]

    assert schema.relation_type is relation_type
    assert schema.allowed_role_pairs
    assert schema.allowed_modalities
    assert schema.temporal_rule
    assert schema.causal is (relation_type in trail_authority.CAUSAL_RELATION_TYPES)
    assert schema.identity_rule
    assert isinstance(schema.requires_opposing, bool)


def test_relation_evidence_tuple_is_exact_endpoint_order_not_a_subset(
    trail_fixture: TrailFixture,
) -> None:
    fixture = _two_source_fixture(trail_fixture)
    relation = fixture.snapshot.relations[0].model_copy(update={"evidence_ids": ("evidence-1",)})
    snapshot = fixture.snapshot.model_copy(
        update={"relations": (relation, fixture.snapshot.relations[1])}
    )
    snapshot = with_fresh_source_first(fixture, snapshot)

    result = validate_trail(snapshot, fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "RELATION_EVIDENCE_SCOPE_INVALID" in result.finding_codes


def test_asserted_modality_cannot_authorize_candidate_causality(
    trail_fixture: TrailFixture,
) -> None:
    snapshot = _with_causal_relation(
        trail_fixture,
        RelationType.CAUSES_CANDIDATE,
        _exact_causal_support(trail_fixture),
    )
    asserted = snapshot.relations[1].model_copy(update={"modality": ClaimModality.ASSERTED})
    snapshot = snapshot.model_copy(update={"relations": (snapshot.relations[0], asserted)})
    snapshot = with_fresh_source_first(trail_fixture, snapshot)
    assert snapshot.relations[1].modality is ClaimModality.ASSERTED

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "RELATION_MODALITY_INVALID" in result.finding_codes


def test_opposing_role_without_actual_contradiction_is_invalid(
    trail_fixture: TrailFixture,
) -> None:
    required, supporting = trail_fixture.snapshot.nodes
    opposing = supporting.model_copy(update={"role": TrailNodeRole.OPPOSING, "necessity": True})
    version = trail_fixture.snapshot.version.model_copy(
        update={
            "supporting_node_ids": (),
            "opposing_node_ids": (opposing.node_id,),
            "status": TrailOutcome.CONFLICTED,
        }
    )
    snapshot = trail_fixture.snapshot.model_copy(
        update={"version": version, "nodes": (required, opposing)}
    )

    result = validate_trail(snapshot, trail_fixture.inputs)

    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "OPPOSING_WITHOUT_CONTRADICTION" in result.finding_codes


def _report_binding(
    fixture: TrailFixture,
    nodes: tuple[object, ...],
    *,
    contradiction_node_ids: tuple[str, ...] = (),
    opposing_node_ids: tuple[str, ...] = (),
    modality: ClaimModality = ClaimModality.ASSERTED,
) -> ReportSentenceBinding:
    version = fixture.snapshot.version
    return ReportSentenceBinding(
        binding_id="binding-authority-hardening",
        trail_version_id=version.trail_version_id,
        claim_version_id=version.claim_version_id,
        sentence="The exact retained evidence bounds this sentence.",
        outcome=version.status,
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
        contradiction_node_ids=contradiction_node_ids,
        opposing_node_ids=opposing_node_ids,
        uncertainty="The wording retains the trail's bounded uncertainty.",
        modality=modality,
        created_at=version.created_at,
        governing_policy_hash=version.governing_policy_hash,
    )


def test_required_report_nodes_are_exact_canonical_nonredundant_nodes(
    trail_fixture: TrailFixture,
) -> None:
    assert (
        trail_authority.required_report_nodes(
            trail_fixture.snapshot,
            TrailOutcome.SUFFICIENT,
        )
        == trail_fixture.snapshot.nodes
    )


@pytest.mark.parametrize("mutation", ("partial", "reordered", "duplicate", "span_reordered"))
def test_report_binding_rejects_partial_duplicate_or_reordered_relevance(
    trail_fixture: TrailFixture,
    mutation: str,
) -> None:
    nodes = trail_fixture.snapshot.nodes
    binding = _report_binding(trail_fixture, nodes)
    if mutation == "partial":
        binding = _report_binding(trail_fixture, (nodes[0],))
    elif mutation == "reordered":
        binding = _report_binding(trail_fixture, tuple(reversed(nodes)))
    elif mutation == "duplicate":
        binding = binding.model_copy(
            update={"source_node_ids": (nodes[0].node_id, nodes[1].node_id, nodes[0].node_id)}
        )
    else:
        binding = binding.model_copy(update={"source_spans": tuple(reversed(binding.source_spans))})

    findings = trail_models.validate_report_binding(
        binding,
        trail_fixture.snapshot,
        trail_fixture.inputs,
    )

    assert "REPORT_NODE_SCOPE_MISMATCH" in findings or "REPORT_SPAN_MISMATCH" in findings


def _conflicted_fixture(fixture: TrailFixture) -> TrailFixture:
    required, supporting = fixture.snapshot.nodes
    opposing = supporting.model_copy(update={"role": TrailNodeRole.OPPOSING, "necessity": True})
    contradiction = fixture.snapshot.relations[0].model_copy(
        update={
            "relation_type": RelationType.CONTRADICTS,
            "source_node_id": opposing.node_id,
            "target_node_id": required.node_id,
        }
    )
    snapshot = fixture.snapshot.model_copy(
        update={
            "version": fixture.snapshot.version.model_copy(
                update={
                    "supporting_node_ids": (),
                    "opposing_node_ids": (opposing.node_id,),
                    "status": TrailOutcome.CONFLICTED,
                }
            ),
            "nodes": (required, opposing),
            "relations": (contradiction, fixture.snapshot.relations[1]),
        }
    )
    snapshot = with_fresh_source_first(fixture, snapshot)
    return TrailFixture(snapshot=snapshot, inputs=fixture.inputs)


def test_conflicted_binding_names_every_actual_contradiction_participant(
    trail_fixture: TrailFixture,
) -> None:
    fixture = _conflicted_fixture(trail_fixture)
    _required, opposing = fixture.snapshot.nodes
    binding = _report_binding(
        fixture,
        fixture.snapshot.nodes,
        contradiction_node_ids=(opposing.node_id,),
        opposing_node_ids=(opposing.node_id,),
        modality=ClaimModality.QUALIFIED,
    )

    findings = trail_models.validate_report_binding(
        binding,
        fixture.snapshot,
        fixture.inputs,
    )

    assert "REPORT_CONTRADICTIONS_MISMATCH" in findings


def test_conflicted_binding_cannot_use_asserted_modality(
    trail_fixture: TrailFixture,
) -> None:
    fixture = _conflicted_fixture(trail_fixture)
    _required, opposing = fixture.snapshot.nodes
    binding = _report_binding(
        fixture,
        fixture.snapshot.nodes,
        contradiction_node_ids=(opposing.node_id,),
        opposing_node_ids=(opposing.node_id,),
    )

    findings = trail_models.validate_report_binding(
        binding,
        fixture.snapshot,
        fixture.inputs,
    )

    assert "REPORT_CONFLICT_MODALITY_INVALID" in findings
