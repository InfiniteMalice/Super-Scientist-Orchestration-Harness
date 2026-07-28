from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.evidence_trails.models import (
    AddEvidenceReceiptRef,
    AssessmentCategory,
    CausalSupport,
    ClaimModality,
    ClaimStageReceiptRef,
    EvidenceTrailNode,
    EvidenceTrailNodeStageReceiptRef,
    EvidenceTrailRelation,
    EvidenceTrailRelationStageReceiptRef,
    EvidenceTrailSnapshot,
    RelationType,
    ReportSourceSpan,
    SourceFirstProvenance,
    StructuralLocation,
    TrailAssessment,
    TrailGeometry,
    TrailNodeRole,
    TrailOutcome,
)
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.improvement.classification import ExternalGrounding
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import NonBlankText, canonical_json_bytes, sha256_hex

TRUSTED_TRAIL_CHECKER_ID = "super-scientist-trail-validator"
TRUSTED_TRAIL_CHECKER_VERSION = "2"
CAUSAL_RELATION_TYPES = frozenset(
    {
        RelationType.CAUSES_CANDIDATE,
        RelationType.ENABLES,
        RelationType.PREVENTS,
    }
)


class RelationTemporalRule(StrEnum):
    ANY = "ANY"
    SOURCE_BEFORE_TARGET = "SOURCE_BEFORE_TARGET"
    TARGET_BEFORE_SOURCE = "TARGET_BEFORE_SOURCE"
    SAME_TIME = "SAME_TIME"


class RelationIdentityRule(StrEnum):
    NONE = "NONE"
    SAME_ENTITY = "SAME_ENTITY"
    SAME_EVENT = "SAME_EVENT"


