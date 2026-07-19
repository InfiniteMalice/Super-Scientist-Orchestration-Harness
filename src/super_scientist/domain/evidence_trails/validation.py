from __future__ import annotations

from collections.abc import Callable

from super_scientist.domain.evidence.models import VerificationState
from super_scientist.domain.evidence_trails.authority import (
    RELATION_SCHEMAS,
    TRUSTED_TRAIL_CHECKER_ID,
    TRUSTED_TRAIL_CHECKER_VERSION,
    RelationIdentityRule,
    RelationTemporalRule,
    canonical_evidence_ids,
    canonical_relation_evidence_ids,
    claim_content_hash,
    derive_causal_positions,
    derive_geometry,
    parse_external_grounding,
    parse_source_structure,
    relation_content_hash,
    required_assessment_scope,
    required_causal_support,
    required_contradiction_node_ids,
    required_opposing_report_node_ids,
    required_report_nodes,
    required_report_spans,
    semantic_assessment_outcome,
    source_first_event_id,
    trail_actors_are_independent,
    trusted_assessment_id,
    trusted_check_id,
)
from super_scientist.domain.evidence_trails.models import (
    AssessmentCategory,
    ClaimModality,
    EvidenceTrailNode,
    EvidenceTrailSnapshot,
    RelationType,
    ReportSentenceBinding,
    RetainedEvidenceSource,
    SourceFirstStageEvent,
    SourceFirstStageKind,
    TrailAssessment,
    TrailCheckCategory,
    TrailNodeRole,
    TrailOutcome,
    TrailValidationInputs,
    TrailValidationResult,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ExternalGrounding,
    is_authoritative_verification,
)
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
    expected_nodes = required_report_nodes(trail, binding.outcome)
    expected_node_ids = tuple(node.node_id for node in expected_nodes)
    if _has_duplicates(binding.source_node_ids):
        findings.add("REPORT_NODE_ID_DUPLICATE")
    if binding.source_node_ids != expected_node_ids:
        findings.add("REPORT_NODE_SCOPE_MISMATCH")
    if not set(binding.source_node_ids).issubset(nodes_by_id):
        findings.add("REPORT_UNKNOWN_NODE")
    span_node_ids = tuple(span.node_id for span in binding.source_spans)
    if _has_duplicates(span_node_ids) or set(span_node_ids) != set(binding.source_node_ids):
        findings.add("REPORT_SOURCE_SPAN_SET_MISMATCH")
    if binding.source_spans != required_report_spans(trail, binding.outcome):
        findings.add("REPORT_SPAN_MISMATCH")
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

    expected_opposing = required_opposing_report_node_ids(trail)
    if _has_duplicates(binding.opposing_node_ids) or binding.opposing_node_ids != expected_opposing:
        findings.add("REPORT_OPPOSING_NODES_MISMATCH")
    expected_contradictions = required_contradiction_node_ids(trail)
    if (
        _has_duplicates(binding.contradiction_node_ids)
        or binding.contradiction_node_ids != expected_contradictions
    ):
        findings.add("REPORT_CONTRADICTIONS_MISMATCH")
    if (
        version.status is TrailOutcome.CONFLICTED
        and binding.modality is ClaimModality.ASSERTED
    ):
        findings.add("REPORT_CONFLICT_MODALITY_INVALID")

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
    _unique_by_id(
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
    source_structures: dict[str, tuple[object, ...]] = {}
    for source_id, retained_source in sources_by_id.items():
        evidence = retained_source.evidence
        try:
            grounding = parse_external_grounding(evidence)
        except ValueError:
            grounding = None
        if grounding is not ExternalGrounding.PRIMARY_SOURCE:
            add(
                TrailCheckCategory.GROUNDEDNESS,
                "PRIMARY_SOURCE_GROUNDING_REQUIRED",
            )
        try:
            source_structure = parse_source_structure(evidence)
        except ValueError:
            add(TrailCheckCategory.STRUCTURAL_BOUNDS, "SOURCE_STRUCTURE_INVALID")
        else:
            source_structures[source_id] = source_structure.locations
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

    provenance = getattr(version, "source_first_provenance", None)
    if provenance is None:
        add(
            TrailCheckCategory.GROUNDEDNESS,
            "SOURCE_FIRST_PROVENANCE_REQUIRED",
        )
    elif not _source_first_provenance_matches(trail, retained, sources_by_id):
        add(
            TrailCheckCategory.GROUNDEDNESS,
            "SOURCE_FIRST_PROVENANCE_MISMATCH",
        )

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
        if location not in source_structures.get(node.source_id, ()):
            add(
                TrailCheckCategory.STRUCTURAL_BOUNDS,
                "STRUCTURAL_LOCATION_MISMATCH",
            )
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
    contradiction_opposing_node_ids: set[str] = set()
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
        if relation.evidence_ids != canonical_relation_evidence_ids(relation, trail.nodes):
            add(TrailCheckCategory.RELATION_SCHEMA, "RELATION_EVIDENCE_SCOPE_INVALID")
        schema = RELATION_SCHEMAS[relation.relation_type]
        role_pair = (source_node.role, target_node.role)
        if role_pair not in schema.allowed_role_pairs:
            add(TrailCheckCategory.RELATION_SCHEMA, "RELATION_ROLE_PAIR_INVALID")
        if relation.modality not in schema.allowed_modalities:
            add(TrailCheckCategory.RELATION_SCHEMA, "RELATION_MODALITY_INVALID")
        if schema.requires_opposing and TrailNodeRole.OPPOSING not in role_pair:
            add(TrailCheckCategory.RELATION_SCHEMA, "RELATION_REQUIRES_OPPOSING")
        if relation.relation_type is RelationType.CONTRADICTS:
            contradiction_opposing_node_ids.update(
                node.node_id
                for node in (source_node, target_node)
                if node.role is TrailNodeRole.OPPOSING
            )
        if schema.temporal_rule is RelationTemporalRule.SOURCE_BEFORE_TARGET:
            ordering_edges.add((source_node.node_id, target_node.node_id))
            if not _strictly_precedes(source_node, target_node):
                add(TrailCheckCategory.TEMPORAL_ORDER, "TEMPORAL_ORDER_INVALID")
        elif schema.temporal_rule is RelationTemporalRule.TARGET_BEFORE_SOURCE:
            ordering_edges.add((target_node.node_id, source_node.node_id))
            if not _strictly_precedes(target_node, source_node):
                add(TrailCheckCategory.TEMPORAL_ORDER, "TEMPORAL_ORDER_INVALID")
        elif schema.temporal_rule is RelationTemporalRule.SAME_TIME:
            if (
                source_node.temporal_position is None
                or target_node.temporal_position is None
                or source_node.temporal_position != target_node.temporal_position
            ):
                add(TrailCheckCategory.TEMPORAL_ORDER, "SAME_EVENT_TIME_MISMATCH")
        if (
            schema.identity_rule is RelationIdentityRule.SAME_ENTITY
            and source_node.content_hash != target_node.content_hash
        ):
            add(TrailCheckCategory.RELATION_SCHEMA, "SAME_ENTITY_IDENTITY_UNPROVEN")
        if schema.causal:
            causal_relations.append(relation)
            if (
                relation.causal_support
                != required_causal_support(relation, trail.nodes)
            ):
                add(TrailCheckCategory.RELATION_SCHEMA, "CAUSAL_SUPPORT_MISMATCH")
                add(TrailCheckCategory.RELATION_SCHEMA, "CAUSAL_OVERCLAIM")
            if (
                not _strictly_precedes(source_node, target_node)
                or source_node.causal_position is None
                or target_node.causal_position is None
                or source_node.causal_position >= target_node.causal_position
            ):
                add(TrailCheckCategory.RELATION_SCHEMA, "CAUSAL_OVERCLAIM")
        elif relation.causal_support:
            add(TrailCheckCategory.RELATION_SCHEMA, "NONCAUSAL_SUPPORT_FORBIDDEN")

    expected_causal_positions = derive_causal_positions(trail)
    if expected_causal_positions is None:
        add(TrailCheckCategory.RELATION_SCHEMA, "CAUSAL_GRAPH_CYCLE")
    elif any(
        node.causal_position != expected_causal_positions[node.node_id]
        for node in trail.nodes
    ):
        add(TrailCheckCategory.RELATION_SCHEMA, "CAUSAL_POSITION_MISMATCH")
    if version.geometry is not derive_geometry(trail):
        add(TrailCheckCategory.GRAPH_MEMBERSHIP, "GEOMETRY_MISMATCH")

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

    expected_assessment_ids = tuple(
        trusted_assessment_id(version.trail_version_id, category)
        for category in AssessmentCategory
    )
    actual_assessment_ids = tuple(
        assessment.assessment_id for assessment in trail.assessments
    )
    actual_assessment_categories = tuple(
        assessment.category for assessment in trail.assessments
    )
    if _has_duplicates(actual_assessment_ids) or _has_duplicates(version.assessment_ids):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "DUPLICATE_ASSESSMENT_ID")
    if actual_assessment_ids != expected_assessment_ids:
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_ID_NOT_TRUSTED")
    if (
        version.assessment_ids != expected_assessment_ids
        or actual_assessment_ids != version.assessment_ids
    ):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_ID_MISMATCH")
    if actual_assessment_categories != tuple(AssessmentCategory):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_ORDER_MISMATCH")
    if set(actual_assessment_categories) != set(AssessmentCategory):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_CATEGORY_MISMATCH")
    if any(category not in actual_assessment_categories for category in AssessmentCategory):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_CATEGORY_MISSING")
    if len(actual_assessment_categories) != len(set(actual_assessment_categories)):
        add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_CATEGORY_DUPLICATE")

    assessment_actors: list[ActorIdentity] = []
    source_actor_ids = {
        source.evidence.ingestion_actor_id for source in sources_by_id.values()
    }
    stage_actors = (
        ()
        if provenance is None
        else (
            *provenance.source_events,
            provenance.node_event,
            provenance.relation_event,
            provenance.claim_event,
        )
    )
    non_assessment_actors = (
        version.constructed_by,
        *(event.actor for event in stage_actors),
    )
    assessments_by_category = {
        assessment.category: assessment for assessment in trail.assessments
    }
    if version.opposing_node_ids:
        if set(version.opposing_node_ids) != contradiction_opposing_node_ids:
            add(
                TrailCheckCategory.COUNTEREVIDENCE,
                "OPPOSING_WITHOUT_CONTRADICTION",
            )
        counterevidence = assessments_by_category.get(AssessmentCategory.COUNTEREVIDENCE)
        if (
            counterevidence is None
            or counterevidence.provenance.result is not AssessmentOutcome.PASSED
        ):
            add(
                TrailCheckCategory.COUNTEREVIDENCE,
                "COUNTEREVIDENCE_NOT_PASSED",
            )
    for assessment in trail.assessments:
        provenance = assessment.provenance
        if assessment.trail_version_id != version.trail_version_id:
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "CROSS_VERSION_ASSESSMENT")
        if assessment.claim_version_id != version.claim_version_id:
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_CLAIM_MISMATCH")
        if (
            assessment.governing_policy_hash != version.governing_policy_hash
            or provenance.governing_policy_hash != version.governing_policy_hash
        ):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_POLICY_MISMATCH")
        expected_scope = required_assessment_scope(
            assessment.category,
            trail.nodes,
            trail.relations,
        )
        if (
            assessment.node_ids != expected_scope.node_ids
            or assessment.relation_ids != expected_scope.relation_ids
            or assessment.evidence_ids != expected_scope.evidence_ids
            or provenance.evidence_ids != expected_scope.evidence_ids
        ):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_SCOPE_MISMATCH")
        if not assessment.evidence_ids:
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_EVIDENCE_EMPTY")
        if provenance.checks_run != version.check_ids:
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_CHECK_MISMATCH")
        if assessment.finding_codes != tuple(sorted(set(assessment.finding_codes))):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_FINDINGS_NOT_CANONICAL")
        actor = provenance.actor
        configuration = actor.configuration_hash
        if (
            provenance.proposer_relationship is not ActorRelationship.INDEPENDENT
            or actor.actor_id == retained.claim.created_by
            or actor.actor_id in source_actor_ids
            or any(
                not trail_actors_are_independent(actor, authority_actor)
                for authority_actor in non_assessment_actors
            )
            or any(
                not trail_actors_are_independent(actor, prior_actor)
                for prior_actor in assessment_actors
            )
        ):
            add(TrailCheckCategory.ASSESSMENT_AUTHORITY, "ASSESSMENT_NOT_INDEPENDENT")
        assessment_actors.append(actor)
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
    if causal_relations and causal_assessment is not None:
        latest_check_at = max(check.checked_at for check in trail.checks)
        if not (
            latest_check_at < causal_assessment.provenance.assessed_at < version.created_at
        ):
            add(TrailCheckCategory.RELATION_SCHEMA, "STALE_CAUSAL_ASSESSMENT")

    expected_check_ids = tuple(
        trusted_check_id(version.trail_version_id, category)
        for category in TrailCheckCategory
    )
    actual_check_ids = tuple(check.check_id for check in trail.checks)
    actual_check_categories = tuple(check.category for check in trail.checks)
    if _has_duplicates(actual_check_ids) or _has_duplicates(version.check_ids):
        add(TrailCheckCategory.GRAPH_MEMBERSHIP, "DUPLICATE_CHECK_ID")
    if actual_check_ids != expected_check_ids:
        add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_ID_NOT_TRUSTED")
    if version.check_ids != expected_check_ids:
        add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_ID_MISMATCH")
    if actual_check_categories != tuple(TrailCheckCategory):
        add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_ORDER_MISMATCH")
    expected_node_ids = tuple(node.node_id for node in trail.nodes)
    expected_relation_ids = tuple(relation.relation_id for relation in trail.relations)
    expected_evidence_ids = canonical_evidence_ids(trail.nodes)

    for check in trail.checks:
        if check.trail_version_id != version.trail_version_id:
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CROSS_VERSION_CHECK")
        if check.claim_version_id != version.claim_version_id:
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_CLAIM_MISMATCH")
        if check.governing_policy_hash != version.governing_policy_hash:
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_POLICY_MISMATCH")
        if (
            check.node_ids != expected_node_ids
            or check.relation_ids != expected_relation_ids
            or check.evidence_ids != expected_evidence_ids
        ):
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECK_SCOPE_MISMATCH")
        if (
            check.checker_id != TRUSTED_TRAIL_CHECKER_ID
            or check.checker_version != TRUSTED_TRAIL_CHECKER_VERSION
        ):
            add(TrailCheckCategory.GRAPH_MEMBERSHIP, "CHECKER_NOT_TRUSTED")

    for check in trail.checks:
        expected_codes = tuple(sorted(findings[check.category]))
        if check.passed is bool(expected_codes) or check.finding_codes != expected_codes:
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
    outcomes = {
        category: (
            AssessmentOutcome.INCONCLUSIVE
            if assessments_by_category.get(category) is None
            else assessments_by_category[category].provenance.result
        )
        for category in AssessmentCategory
    }
    return semantic_assessment_outcome(outcomes, conflicted=bool(opposing_node_ids))


