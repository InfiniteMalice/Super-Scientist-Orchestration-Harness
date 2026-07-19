from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from super_scientist.domain.evidence.models import VerificationState
from super_scientist.domain.evidence_trails.models import (
    AssessmentCategory,
    ClaimModality,
    EvidenceTrailNode,
    EvidenceTrailSnapshot,
    RelationType,
    ReportSentenceBinding,
    RetainedEvidenceSource,
    TrailAssessment,
    TrailCheckCategory,
    TrailNodeRole,
    TrailOutcome,
    TrailValidationInputs,
    TrailValidationResult,
)
from super_scientist.domain.identity import ActorKind, are_independent
from super_scientist.domain.improvement.classification import is_authoritative_verification
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
)
from super_scientist.domain.primitives import sha256_hex

_MODALITY_STRENGTH = {
    ClaimModality.ASSERTED: 0,
    ClaimModality.QUALIFIED: 1,
    ClaimModality.UNCERTAIN: 2,
    ClaimModality.HYPOTHETICAL: 3,
    ClaimModality.COUNTERFACTUAL: 4,
    ClaimModality.ABSTAINED: 5,
}


def validate_report_binding(
    binding: ReportSentenceBinding,
    trail: EvidenceTrailSnapshot,
    retained: TrailValidationInputs,
) -> tuple[str, ...]:
    """Return deterministic findings for one derived sentence-to-trail binding."""

    findings: set[str] = set()
    version = trail.version
    validation = validate_trail(trail, retained)
    if validation.outcome is TrailOutcome.INVALID_TRAIL:
        findings.add("RETAINED_TRAIL_INVALID")
    if binding.trail_version_id != version.trail_version_id:
        findings.add("REPORT_TRAIL_VERSION_MISMATCH")
    if binding.claim_version_id != version.claim_version_id or binding.claim_version_id != (
        f"{retained.claim.claim_id}:{retained.claim.version}"
    ):
        findings.add("REPORT_CLAIM_VERSION_MISMATCH")
    if binding.governing_policy_hash != version.governing_policy_hash:
        findings.add("REPORT_POLICY_MISMATCH")
    if binding.outcome is not version.status or binding.outcome is TrailOutcome.INVALID_TRAIL:
        findings.add("REPORT_OUTCOME_MISMATCH")

    nodes_by_id = {node.node_id: node for node in trail.nodes}
    if _has_duplicates(binding.source_node_ids):
        findings.add("REPORT_NODE_ID_DUPLICATE")
    if not set(binding.source_node_ids).issubset(nodes_by_id):
        findings.add("REPORT_UNKNOWN_NODE")
    span_node_ids = tuple(span.node_id for span in binding.source_spans)
    if _has_duplicates(span_node_ids) or set(span_node_ids) != set(binding.source_node_ids):
        findings.add("REPORT_SOURCE_SPAN_SET_MISMATCH")
    for span in binding.source_spans:
        node = nodes_by_id.get(span.node_id)
        if node is None:
            findings.add("REPORT_UNKNOWN_NODE")
            continue
        exact = node.exact_span
        if (
            span.source_id != node.source_id
            or span.evidence_id != node.evidence_id
            or span.start != exact.start
            or span.end != exact.end
            or span.text != exact.text
            or span.content_hash != node.content_hash
        ):
            findings.add("REPORT_SPAN_MISMATCH")

    if (
        _has_duplicates(binding.opposing_node_ids)
        or set(binding.opposing_node_ids) != set(version.opposing_node_ids)
        or (
            version.status is TrailOutcome.CONFLICTED
            and not set(version.opposing_node_ids).issubset(binding.source_node_ids)
        )
    ):
        findings.add("REPORT_OPPOSING_NODES_MISMATCH")
    contradiction_node_ids: set[str] = set()
    for relation in trail.relations:
        if relation.relation_type is not RelationType.CONTRADICTS:
            continue
        endpoints = (relation.source_node_id, relation.target_node_id)
        opposing_endpoints = {
            node_id
            for node_id in endpoints
            if node_id in nodes_by_id and nodes_by_id[node_id].role is TrailNodeRole.OPPOSING
        }
        contradiction_node_ids.update(opposing_endpoints or endpoints)
    if (
        _has_duplicates(binding.contradiction_node_ids)
        or set(binding.contradiction_node_ids) != contradiction_node_ids
    ):
        findings.add("REPORT_CONTRADICTIONS_MISMATCH")

    try:
        claim_modality = ClaimModality(retained.claim.epistemic_modality.upper())
    except ValueError:
        findings.add("REPORT_MODALITY_MISMATCH")
    else:
        if _MODALITY_STRENGTH[binding.modality] < _MODALITY_STRENGTH[claim_modality]:
            findings.add("REPORT_MODALITY_MISMATCH")
    return tuple(sorted(findings))


