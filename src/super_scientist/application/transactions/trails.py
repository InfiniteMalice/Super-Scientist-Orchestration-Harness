from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Connection

from super_scientist.application.evidence_verification import verified_artifact_bytes
from super_scientist.application.trails.service import (
    BindReportSentenceHandler,
    RecordEvidenceTrailVersionHandler,
)
from super_scientist.application.transactions.contracts import ProposalHandler
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.evidence_trails.models import (
    EvidenceTrailNode,
    EvidenceTrailRelation,
    EvidenceTrailSnapshot,
    EvidenceTrailVersion,
    ReportSentenceBinding,
    RetainedEvidenceSource,
    TrailAssessment,
    TrailCheckResult,
    TrailValidationInputs,
)
from super_scientist.kernel.transactions.models import (
    BindReportSentence,
    RecordEvidenceTrailVersion,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.domain_records import (
    EvidenceTrailAssessmentRepository,
    EvidenceTrailCheckRepository,
    EvidenceTrailHeadRepository,
    EvidenceTrailNodeRepository,
    EvidenceTrailRelationRepository,
    EvidenceTrailVersionRepository,
    ReportSentenceBindingRepository,
)
from super_scientist.providers.storage.repositories import ClaimRepository, EvidenceRepository

type FixedTrailHandler = ProposalHandler[BaseModel, BaseModel]


@dataclass(frozen=True)
class TrailCapabilities:
    active_policy: PolicySnapshot
    claims: ClaimRepository
    evidence: EvidenceRepository
    artifacts: ArtifactStore
    versions: EvidenceTrailVersionRepository
    nodes: EvidenceTrailNodeRepository
    relations: EvidenceTrailRelationRepository
    checks: EvidenceTrailCheckRepository
    assessments: EvidenceTrailAssessmentRepository
    bindings: ReportSentenceBindingRepository
    head: EvidenceTrailHeadRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_trail_head(self, trail_id: str) -> tuple[str, int] | None:
        return self.head.get(trail_id)

    def get_snapshot(self, trail_version_id: str) -> EvidenceTrailSnapshot | None:
        version = self.versions.get(trail_version_id)
        if version is None:
            return None
        return EvidenceTrailSnapshot(
            version=version,
            nodes=tuple(
                node for node in self.nodes.list_all() if node.trail_version_id == trail_version_id
            ),
            relations=tuple(
                relation
                for relation in self.relations.list_all()
                if relation.trail_version_id == trail_version_id
            ),
            checks=tuple(
                check
                for check in self.checks.list_all()
                if check.trail_version_id == trail_version_id
            ),
            assessments=tuple(
                assessment
                for assessment in self.assessments.list_all()
                if assessment.trail_version_id == trail_version_id
            ),
        )

    def validation_inputs(
        self,
        snapshot: EvidenceTrailSnapshot,
    ) -> TrailValidationInputs | None:
        claim = self._claim_version(snapshot.version.claim_version_id)
        if claim is None:
            return None
        retained: list[RetainedEvidenceSource] = []
        for node in snapshot.nodes:
            evidence = self.evidence.get(node.evidence_id)
            if evidence is None:
                return None
            retained.append(
                RetainedEvidenceSource(
                    source_id=node.source_id,
                    evidence=evidence,
                    artifact_bytes=verified_artifact_bytes(evidence, self.artifacts),
                )
            )
        unique_sources: dict[str, RetainedEvidenceSource] = {}
        for source in retained:
            prior = unique_sources.get(source.source_id)
            if prior is None:
                unique_sources[source.source_id] = source
            elif prior != source:
                return None
        return TrailValidationInputs(claim=claim, sources=tuple(unique_sources.values()))

    def collision_ids(self, snapshot: EvidenceTrailSnapshot) -> tuple[str, ...]:
        collisions: list[str] = []
        if self.versions.get(snapshot.version.trail_version_id) is not None:
            collisions.append(snapshot.version.trail_version_id)
        for node in snapshot.nodes:
            if self.nodes.get(node.node_id) is not None:
                collisions.append(node.node_id)
        for relation in snapshot.relations:
            if self.relations.get(relation.relation_id) is not None:
                collisions.append(relation.relation_id)
        for check in snapshot.checks:
            if self.checks.get(check.check_id) is not None:
                collisions.append(check.check_id)
        for assessment in snapshot.assessments:
            if self.assessments.get(assessment.assessment_id) is not None:
                collisions.append(assessment.assessment_id)
        return tuple(collisions)

    def get_binding(self, binding_id: str) -> ReportSentenceBinding | None:
        return self.bindings.get(binding_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if isinstance(record, EvidenceTrailVersion):
            self.versions.add(record.trail_version_id, record, record.created_at)
            return
        if isinstance(record, EvidenceTrailNode):
            version = self.versions.get(record.trail_version_id)
            if version is None:
                raise RuntimeError("trail node projection requires its version")
            self.nodes.add(record.node_id, record, version.created_at)
            return
        if isinstance(record, EvidenceTrailRelation):
            version = self.versions.get(record.trail_version_id)
            if version is None:
                raise RuntimeError("trail relation projection requires its version")
            self.relations.add(record.relation_id, record, version.created_at)
            return
        if isinstance(record, TrailCheckResult):
            self.checks.add(record.check_id, record, record.checked_at)
            return
        if isinstance(record, TrailAssessment):
            self.assessments.add(
                record.assessment_id,
                record,
                record.provenance.assessed_at,
            )
            return
        if isinstance(record, ReportSentenceBinding):
            self.bindings.add(record.binding_id, record, record.created_at)
            return
        raise TypeError(f"unsupported evidence-trail record: {type(record)!r}")

    def update_projection(self, record: BaseModel) -> None:
        if not isinstance(record, EvidenceTrailVersion):
            raise TypeError(f"unsupported evidence-trail head record: {type(record)!r}")
        self.head.set(record.trail_id, record.trail_version_id, record.version)

    def _claim_version(self, claim_version_id: str) -> AtomicClaim | None:
        for head in self.claims.list_heads():
            for claim in self.claims.history(head.claim_id):
                if f"{claim.claim_id}:{claim.version}" == claim_version_id:
                    return claim
        return None


def fixed_trail_handlers() -> tuple[FixedTrailHandler, ...]:
    return (  # type: ignore[return-value]
        RecordEvidenceTrailVersionHandler(),
        BindReportSentenceHandler(),
    )


def trail_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
    artifact_store: ArtifactStore,
) -> TrailCapabilities:
    if not isinstance(proposal, (RecordEvidenceTrailVersion, BindReportSentence)):
        raise TypeError(f"no fixed evidence-trail capability for proposal: {type(proposal)!r}")
    return TrailCapabilities(
        active_policy=active_policy,
        claims=ClaimRepository(connection),
        evidence=EvidenceRepository(connection),
        artifacts=artifact_store,
        versions=EvidenceTrailVersionRepository(connection),
        nodes=EvidenceTrailNodeRepository(connection),
        relations=EvidenceTrailRelationRepository(connection),
        checks=EvidenceTrailCheckRepository(connection),
        assessments=EvidenceTrailAssessmentRepository(connection),
        bindings=ReportSentenceBindingRepository(connection),
        head=EvidenceTrailHeadRepository(connection),
    )
