from __future__ import annotations

import inspect

import pytest

from super_scientist.domain.evidence_trails.models import (
    AssessmentCategory,
    EvidenceTrailRelation,
    EvidenceTrailVersion,
    RelationType,
    StructuralLocation,
    StructuralLocationKind,
    TrailAssessment,
    TrailOutcome,
)
from super_scientist.providers.storage import domain_records
from tests.unit.evidence_trails.conftest import make_trail_fixture


@pytest.mark.property
def test_public_storage_surface_has_exact_fixed_trail_record_wrappers() -> None:
    names = {
        "EvidenceTrailVersionRepository",
        "EvidenceTrailNodeRepository",
        "EvidenceTrailRelationRepository",
        "EvidenceTrailCheckRepository",
        "EvidenceTrailAssessmentRepository",
        "ReportSentenceBindingRepository",
    }
    assert names <= set(domain_records.__all__)
    for name in names:
        repository_type = getattr(domain_records, name)
        assert tuple(inspect.signature(repository_type).parameters) == ("connection",)


@pytest.mark.property
@pytest.mark.parametrize("relation_type", tuple(RelationType))
def test_every_closed_relation_type_round_trips_strictly(
    relation_type: RelationType,
) -> None:
    base = make_trail_fixture().snapshot.relations[0]
    relation = EvidenceTrailRelation.model_validate(
        {**base.model_dump(mode="python"), "relation_type": relation_type}
    )

    assert relation.relation_type is relation_type


@pytest.mark.property
@pytest.mark.parametrize("outcome", tuple(TrailOutcome))
def test_every_closed_trail_outcome_round_trips_strictly(outcome: TrailOutcome) -> None:
    base = make_trail_fixture().snapshot.version
    version = EvidenceTrailVersion.model_validate(
        {**base.model_dump(mode="python"), "status": outcome}
    )

    assert version.status is outcome


@pytest.mark.property
@pytest.mark.parametrize("kind", tuple(StructuralLocationKind))
def test_every_closed_structural_location_kind_round_trips_strictly(
    kind: StructuralLocationKind,
) -> None:
    location = StructuralLocation(kind=kind, locator="retained-span", start=0, end=1)

    assert location.kind is kind


@pytest.mark.property
@pytest.mark.parametrize("category", tuple(AssessmentCategory))
def test_every_closed_assessment_category_round_trips_strictly(
    category: AssessmentCategory,
) -> None:
    base = make_trail_fixture().snapshot.assessments[0]
    assessment = TrailAssessment.model_validate(
        {**base.model_dump(mode="python"), "category": category}
    )

    assert assessment.category is category