class RelationSchema(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    relation_type: RelationType
    allowed_role_pairs: tuple[tuple[TrailNodeRole, TrailNodeRole], ...] = Field(min_length=1)
    allowed_modalities: tuple[ClaimModality, ...] = Field(min_length=1)
    temporal_rule: RelationTemporalRule
    causal: bool
    identity_rule: RelationIdentityRule
    requires_opposing: bool


_NON_REDUNDANT_ROLES = (
    TrailNodeRole.REQUIRED,
    TrailNodeRole.SUPPORTING,
    TrailNodeRole.OPPOSING,
)
_NON_REDUNDANT_PAIRS = tuple(
    (source_role, target_role)
    for source_role in _NON_REDUNDANT_ROLES
    for target_role in _NON_REDUNDANT_ROLES
)
_SUPPORT_PAIRS = tuple(
    (source_role, target_role)
    for source_role in (TrailNodeRole.REQUIRED, TrailNodeRole.SUPPORTING)
    for target_role in (TrailNodeRole.REQUIRED, TrailNodeRole.SUPPORTING)
)
_OPPOSING_PAIRS = tuple(pair for pair in _NON_REDUNDANT_PAIRS if TrailNodeRole.OPPOSING in pair)
_ALL_MODALITIES = tuple(ClaimModality)
_CAUSAL_MODALITIES = (
    ClaimModality.QUALIFIED,
    ClaimModality.UNCERTAIN,
    ClaimModality.HYPOTHETICAL,
    ClaimModality.COUNTERFACTUAL,
)


def _relation_schema(
    relation_type: RelationType,
    *,
    role_pairs: tuple[tuple[TrailNodeRole, TrailNodeRole], ...] = _NON_REDUNDANT_PAIRS,
    modalities: tuple[ClaimModality, ...] = _ALL_MODALITIES,
    temporal: RelationTemporalRule = RelationTemporalRule.ANY,
    causal: bool = False,
    identity: RelationIdentityRule = RelationIdentityRule.NONE,
    requires_opposing: bool = False,
) -> RelationSchema:
    return RelationSchema(
        relation_type=relation_type,
        allowed_role_pairs=role_pairs,
        allowed_modalities=modalities,
        temporal_rule=temporal,
        causal=causal,
        identity_rule=identity,
        requires_opposing=requires_opposing,
    )


RELATION_SCHEMAS: Mapping[RelationType, RelationSchema] = MappingProxyType(
    {
        RelationType.SUPPORTS: _relation_schema(
            RelationType.SUPPORTS,
            role_pairs=_SUPPORT_PAIRS,
        ),
        RelationType.CONTRADICTS: _relation_schema(
            RelationType.CONTRADICTS,
            role_pairs=_OPPOSING_PAIRS,
            requires_opposing=True,
        ),
        RelationType.PRECEDES: _relation_schema(
            RelationType.PRECEDES,
            temporal=RelationTemporalRule.SOURCE_BEFORE_TARGET,
        ),
        RelationType.FOLLOWS: _relation_schema(
            RelationType.FOLLOWS,
            temporal=RelationTemporalRule.TARGET_BEFORE_SOURCE,
        ),
        RelationType.CAUSES_CANDIDATE: _relation_schema(
            RelationType.CAUSES_CANDIDATE,
            modalities=_CAUSAL_MODALITIES,
            temporal=RelationTemporalRule.SOURCE_BEFORE_TARGET,
            causal=True,
        ),
        RelationType.ENABLES: _relation_schema(
            RelationType.ENABLES,
            modalities=_CAUSAL_MODALITIES,
            temporal=RelationTemporalRule.SOURCE_BEFORE_TARGET,
            causal=True,
        ),
        RelationType.PREVENTS: _relation_schema(
            RelationType.PREVENTS,
            modalities=_CAUSAL_MODALITIES,
            temporal=RelationTemporalRule.SOURCE_BEFORE_TARGET,
            causal=True,
        ),
        RelationType.QUALIFIES: _relation_schema(RelationType.QUALIFIES),
        RelationType.EXPLAINS: _relation_schema(RelationType.EXPLAINS),
        RelationType.SAME_ENTITY: _relation_schema(
            RelationType.SAME_ENTITY,
            identity=RelationIdentityRule.SAME_ENTITY,
        ),
        RelationType.SAME_EVENT: _relation_schema(
            RelationType.SAME_EVENT,
            temporal=RelationTemporalRule.SAME_TIME,
            identity=RelationIdentityRule.SAME_EVENT,
        ),
        RelationType.DEPENDS_ON: _relation_schema(RelationType.DEPENDS_ON),
        RelationType.ALTERNATIVE_EXPLANATION: _relation_schema(
            RelationType.ALTERNATIVE_EXPLANATION,
            role_pairs=_OPPOSING_PAIRS,
            modalities=_CAUSAL_MODALITIES,
            requires_opposing=True,
        ),
    }
)


class SourceStructureIndex(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1] = 1
    locations: tuple[StructuralLocation, ...] = Field(min_length=1)


class TrailScope(BaseModel):
    """One exact, ordered graph scope derived from a complete trail snapshot."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    node_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


class EvidenceIdentityProvenance(BaseModel):
    """Strict typed view of legacy string-only evidence identity provenance."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    entity_id: NonBlankText | None = None
    event_id: NonBlankText | None = None


def parse_identity_provenance(
    evidence: EvidenceRecord,
) -> EvidenceIdentityProvenance:
    parsed: dict[str, str | None] = {}
    for key in ("entity_id", "event_id"):
        value = evidence.provenance.get(key)
        if value is None:
            parsed[key] = None
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"evidence provenance {key} must be a nonblank string")
        parsed[key] = value.strip()
    return EvidenceIdentityProvenance(**parsed)