def validate_trail(
    trail: EvidenceTrailSnapshot,
    retained: TrailValidationInputs,
) -> TrailValidationResult:
    """Purely recompute one complete trail version from retained source bytes and claim."""

    findings: dict[TrailCheckCategory, set[str]] = {
        category: set() for category in TrailCheckCategory
    }
    unclassified_findings: set[str] = set()

    def add(category: TrailCheckCategory, code: str) -> None:
        findings[category].add(code)

    version = trail.version
    nodes_by_id = _unique_by_id(
        trail.nodes,
        lambda node: node.node_id,
        lambda: add(TrailCheckCategory.GRAPH_MEMBERSHIP, "DUPLICATE_NODE_ID"),
    )
    relations_by_id = _unique_by_id(
        trail.relations,
        lambda relation: relation.relation_id,
        lambda: add(TrailCheckCategory.RELATION_SCHEMA, "DUPLICATE_RELATION_ID"),
    )
    sources_by_id = _unique_by_id(
        retained.sources,
        lambda source: source.source_id,
        lambda: add(TrailCheckCategory.SOURCE_EXISTENCE, "DUPLICATE_SOURCE_ID"),
    )

    expected_claim_version_id = f"{retained.claim.claim_id}:{retained.claim.version}"
    if version.claim_version_id != expected_claim_version_id:
        add(TrailCheckCategory.GROUNDEDNESS, "CLAIM_VERSION_MISMATCH")
    if version.construction_method.value != "SOURCE_FIRST":
        add(TrailCheckCategory.GROUNDEDNESS, "CONCLUSION_FIRST_UNSUPPORTED")

    node_source_ids: list[str] = []
    node_evidence_ids: set[str] = set()
    decoded_sources: dict[str, str] = {}
    for source_id, retained_source in sources_by_id.items():
        evidence = retained_source.evidence
        if evidence.verification_state is not VerificationState.HASH_VERIFIED:
            add(TrailCheckCategory.EVIDENCE_EXISTENCE, "EVIDENCE_NOT_HASH_VERIFIED")
        data = retained_source.artifact_bytes
        if len(data) != evidence.artifact.size_bytes:
            add(TrailCheckCategory.ARTIFACT_FIDELITY, "ARTIFACT_SIZE_MISMATCH")
        if sha256_hex(data) != evidence.artifact.sha256:
            add(TrailCheckCategory.ARTIFACT_FIDELITY, "CONTENT_HASH_MISMATCH")
        if not evidence.artifact.media_type.startswith("text/"):
            add(TrailCheckCategory.EXACT_SPAN_FIDELITY, "NON_TEXT_SOURCE")
            continue
        try:
            decoded_sources[source_id] = data.decode("utf-8")
        except UnicodeDecodeError:
            add(TrailCheckCategory.EXACT_SPAN_FIDELITY, "INVALID_UTF8_SOURCE")

    for node in trail.nodes:
        if node.trail_version_id != version.trail_version_id:
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CROSS_VERSION_NODE")
        node_source_ids.append(node.source_id)
        node_evidence_ids.add(node.evidence_id)
        source = sources_by_id.get(node.source_id)
        if source is None:
            add(TrailCheckCategory.SOURCE_EXISTENCE, "MISSING_SOURCE")
            continue
        if source.evidence.evidence_id != node.evidence_id:
            add(TrailCheckCategory.EVIDENCE_EXISTENCE, "EVIDENCE_SOURCE_MISMATCH")
        source_text = decoded_sources.get(node.source_id)
        if source_text is None:
            continue
        span = node.exact_span
        if span.end > len(source_text) or source_text[span.start : span.end] != span.text:
            add(TrailCheckCategory.EXACT_SPAN_FIDELITY, "EXACT_SPAN_MISMATCH")
        if sha256_hex(span.text.encode("utf-8")) != node.content_hash:
            add(TrailCheckCategory.ARTIFACT_FIDELITY, "NODE_CONTENT_HASH_MISMATCH")
        location = node.structural_location
        if not (
            0 <= location.start <= span.start
            and span.end <= location.end <= len(source_text)
        ):
            add(TrailCheckCategory.STRUCTURAL_BOUNDS, "STRUCTURAL_BOUNDS_INVALID")
        evidence_span = source.evidence.extracted_span
        if evidence_span is not None and not (
            evidence_span.start <= span.start
            and span.end <= evidence_span.end
            and source_text[evidence_span.start : evidence_span.end] == evidence_span.text
        ):
            add(TrailCheckCategory.EXACT_SPAN_FIDELITY, "OUTSIDE_EVIDENCE_SPAN")

    if _has_duplicates(version.source_ids) or set(version.source_ids) != set(node_source_ids):
        add(TrailCheckCategory.SOURCE_EXISTENCE, "SOURCE_ID_SET_MISMATCH")

    role_fields = {
        TrailNodeRole.REQUIRED: version.required_node_ids,
        TrailNodeRole.SUPPORTING: version.supporting_node_ids,
        TrailNodeRole.OPPOSING: version.opposing_node_ids,
        TrailNodeRole.REDUNDANT: version.redundant_node_ids,
    }
    declared_partition = tuple(node_id for values in role_fields.values() for node_id in values)
    if _has_duplicates(declared_partition) or set(declared_partition) != set(nodes_by_id):
        add(TrailCheckCategory.GRAPH_MEMBERSHIP, "NODE_PARTITION_MISMATCH")
    for role, declared_ids in role_fields.items():
        actual_ids = {node.node_id for node in trail.nodes if node.role is role}
        if _has_duplicates(declared_ids) or set(declared_ids) != actual_ids:
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "NODE_ROLE_SET_MISMATCH")
    if any(
        (node.role is TrailNodeRole.REQUIRED and not node.necessity)
        or (node.role is TrailNodeRole.REDUNDANT and node.necessity)
        for node in trail.nodes
    ):
        add(TrailCheckCategory.NECESSITY, "NECESSITY_ROLE_MISMATCH")

    causal_relations = []
    ordering_edges: set[tuple[str, str]] = set()
    for relation in trail.relations:
        if relation.trail_version_id != version.trail_version_id:
            add(TrailCheckCategory.RELATION_SCHEMA, "CROSS_VERSION_RELATION")
        source_node = nodes_by_id.get(relation.source_node_id)
        target_node = nodes_by_id.get(relation.target_node_id)
        if source_node is None or target_node is None:
            add(TrailCheckCategory.RELATION_SCHEMA, "UNKNOWN_RELATION_ENDPOINT")
            continue
        if relation.source_node_id == relation.target_node_id:
            add(TrailCheckCategory.RELATION_SCHEMA, "SELF_RELATION")
        endpoint_evidence_ids = {source_node.evidence_id, target_node.evidence_id}
        if (
            _has_duplicates(relation.evidence_ids)
            or not set(relation.evidence_ids)
            or not set(relation.evidence_ids).issubset(endpoint_evidence_ids)
            or not set(relation.evidence_ids).issubset(node_evidence_ids)
        ):
            add(TrailCheckCategory.RELATION_SCHEMA, "RELATION_EVIDENCE_SCOPE_INVALID")
        if relation.relation_type is RelationType.PRECEDES:
            ordering_edges.add((source_node.node_id, target_node.node_id))
            if not _strictly_precedes(source_node, target_node):
                add(TrailCheckCategory.TEMPORAL_ORDER, "TEMPORAL_ORDER_INVALID")
        elif relation.relation_type is RelationType.FOLLOWS:
            ordering_edges.add((target_node.node_id, source_node.node_id))
            if not _strictly_precedes(target_node, source_node):
                add(TrailCheckCategory.TEMPORAL_ORDER, "TEMPORAL_ORDER_INVALID")
        elif relation.relation_type is RelationType.SAME_EVENT:
            if (
                source_node.temporal_position is None
                or target_node.temporal_position is None
                or source_node.temporal_position != target_node.temporal_position
            ):
                add(TrailCheckCategory.TEMPORAL_ORDER, "SAME_EVENT_TIME_MISMATCH")
        if relation.relation_type is RelationType.CAUSES_CANDIDATE:
            causal_relations.append(relation)
            if (
                not relation.causal_support
                or _has_duplicates(relation.causal_support)
                or not set(relation.causal_support).issubset(set(relation.evidence_ids))
                or not _strictly_precedes(source_node, target_node)
            ):
                add(TrailCheckCategory.RELATION_SCHEMA, "CAUSAL_OVERCLAIM")

    constraint_ids: set[str] = set()
    for constraint in version.ordering_constraints:
        if constraint.constraint_id in constraint_ids:
            add(TrailCheckCategory.ORDERING, "DUPLICATE_ORDER_CONSTRAINT_ID")
        constraint_ids.add(constraint.constraint_id)
        before = nodes_by_id.get(constraint.before_node_id)
        after = nodes_by_id.get(constraint.after_node_id)
        if before is None or after is None:
            add(TrailCheckCategory.ORDERING, "UNKNOWN_ORDER_ENDPOINT")
            continue
        ordering_edges.add((before.node_id, after.node_id))
        if not _strictly_precedes(before, after):
            add(TrailCheckCategory.ORDERING, "ORDER_CONSTRAINT_INVALID")
    if _has_cycle(set(nodes_by_id), ordering_edges):
        add(TrailCheckCategory.ORDERING, "ORDERING_CYCLE")

    try:
        claim_modality = ClaimModality(retained.claim.epistemic_modality.upper())
    except ValueError:
        add(TrailCheckCategory.MODALITY, "UNKNOWN_CLAIM_MODALITY")
    else:
        if any(
            _MODALITY_STRENGTH[relation.modality] < _MODALITY_STRENGTH[claim_modality]
            for relation in trail.relations
        ):
            add(TrailCheckCategory.MODALITY, "MODALITY_OVERCLAIM")

    assessments_by_id = _unique_by_id(
        trail.assessments,
        lambda assessment: assessment.assessment_id,
        lambda: add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "DUPLICATE_ASSESSMENT_ID"),
    )
    if (
        _has_duplicates(version.assessment_ids)
        or set(version.assessment_ids) != set(assessments_by_id)
    ):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_ID_MISMATCH")
    category_counts = Counter(assessment.category for assessment in trail.assessments)
    if any(category_counts[category] == 0 for category in AssessmentCategory):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_CATEGORY_MISSING")
    if any(category_counts[category] > 1 for category in AssessmentCategory):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_CATEGORY_DUPLICATE")

    assessment_actor_ids: set[str] = set()
    assessment_configurations: set[str] = set()
    assessments_by_category = {
        assessment.category: assessment for assessment in trail.assessments
    }
    for assessment in trail.assessments:
        provenance = assessment.provenance
        if assessment.trail_version_id != version.trail_version_id:
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "CROSS_VERSION_ASSESSMENT")
        if not set(assessment.node_ids).issubset(nodes_by_id):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "UNKNOWN_ASSESSMENT_NODE")
        if not set(assessment.relation_ids).issubset(relations_by_id):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "UNKNOWN_ASSESSMENT_RELATION")
        if (
            not set(assessment.evidence_ids).issubset(node_evidence_ids)
            or set(provenance.evidence_ids) != set(assessment.evidence_ids)
        ):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_EVIDENCE_MISMATCH")
        if provenance.governing_policy_hash != version.governing_policy_hash:
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_POLICY_MISMATCH")
        if set(provenance.checks_run) != set(version.check_ids):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_CHECK_MISMATCH")
        actor = provenance.actor
        configuration = actor.configuration_hash
        repeated_actor = actor.actor_id in assessment_actor_ids
        repeated_configuration = (
            configuration is not None and configuration in assessment_configurations
        )
        same_builder_configuration = (
            configuration is not None
            and configuration == version.constructed_by.configuration_hash
        )
        if (
            provenance.proposer_relationship is not ActorRelationship.INDEPENDENT
            or not are_independent(actor, version.constructed_by)
            or actor.actor_id == source_actor_id_for(assessment, sources_by_id)
            or repeated_actor
            or repeated_configuration
            or same_builder_configuration
        ):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_NOT_INDEPENDENT")
        assessment_actor_ids.add(actor.actor_id)
        if configuration is not None:
            assessment_configurations.add(configuration)
        if not is_authoritative_verification(provenance.category):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_NOT_AUTHORITATIVE")
        if actor.kind is ActorKind.MODEL and (
            actor.provider_id is None or actor.model_id is None or configuration is None
        ):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_MODEL_IDENTITY_INCOMPLETE")

    causal_assessment = assessments_by_category.get(AssessmentCategory.CAUSAL_OVERCLAIM_RISK)
    if causal_relations and (
        causal_assessment is None
        or causal_assessment.provenance.result is not AssessmentOutcome.PASSED
    ):
        add(TrailCheckCategory.RELATION_SCHEMA, "CAUSAL_OVERCLAIM")

    checks_by_id = _unique_by_id(
        trail.checks,
        lambda check: check.check_id,
        lambda: add(TrailCheckCategory.GRAPH_MEMBERSHIP, "DUPLICATE_CHECK_ID"),
    )
    if _has_duplicates(version.check_ids) or set(version.check_ids) != set(checks_by_id):
        add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_ID_MISMATCH")
    check_category_counts = Counter(check.category for check in trail.checks)
    if any(check_category_counts[category] != 1 for category in TrailCheckCategory):
        add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_CATEGORY_MISMATCH")

    for check in trail.checks:
        if check.trail_version_id != version.trail_version_id:
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CROSS_VERSION_CHECK")
        if (
            set(check.node_ids) != set(nodes_by_id)
            or set(check.relation_ids) != set(relations_by_id)
            or set(check.evidence_ids) != node_evidence_ids
        ):
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_SCOPE_MISMATCH")

    for check in trail.checks:
        expected_codes = findings[check.category]
        if check.passed is bool(expected_codes) or set(check.finding_codes) != expected_codes:
            unclassified_findings.add("CHECK_RESULT_MISMATCH")

    structural_codes = {
        code for category_findings in findings.values() for code in category_findings
    } | unclassified_findings
    semantic_outcome = _semantic_outcome(version.opposing_node_ids, assessments_by_category)
    if version.status is not semantic_outcome:
        structural_codes.add("STATUS_MISMATCH")
    outcome = TrailOutcome.INVALID_TRAIL if structural_codes else semantic_outcome
    return TrailValidationResult(
        trail_version_id=version.trail_version_id,
        outcome=outcome,
        finding_codes=tuple(sorted(structural_codes)),
        required_node_ids=version.required_node_ids,
        opposing_node_ids=version.opposing_node_ids,
        assessment_ids=version.assessment_ids,
    )


