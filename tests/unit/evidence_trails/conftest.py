from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, VerificationState
from super_scientist.domain.evidence_trails.authority import (
    TRUSTED_TRAIL_CHECKER_ID,
    TRUSTED_TRAIL_CHECKER_VERSION,
    build_source_first_provenance,
    required_assessment_scope,
    trusted_assessment_id,
    trusted_check_id,
)
from super_scientist.domain.evidence_trails.models import (
    AddEvidenceReceiptRef,
    AssessmentCategory,
    ClaimModality,
    ConstructionMethod,
    EvidenceTrailNode,
    EvidenceTrailNodeStageReceiptRef,
    EvidenceTrailRelation,
    EvidenceTrailRelationStageReceiptRef,
    EvidenceTrailSnapshot,
    EvidenceTrailVersion,
    ExactSourceSpan,
    ProposeClaimReceiptRef,
    RelationType,
    RetainedEvidenceSource,
    StructuralLocation,
    StructuralLocationKind,
    TrailAssessment,
    TrailCheckCategory,
    TrailCheckResult,
    TrailGeometry,
    TrailNodeRole,
    TrailOrderingConstraint,
    TrailOutcome,
    TrailValidationInputs,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
)
from super_scientist.domain.primitives import sha256_hex

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
RETRIEVED_AT = NOW - timedelta(minutes=10)
CLAIMED_AT = NOW - timedelta(minutes=4)
NODE_PROPOSED_AT = NOW - timedelta(minutes=8)
RELATION_PROPOSED_AT = NOW - timedelta(minutes=6)
CHECKED_AT = NOW - timedelta(minutes=2)
ASSESSED_AT = NOW - timedelta(minutes=1)
POLICY_HASH = "a" * 64
SOURCE_TEXT = "Cause happened. Effect followed. Alternative explanation remains."


def _actor(actor_id: str, *, model_id: str = "builder") -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.MODEL,
        provider_id=f"provider-{actor_id}",
        model_id=model_id,
        configuration_hash=sha256_hex(actor_id.encode("utf-8")),
        created_at=NOW,
    )


def _node(
    node_id: str,
    text: str,
    role: TrailNodeRole,
    temporal_position: int,
    *,
    necessity: bool,
) -> EvidenceTrailNode:
    start = SOURCE_TEXT.index(text)
    end = start + len(text)
    return EvidenceTrailNode(
        node_id=node_id,
        trail_version_id="trail-version-1",
        source_id="source-1",
        evidence_id="evidence-1",
        exact_span=ExactSourceSpan(start=start, end=end, text=text),
        structural_location=StructuralLocation(
            kind=StructuralLocationKind.PARAGRAPH,
            locator="paragraph-1",
            start=0,
            end=len(SOURCE_TEXT),
        ),
        content_hash=sha256_hex(text.encode("utf-8")),
        role=role,
        temporal_position=temporal_position,
        causal_position=None,
        confidence=0.9,
        necessity=necessity,
    )


@dataclass(frozen=True)
class TrailFixture:
    snapshot: EvidenceTrailSnapshot
    inputs: TrailValidationInputs

    def with_snapshot(self, **updates: object) -> EvidenceTrailSnapshot:
        return self.snapshot.model_copy(update=updates)


def with_fresh_source_first(
    fixture: TrailFixture,
    snapshot: EvidenceTrailSnapshot,
) -> EvidenceTrailSnapshot:
    prior = fixture.snapshot.version.source_first_provenance
    version_id = snapshot.version.trail_version_id
    provenance = build_source_first_provenance(
        source_receipts=prior.source_receipts,
        node_stage_receipt=EvidenceTrailNodeStageReceiptRef(
            **_receipt_fields(f"node-stage-{version_id}")
        ),
        relation_stage_receipt=EvidenceTrailRelationStageReceiptRef(
            **_receipt_fields(f"relation-stage-{version_id}")
        ),
        claim_stage_receipt=prior.claim_stage_receipt,
    )
    return snapshot.model_copy(
        update={
            "version": snapshot.version.model_copy(update={"source_first_provenance": provenance})
        }
    )


@pytest.fixture
def trail_fixture() -> TrailFixture:
    return make_trail_fixture()