def canonical_evidence_ids(nodes: tuple[EvidenceTrailNode, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(node.evidence_id for node in nodes))


def trusted_check_id(trail_version_id: str, category: object) -> str:
    value = getattr(category, "value", None)
    if not isinstance(value, str):
        raise TypeError("trusted check category must be a string enum")
    return f"{trail_version_id}:check:{value.lower()}"


def trusted_assessment_id(trail_version_id: str, category: AssessmentCategory) -> str:
    return f"{trail_version_id}:assessment:{category.value.lower()}"


def parse_external_grounding(evidence: EvidenceRecord) -> ExternalGrounding:
    """Read the one retained provenance field that authorizes source grounding."""

    value = evidence.provenance.get("external_grounding")
    if not isinstance(value, str):
        raise ValueError("retained evidence has no exact external_grounding provenance")
    try:
        return ExternalGrounding(value)
    except ValueError as error:
        raise ValueError("retained evidence has unknown external_grounding provenance") from error


def parse_source_structure(evidence: EvidenceRecord) -> SourceStructureIndex:
    observation = evidence.structured_observation
    if not isinstance(observation, Mapping):
        raise ValueError("retained evidence has no source_structure index")
    raw = observation.get("source_structure")
    if not isinstance(raw, Mapping):
        raise ValueError("retained evidence has no exact source_structure index")
    try:
        return SourceStructureIndex.model_validate_json(
            canonical_json_bytes(_plain_json_value(raw)),
            strict=True,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise ValueError("retained evidence has an invalid source_structure index") from error


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_plain_json_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError("source structure contains a non-JSON value")


def canonical_relation_evidence_ids(
    relation: EvidenceTrailRelation,
    nodes: tuple[EvidenceTrailNode, ...],
) -> tuple[str, ...]:
    nodes_by_id = {node.node_id: node for node in nodes}
    if relation.source_node_id not in nodes_by_id or relation.target_node_id not in nodes_by_id:
        return ()
    return tuple(
        dict.fromkeys(
            (
                nodes_by_id[relation.source_node_id].evidence_id,
                nodes_by_id[relation.target_node_id].evidence_id,
            )
        )
    )


def required_report_nodes(
    trail: EvidenceTrailSnapshot,
    outcome: TrailOutcome,
) -> tuple[EvidenceTrailNode, ...]:
    if outcome is TrailOutcome.INVALID_TRAIL:
        return ()
    allowed_roles = {TrailNodeRole.REQUIRED, TrailNodeRole.SUPPORTING}
    if outcome is TrailOutcome.CONFLICTED:
        allowed_roles.add(TrailNodeRole.OPPOSING)
    return tuple(node for node in trail.nodes if node.role in allowed_roles)


def required_report_spans(
    trail: EvidenceTrailSnapshot,
    outcome: TrailOutcome,
) -> tuple[ReportSourceSpan, ...]:
    return tuple(
        ReportSourceSpan(
            node_id=node.node_id,
            source_id=node.source_id,
            evidence_id=node.evidence_id,
            start=node.exact_span.start,
            end=node.exact_span.end,
            text=node.exact_span.text,
            content_hash=node.content_hash,
        )
        for node in required_report_nodes(trail, outcome)
    )


def required_contradiction_node_ids(
    trail: EvidenceTrailSnapshot,
) -> tuple[str, ...]:
    participants = {
        node_id
        for relation in trail.relations
        if relation.relation_type is RelationType.CONTRADICTS
        for node_id in (relation.source_node_id, relation.target_node_id)
    }
    return tuple(node.node_id for node in trail.nodes if node.node_id in participants)


def required_opposing_report_node_ids(
    trail: EvidenceTrailSnapshot,
) -> tuple[str, ...]:
    contradiction_ids = set(required_contradiction_node_ids(trail))
    return tuple(
        node.node_id
        for node in trail.nodes
        if node.role is TrailNodeRole.OPPOSING and node.node_id in contradiction_ids
    )


def derive_geometry(trail: EvidenceTrailSnapshot) -> TrailGeometry:
    return derive_geometry_from_graph(trail.nodes, trail.relations)


def derive_geometry_from_graph(
    nodes: tuple[EvidenceTrailNode, ...],
    relations: tuple[EvidenceTrailRelation, ...],
) -> TrailGeometry:
    node_ids = {node.node_id for node in nodes}
    if len(node_ids) <= 1:
        return TrailGeometry.LINEAR
    directed_edges = {
        (relation.source_node_id, relation.target_node_id)
        for relation in relations
        if relation.source_node_id in node_ids and relation.target_node_id in node_ids
    }
    if any(source == target for source, target in directed_edges):
        return TrailGeometry.NETWORK
    undirected_edges = {frozenset((source, target)) for source, target in directed_edges}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in undirected_edges:
        if len(edge) != 2:
            return TrailGeometry.NETWORK
        source, target = tuple(edge)
        adjacency[source].add(target)
        adjacency[target].add(source)
    pending = [min(node_ids)]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(adjacency[current].difference(visited), reverse=True))
    if visited != node_ids or len(undirected_edges) != len(node_ids) - 1:
        return TrailGeometry.NETWORK
    indegree = {node_id: 0 for node_id in node_ids}
    outdegree = {node_id: 0 for node_id in node_ids}
    for source, target in directed_edges:
        outdegree[source] += 1
        indegree[target] += 1
    convergent = any(value > 1 for value in indegree.values())
    divergent = any(value > 1 for value in outdegree.values())
    if convergent and divergent:
        return TrailGeometry.BRANCHED
    if convergent:
        return TrailGeometry.CONVERGENT
    if divergent:
        return TrailGeometry.DIVERGENT
    return TrailGeometry.LINEAR


def derive_causal_positions(
    trail: EvidenceTrailSnapshot,
) -> Mapping[str, int | None] | None:
    return derive_causal_positions_from_graph(trail.nodes, trail.relations)


def derive_causal_positions_from_graph(
    nodes: tuple[EvidenceTrailNode, ...],
    relations: tuple[EvidenceTrailRelation, ...],
) -> Mapping[str, int | None] | None:
    node_ids = {node.node_id for node in nodes}
    causal_edges = {
        (relation.source_node_id, relation.target_node_id)
        for relation in relations
        if relation.relation_type in CAUSAL_RELATION_TYPES
        and relation.source_node_id in node_ids
        and relation.target_node_id in node_ids
    }
    members = {node_id for edge in causal_edges for node_id in edge}
    positions: dict[str, int | None] = {node_id: None for node_id in node_ids}
    if not members:
        return MappingProxyType(positions)
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in members}
    successors: dict[str, set[str]] = {node_id: set() for node_id in members}
    for source, target in causal_edges:
        predecessors[target].add(source)
        successors[source].add(target)
    remaining = {node_id: len(predecessors[node_id]) for node_id in members}
    ready = sorted(node_id for node_id, degree in remaining.items() if degree == 0)
    processed: list[str] = []
    while ready:
        current = ready.pop(0)
        processed.append(current)
        parent_layers = tuple(positions[parent] for parent in predecessors[current])
        if any(layer is None for layer in parent_layers):
            return None
        positions[current] = (
            0
            if not parent_layers
            else max(layer for layer in parent_layers if layer is not None) + 1
        )
        for target in sorted(successors[current]):
            remaining[target] -= 1
            if remaining[target] == 0:
                ready.append(target)
                ready.sort()
    if len(processed) != len(members):
        return None
    return MappingProxyType(positions)