def _semantic_outcome(
    opposing_node_ids: tuple[str, ...],
    assessments_by_category: dict[AssessmentCategory, TrailAssessment],
) -> TrailOutcome:
    def result(category: AssessmentCategory) -> AssessmentOutcome | None:
        assessment = assessments_by_category.get(category)
        return None if assessment is None else assessment.provenance.result

    answerability = result(AssessmentCategory.ANSWERABILITY)
    if answerability in {AssessmentOutcome.FAILED, AssessmentOutcome.ABSTAINED}:
        return TrailOutcome.UNANSWERABLE
    if opposing_node_ids:
        return TrailOutcome.CONFLICTED
    if result(AssessmentCategory.NECESSITY) in {
        AssessmentOutcome.FAILED,
        AssessmentOutcome.ABSTAINED,
    } or result(AssessmentCategory.GROUNDEDNESS) in {
        AssessmentOutcome.FAILED,
        AssessmentOutcome.ABSTAINED,
    }:
        return TrailOutcome.INSUFFICIENT
    results = tuple(
        assessment.provenance.result for assessment in assessments_by_category.values()
    )
    if len(results) != len(AssessmentCategory) or any(
        item is AssessmentOutcome.INCONCLUSIVE for item in results
    ):
        return TrailOutcome.PARTIALLY_SUPPORTING
    return TrailOutcome.SUFFICIENT


def _strictly_precedes(source: EvidenceTrailNode, target: EvidenceTrailNode) -> bool:
    return (
        source.temporal_position is not None
        and target.temporal_position is not None
        and source.temporal_position < target.temporal_position
    )


def _has_duplicates(values: tuple[str, ...]) -> bool:
    return len(values) != len(set(values))


def _unique_by_id[ItemT](
    items: tuple[ItemT, ...],
    identifier: Callable[[ItemT], str],
    on_duplicate: Callable[[], None],
) -> dict[str, ItemT]:
    result: dict[str, ItemT] = {}
    for item in items:
        item_id = identifier(item)
        if item_id in result:
            on_duplicate()
        else:
            result[item_id] = item
    return result


def _has_cycle(node_ids: set[str], edges: set[tuple[str, str]]) -> bool:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for source, target in edges:
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(visit(target) for target in adjacency[node_id]):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in sorted(node_ids))


def source_actor_id_for(
    assessment: TrailAssessment,
    sources_by_id: dict[str, RetainedEvidenceSource],
) -> str | None:
    evidence_ids = set(assessment.evidence_ids)
    for source in sources_by_id.values():
        if source.evidence.evidence_id in evidence_ids:
            return source.evidence.ingestion_actor_id
    return None