def make_trail_fixture() -> TrailFixture:
    source_bytes = SOURCE_TEXT.encode("utf-8")
    evidence = EvidenceRecord(
        evidence_id="evidence-1",
        evidence_type="primary-source",
        source_locator="fixture://source-1",
        retrieved_at=RETRIEVED_AT,
        artifact=ArtifactRef(
            sha256=sha256_hex(source_bytes),
            size_bytes=len(source_bytes),
            media_type="text/plain",
            relative_path=f"sha256/{sha256_hex(source_bytes)[:2]}/{sha256_hex(source_bytes)}",
        ),
        structured_observation={
            "source_structure": {
                "schema_version": 1,
                "locations": (
                    {
                        "kind": "PARAGRAPH",
                        "locator": "paragraph-1",
                        "start": 0,
                        "end": len(SOURCE_TEXT),
                    },
                ),
            }
        },
        provenance={
            "fixture": "real artifact bytes",
            "external_grounding": "PRIMARY_SOURCE",
        },
        ingestion_actor_id="ingestor-1",
        verification_state=VerificationState.HASH_VERIFIED,
    )
    claim = AtomicClaim(
        claim_id="claim-1",
        version=1,
        proposition="The cause preceded the effect.",
        scope="fixture source",
        population_or_system="fixture system",
        epistemic_modality="ASSERTED",
        status=ClaimStatus.PROPOSED,
        created_at=CLAIMED_AT,
        created_by="claim-author",
    )
    required = _node("node-required", "Cause happened", TrailNodeRole.REQUIRED, 0, necessity=True)
    supporting = _node(
        "node-supporting",
        "Effect followed",
        TrailNodeRole.SUPPORTING,
        1,
        necessity=False,
    )
    relations = (
        EvidenceTrailRelation(
            relation_id="relation-support",
            trail_version_id="trail-version-1",
            source_node_id=supporting.node_id,
            target_node_id=required.node_id,
            relation_type=RelationType.SUPPORTS,
            evidence_ids=("evidence-1",),
            modality=ClaimModality.ASSERTED,
        ),
        EvidenceTrailRelation(
            relation_id="relation-order",
            trail_version_id="trail-version-1",
            source_node_id=required.node_id,
            target_node_id=supporting.node_id,
            relation_type=RelationType.PRECEDES,
            evidence_ids=("evidence-1",),
            modality=ClaimModality.ASSERTED,
        ),
    )
    node_ids = (required.node_id, supporting.node_id)
    relation_ids = tuple(relation.relation_id for relation in relations)
    check_ids = tuple(
        trusted_check_id("trail-version-1", category) for category in TrailCheckCategory
    )
    checks = tuple(
        TrailCheckResult(
            check_id=check_id,
            trail_version_id="trail-version-1",
            claim_version_id="claim-1:1",
            governing_policy_hash=POLICY_HASH,
            category=category,
            passed=True,
            finding_codes=(),
            node_ids=node_ids,
            relation_ids=relation_ids,
            evidence_ids=("evidence-1",),
            checker_id=TRUSTED_TRAIL_CHECKER_ID,
            checker_version=TRUSTED_TRAIL_CHECKER_VERSION,
            checked_at=CHECKED_AT,
        )
        for category, check_id in zip(TrailCheckCategory, check_ids, strict=True)
    )
    assessments = tuple(
        TrailAssessment(
            assessment_id=trusted_assessment_id("trail-version-1", category),
            trail_version_id="trail-version-1",
            claim_version_id="claim-1:1",
            governing_policy_hash=POLICY_HASH,
            category=category,
            provenance=AssessmentProvenance(
                actor=_actor(f"assessor-{index}", model_id=f"judge-{index}"),
                actor_version="1",
                category=VerificationLevel.INDEPENDENT_LEARNED_JUDGE,
                deterministic_or_learned="LEARNED",
                proposer_relationship=ActorRelationship.INDEPENDENT,
                assumptions=(),
                evidence_ids=required_assessment_scope(
                    category,
                    (required, supporting),
                    relations,
                ).evidence_ids,
                checks_run=check_ids,
                limitations=("Fixture assessment is bounded to the retained source.",),
                result=AssessmentOutcome.PASSED,
                meaningful_confidence=0.9,
                assessed_at=ASSESSED_AT,
                governing_policy_hash=POLICY_HASH,
            ),
            node_ids=required_assessment_scope(
                category,
                (required, supporting),
                relations,
            ).node_ids,
            relation_ids=required_assessment_scope(
                category,
                (required, supporting),
                relations,
            ).relation_ids,
            evidence_ids=required_assessment_scope(
                category,
                (required, supporting),
                relations,
            ).evidence_ids,
            finding_codes=(),
        )
        for index, category in enumerate(AssessmentCategory)
    )
    builder = _actor("trail-builder")
    retained_source = RetainedEvidenceSource(
        source_id="source-1",
        evidence=evidence,
        artifact_bytes=source_bytes,
    )
    source_first_provenance = build_source_first_provenance(
        source_receipts=(AddEvidenceReceiptRef(**_receipt_fields("proposal-source")),),
        node_stage_receipt=EvidenceTrailNodeStageReceiptRef(
            **_receipt_fields("proposal-node-stage-1")
        ),
        relation_stage_receipt=EvidenceTrailRelationStageReceiptRef(
            **_receipt_fields("proposal-relation-stage-1")
        ),
        claim_stage_receipt=ProposeClaimReceiptRef(**_receipt_fields("proposal-claim")),
    )
    version = EvidenceTrailVersion(
        trail_version_id="trail-version-1",
        trail_id="trail-1",
        claim_version_id="claim-1:1",
        version=1,
        source_ids=("source-1",),
        required_node_ids=(required.node_id,),
        supporting_node_ids=(supporting.node_id,),
        opposing_node_ids=(),
        redundant_node_ids=(),
        ordering_constraints=(
            TrailOrderingConstraint(
                constraint_id="order-1",
                before_node_id=required.node_id,
                after_node_id=supporting.node_id,
            ),
        ),
        geometry=TrailGeometry.LINEAR,
        status=TrailOutcome.SUFFICIENT,
        construction_method=ConstructionMethod.SOURCE_FIRST,
        source_first_provenance=source_first_provenance,
        check_ids=check_ids,
        assessment_ids=tuple(item.assessment_id for item in assessments),
        constructed_by=builder,
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    snapshot = EvidenceTrailSnapshot(
        version=version,
        nodes=(required, supporting),
        relations=relations,
        checks=checks,
        assessments=assessments,
    )
    inputs = TrailValidationInputs(
        claim=claim,
        sources=(retained_source,),
    )
    return TrailFixture(snapshot=snapshot, inputs=inputs)


def _receipt_fields(proposal_id: str) -> dict[str, str]:
    return {
        "proposal_id": proposal_id,
        "proposal_hash": sha256_hex(f"proposal:{proposal_id}".encode()),
        "audit_event_id": f"audit-{proposal_id}",
        "audit_event_hash": sha256_hex(f"audit:{proposal_id}".encode()),
    }