def trail_actors_are_independent(
    left: ActorIdentity,
    right: ActorIdentity,
) -> bool:
    """Apply Task 7 independence across IDs, model identity, and configurations."""

    if left.actor_id == right.actor_id:
        return False
    if (
        left.configuration_hash is not None
        and right.configuration_hash is not None
        and left.configuration_hash == right.configuration_hash
    ):
        return False
    left_model = (left.provider_id, left.model_id, left.adapter_id)
    right_model = (right.provider_id, right.model_id, right.adapter_id)
    return not (
        left.provider_id is not None
        and left.model_id is not None
        and right.provider_id is not None
        and right.model_id is not None
        and left_model == right_model
    )


def canonical_node_set_hash(nodes: tuple[EvidenceTrailNode, ...]) -> str:
    return sha256_hex(canonical_json_bytes(tuple(node.model_dump(mode="json") for node in nodes)))


def build_source_first_provenance(
    *,
    source_receipts: tuple[AddEvidenceReceiptRef, ...],
    node_stage_receipt: EvidenceTrailNodeStageReceiptRef,
    relation_stage_receipt: EvidenceTrailRelationStageReceiptRef,
    claim_stage_receipt: ClaimStageReceiptRef,
) -> SourceFirstProvenance:
    """Construct source-first provenance from accepted immutable receipt references."""

    return SourceFirstProvenance(
        source_receipts=source_receipts,
        node_stage_receipt=node_stage_receipt,
        relation_stage_receipt=relation_stage_receipt,
        claim_stage_receipt=claim_stage_receipt,
    )


def required_assessment_scope(
    category: AssessmentCategory,
    nodes: tuple[EvidenceTrailNode, ...],
    relations: tuple[EvidenceTrailRelation, ...],
) -> TrailScope:
    """Derive the category's exact ordered scope from retained graph records."""

    all_node_ids = tuple(node.node_id for node in nodes)
    all_relation_ids = tuple(relation.relation_id for relation in relations)
    all_evidence_ids = canonical_evidence_ids(nodes)
    no_relations = {
        AssessmentCategory.NECESSITY,
        AssessmentCategory.RUBRIC_FIDELITY,
        AssessmentCategory.CONTAMINATION,
    }
    if category in no_relations:
        return TrailScope(
            node_ids=all_node_ids,
            relation_ids=(),
            evidence_ids=all_evidence_ids,
        )
    if category is AssessmentCategory.ANSWERABILITY:
        relevant_nodes = tuple(node for node in nodes if node.role is not TrailNodeRole.REDUNDANT)
        relevant_ids = {node.node_id for node in relevant_nodes}
        relevant_relations = tuple(
            relation
            for relation in relations
            if relation.source_node_id in relevant_ids and relation.target_node_id in relevant_ids
        )
        return TrailScope(
            node_ids=tuple(node.node_id for node in relevant_nodes),
            relation_ids=tuple(relation.relation_id for relation in relevant_relations),
            evidence_ids=canonical_evidence_ids(relevant_nodes) or all_evidence_ids,
        )
    return TrailScope(
        node_ids=all_node_ids,
        relation_ids=all_relation_ids,
        evidence_ids=all_evidence_ids,
    )