def _source_first_provenance_matches(
    trail: EvidenceTrailSnapshot,
    retained: TrailValidationInputs,
    sources_by_id: dict[str, RetainedEvidenceSource],
) -> bool:
    version = trail.version
    provenance = version.source_first_provenance
    if tuple(sources_by_id) != version.source_ids:
        return False
    if len(provenance.source_events) != len(version.source_ids):
        return False
    for event, source_id in zip(
        provenance.source_events,
        version.source_ids,
        strict=True,
    ):
        source = sources_by_id.get(source_id)
        if source is None:
            return False
        evidence = source.evidence
        if (
            event.stage is not SourceFirstStageKind.SOURCE_RETAINED
            or event.subject_ids != (source_id, evidence.evidence_id)
            or event.content_hashes != (evidence.artifact.sha256,)
            or event.actor.actor_id != evidence.ingestion_actor_id
            or event.occurred_at != evidence.retrieved_at
            or not _source_first_event_id_matches(event)
        ):
            return False

    node_event = provenance.node_event
    if (
        node_event.stage is not SourceFirstStageKind.NODES_PROPOSED
        or node_event.subject_ids != tuple(node.node_id for node in trail.nodes)
        or node_event.content_hashes != tuple(node.content_hash for node in trail.nodes)
        or node_event.actor != version.constructed_by
        or not _source_first_event_id_matches(node_event)
    ):
        return False
    relation_event = provenance.relation_event
    if (
        relation_event.stage is not SourceFirstStageKind.RELATIONS_PROPOSED
        or relation_event.subject_ids
        != tuple(relation.relation_id for relation in trail.relations)
        or relation_event.content_hashes
        != tuple(relation_content_hash(relation) for relation in trail.relations)
        or relation_event.actor != version.constructed_by
        or not _source_first_event_id_matches(relation_event)
    ):
        return False
    claim_event = provenance.claim_event
    expected_claim_id = f"{retained.claim.claim_id}:{retained.claim.version}"
    if (
        claim_event.stage is not SourceFirstStageKind.CLAIM_FORMED
        or claim_event.subject_ids != (expected_claim_id,)
        or claim_event.content_hashes != (claim_content_hash(retained.claim),)
        or claim_event.actor.actor_id != retained.claim.created_by
        or claim_event.occurred_at != retained.claim.created_at
        or not _source_first_event_id_matches(claim_event)
    ):
        return False

    latest_source = max(event.occurred_at for event in provenance.source_events)
    earliest_check = min(check.checked_at for check in trail.checks)
    latest_check = max(check.checked_at for check in trail.checks)
    earliest_assessment = min(
        assessment.provenance.assessed_at for assessment in trail.assessments
    )
    latest_assessment = max(
        assessment.provenance.assessed_at for assessment in trail.assessments
    )
    return (
        latest_source < node_event.occurred_at
        < relation_event.occurred_at
        < claim_event.occurred_at
        < earliest_check
        and latest_check < earliest_assessment
        and latest_assessment < version.created_at
    )


def _source_first_event_id_matches(event: SourceFirstStageEvent) -> bool:
    return event.event_id == source_first_event_id(
        stage=event.stage,
        subject_ids=event.subject_ids,
        content_hashes=event.content_hashes,
        actor=event.actor,
        occurred_at=event.occurred_at,
    )


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