def required_causal_support(
    relation: EvidenceTrailRelation,
    nodes: tuple[EvidenceTrailNode, ...],
) -> tuple[CausalSupport, ...]:
    """Bind a causal assertion to both exact retained endpoint spans."""

    nodes_by_id = {node.node_id: node for node in nodes}
    endpoint_ids = (relation.source_node_id, relation.target_node_id)
    if any(node_id not in nodes_by_id for node_id in endpoint_ids):
        return ()
    return tuple(
        CausalSupport(
            support_id=f"{relation.relation_id}:causal:{node_id}",
            trail_version_id=relation.trail_version_id,
            relation_id=relation.relation_id,
            node_id=node_id,
            evidence_id=nodes_by_id[node_id].evidence_id,
            exact_span=nodes_by_id[node_id].exact_span,
            content_hash=nodes_by_id[node_id].content_hash,
        )
        for node_id in endpoint_ids
    )


_PASSED = MappingProxyType(
    {
        AssessmentOutcome.PASSED: None,
        AssessmentOutcome.FAILED: TrailOutcome.INSUFFICIENT,
        AssessmentOutcome.INCONCLUSIVE: TrailOutcome.PARTIALLY_SUPPORTING,
        AssessmentOutcome.ABSTAINED: TrailOutcome.UNANSWERABLE,
    }
)
_ANSWERABILITY = MappingProxyType(
    {
        AssessmentOutcome.PASSED: None,
        AssessmentOutcome.FAILED: TrailOutcome.UNANSWERABLE,
        AssessmentOutcome.INCONCLUSIVE: TrailOutcome.PARTIALLY_SUPPORTING,
        AssessmentOutcome.ABSTAINED: TrailOutcome.UNANSWERABLE,
    }
)

ASSESSMENT_OUTCOME_MATRIX: Mapping[
    AssessmentCategory,
    Mapping[AssessmentOutcome, TrailOutcome | None],
] = MappingProxyType(
    {
        AssessmentCategory.NECESSITY: _PASSED,
        AssessmentCategory.GROUNDEDNESS: _PASSED,
        AssessmentCategory.RELATION_FIDELITY: _PASSED,
        AssessmentCategory.COUNTEREVIDENCE: _PASSED,
        AssessmentCategory.CAUSAL_OVERCLAIM_RISK: _PASSED,
        AssessmentCategory.RUBRIC_FIDELITY: _PASSED,
        AssessmentCategory.CONTAMINATION: _PASSED,
        AssessmentCategory.ANSWERABILITY: _ANSWERABILITY,
    }
)


def semantic_assessment_outcome(
    outcomes: Mapping[AssessmentCategory, AssessmentOutcome],
    *,
    conflicted: bool,
) -> TrailOutcome:
    """Apply every required assessment result before considering success."""

    mapped = tuple(
        ASSESSMENT_OUTCOME_MATRIX[category][outcomes[category]] for category in AssessmentCategory
    )
    if TrailOutcome.UNANSWERABLE in mapped:
        return TrailOutcome.UNANSWERABLE
    if TrailOutcome.INSUFFICIENT in mapped:
        return TrailOutcome.INSUFFICIENT
    if conflicted:
        return TrailOutcome.CONFLICTED
    if TrailOutcome.PARTIALLY_SUPPORTING in mapped:
        return TrailOutcome.PARTIALLY_SUPPORTING
    return TrailOutcome.SUFFICIENT


def derive_trail_outcome(
    assessments: tuple[TrailAssessment, ...],
    *,
    conflicted: bool,
) -> TrailOutcome:
    """Derive a trail status from the complete exact assessment matrix."""

    outcomes = {assessment.category: assessment.provenance.result for assessment in assessments}
    if set(outcomes) != set(AssessmentCategory) or len(assessments) != len(AssessmentCategory):
        return TrailOutcome.INVALID_TRAIL
    return semantic_assessment_outcome(outcomes, conflicted=conflicted)
