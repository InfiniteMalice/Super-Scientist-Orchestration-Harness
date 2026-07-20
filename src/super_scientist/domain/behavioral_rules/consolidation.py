from __future__ import annotations

from itertools import combinations

from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ConflictClassification,
    ConsolidationProposal,
    OverlapClassification,
    RecommendationDisposition,
    RecurrenceRepair,
    ReviewerAssessment,
    ReviewerRole,
    RuleAction,
    RuleRegressionCase,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import ActorRelationship, AssessmentOutcome
from super_scientist.domain.primitives import UtcTimestamp

REVIEWER_WORKFLOW_ORDER = (
    ReviewerRole.SEMANTIC,
    ReviewerRole.CONFLICT,
    ReviewerRole.ABSTRACTION,
    ReviewerRole.ADVERSARIAL,
    ReviewerRole.VERIFICATION,
)
_REVIEWER_ORDER_INDEX = {role: index for index, role in enumerate(REVIEWER_WORKFLOW_ORDER)}
CANONICAL_OVERLAP_PRIORITY = (
    OverlapClassification.EXACT_DUPLICATE,
    OverlapClassification.SEMANTIC_DUPLICATE,
    OverlapClassification.NARROWER_INSTANCE,
    OverlapClassification.BROADER_REFORMULATION,
    OverlapClassification.PARTIAL_OVERLAP,
    OverlapClassification.SAME_TRIGGER_DIFFERENT_ACTION,
    OverlapClassification.DIFFERENT_TRIGGER_SAME_ACTION,
    OverlapClassification.NON_REDUNDANT,
)
_CANONICAL_OVERLAP_PRIORITY_INDEX = {
    classification: index for index, classification in enumerate(CANONICAL_OVERLAP_PRIORITY)
}


def classify_overlap(
    candidate: BehavioralRuleVersion,
    existing: BehavioralRuleVersion,
) -> OverlapClassification:
    candidate_exact = _exact_signature(candidate)
    existing_exact = _exact_signature(existing)
    if candidate_exact == existing_exact:
        return OverlapClassification.EXACT_DUPLICATE

    candidate_scope = _normalized_set(candidate.scope)
    existing_scope = _normalized_set(existing.scope)
    candidate_triggers = _normalized_set(candidate.triggers)
    existing_triggers = _normalized_set(existing.triggers)
    candidate_actions = _action_signature(candidate)
    existing_actions = _action_signature(existing)
    if not candidate_scope.intersection(existing_scope):
        return OverlapClassification.NON_REDUNDANT
    if (
        candidate_scope == existing_scope
        and candidate_triggers == existing_triggers
        and candidate_actions == existing_actions
        and _normalized_set(candidate.exceptions) == _normalized_set(existing.exceptions)
        and _normalize(candidate.decision_boundary) == _normalize(existing.decision_boundary)
    ):
        return OverlapClassification.SEMANTIC_DUPLICATE
    if (
        candidate_actions == existing_actions
        and candidate_scope <= existing_scope
        and candidate_triggers <= existing_triggers
        and (candidate_scope != existing_scope or candidate_triggers != existing_triggers)
    ):
        return OverlapClassification.NARROWER_INSTANCE
    if (
        candidate_actions == existing_actions
        and candidate_scope >= existing_scope
        and candidate_triggers >= existing_triggers
        and (candidate_scope != existing_scope or candidate_triggers != existing_triggers)
    ):
        return OverlapClassification.BROADER_REFORMULATION
    if candidate_triggers == existing_triggers and candidate_actions != existing_actions:
        return OverlapClassification.SAME_TRIGGER_DIFFERENT_ACTION
    if candidate_triggers != existing_triggers and candidate_actions == existing_actions:
        return OverlapClassification.DIFFERENT_TRIGGER_SAME_ACTION
    if (
        candidate_scope & existing_scope
        or candidate_triggers & existing_triggers
        or set(candidate_actions) & set(existing_actions)
    ):
        return OverlapClassification.PARTIAL_OVERLAP
    return OverlapClassification.NON_REDUNDANT


def canonical_overlap_classification(
    candidate: BehavioralRuleVersion,
    active_rules: tuple[BehavioralRuleVersion, ...],
) -> OverlapClassification | None:
    """Select one authoritative overlap independent of active-registry iteration order."""

    if not active_rules:
        return None
    classifications = tuple(classify_overlap(candidate, item) for item in active_rules)
    return min(
        classifications,
        key=_CANONICAL_OVERLAP_PRIORITY_INDEX.__getitem__,
    )


def semantic_version_increases(candidate: str, predecessor: str) -> bool:
    """Compare strict SemVer precedence while ignoring build metadata."""

    candidate_core, candidate_prerelease = _semantic_version_parts(candidate)
    predecessor_core, predecessor_prerelease = _semantic_version_parts(predecessor)
    if candidate_core != predecessor_core:
        return candidate_core > predecessor_core
    if candidate_prerelease is None:
        return predecessor_prerelease is not None
    if predecessor_prerelease is None:
        return False
    for candidate_part, predecessor_part in zip(
        candidate_prerelease,
        predecessor_prerelease,
        strict=False,
    ):
        if candidate_part == predecessor_part:
            continue
        candidate_numeric = candidate_part.isdecimal()
        predecessor_numeric = predecessor_part.isdecimal()
        if candidate_numeric and predecessor_numeric:
            return int(candidate_part) > int(predecessor_part)
        if candidate_numeric != predecessor_numeric:
            return not candidate_numeric
        return candidate_part > predecessor_part
    return len(candidate_prerelease) > len(predecessor_prerelease)


def _semantic_version_parts(
    value: str,
) -> tuple[tuple[int, int, int], tuple[str, ...] | None]:
    precedence = value.split("+", 1)[0]
    core_text, separator, prerelease_text = precedence.partition("-")
    major, minor, patch = core_text.split(".")
    prerelease = tuple(prerelease_text.split(".")) if separator else None
    return (int(major), int(minor), int(patch)), prerelease


def build_candidate_diff(
    *,
    consolidation_decision_id: str,
    review_proposal_id: str,
    assessments: tuple[ReviewerAssessment, ...],
    candidate_rule: BehavioralRuleVersion,
    regression_cases: tuple[RuleRegressionCase, ...],
    action: RuleAction,
    recommendation_dispositions: tuple[RecommendationDisposition, ...],
    separating_variable: str | None,
    separating_boundary_test_id: str | None = None,
    recurrence_incident_ids: tuple[str, ...],
    recurrence_repairs: tuple[RecurrenceRepair, ...],
    integrator: ActorIdentity,
    integrated_at: UtcTimestamp,
    governing_policy_hash: str,
    prior_incident_ids: tuple[str, ...] = (),
    overlap: OverlapClassification | None = None,
) -> ConsolidationProposal:
    ordered_assessments = tuple(
        sorted(assessments, key=lambda item: _REVIEWER_ORDER_INDEX[item.role])
    )
    roles = tuple(item.role for item in ordered_assessments)
    if len(ordered_assessments) != len(ReviewerRole) or set(roles) != set(ReviewerRole):
        raise ValueError("candidate diff requires exactly the five reviewer roles")
    if len(set(roles)) != len(roles):
        raise ValueError("candidate diff requires exactly the five reviewer roles")
    if any(item.proposal_id != review_proposal_id for item in ordered_assessments):
        raise ValueError("all assessments must bind the reviewed proposal")
    if any(
        item.provenance.governing_policy_hash != governing_policy_hash
        or item.provenance.proposer_relationship is not ActorRelationship.INDEPENDENT
        for item in ordered_assessments
    ):
        raise ValueError("review assessments must be independent and policy-bound")
    if any(not _has_exact_review_quality(item) for item in ordered_assessments):
        raise ValueError("review assessments require exact independent deterministic passed checks")
    reviewer_actors = tuple(item.provenance.actor for item in ordered_assessments)
    if any(
        not rule_actors_are_independent(left, right)
        for left, right in combinations(reviewer_actors, 2)
    ) or any(not rule_actors_are_independent(integrator, reviewer) for reviewer in reviewer_actors):
        raise ValueError("candidate diff requires independent reviewer actors and integrator")

    assessment_by_id = {item.assessment_id: item for item in ordered_assessments}
    if len(assessment_by_id) != len(ordered_assessments):
        raise ValueError("assessment stable identifiers must be unique")
    disposition_by_id = {item.assessment_id: item for item in recommendation_dispositions}
    if len(disposition_by_id) != len(recommendation_dispositions) or set(disposition_by_id) != set(
        assessment_by_id
    ):
        raise ValueError("every reviewer recommendation requires one explained disposition")
    ordered_dispositions = tuple(
        disposition_by_id[item.assessment_id] for item in ordered_assessments
    )
    if any(
        item.recommended_action is not assessment_by_id[item.assessment_id].recommended_action
        for item in ordered_dispositions
    ):
        raise ValueError("recommendation dispositions must preserve the reviewed action")

    incident_ids = tuple(
        sorted(
            {
                *prior_incident_ids,
                *recurrence_incident_ids,
                *(
                    incident_id
                    for assessment in ordered_assessments
                    for incident_id in assessment.incident_ids
                ),
            }
        )
    )
    if recurrence_incident_ids and not recurrence_repairs:
        raise ValueError(
            "recurrence requires an abstraction, trigger, retrieval, enforcement, "
            "or scope recurrence repair"
        )
    if tuple(candidate_rule.source_incident_ids) != incident_ids:
        if prior_incident_ids and not set(prior_incident_ids).issubset(
            candidate_rule.source_incident_ids
        ):
            raise ValueError("recurrence consolidation must not delete prior incidents")
        raise ValueError("candidate rule must retain every reviewed incident")
    regression_test_ids = tuple(item.test_id for item in regression_cases)
    if len(set(regression_test_ids)) != len(regression_test_ids):
        raise ValueError("regression cases must use unique test identifiers")
    if not set(regression_test_ids).issubset(candidate_rule.regression_test_ids):
        raise ValueError("every regression case test must be declared by the candidate")
    if any(
        not set(item.incident_ids).issubset(candidate_rule.source_incident_ids)
        for item in regression_cases
    ):
        raise ValueError("regression cases must bind retained candidate incidents")
    regression_by_test_id = {item.test_id: item for item in regression_cases}
    conflict = _single_conflict(ordered_assessments)
    if conflict is not None:
        if separating_variable is None or not separating_variable.strip():
            raise ValueError("contradiction resolution requires a separating variable")
        if not candidate_rule.exceptions or not candidate_rule.decision_boundary.strip():
            raise ValueError(
                "contradiction resolution requires an explicit precondition or exception boundary"
            )
        reviewed_incidents = {
            incident_id
            for item in ordered_assessments
            if item.conflict is not None
            for incident_id in item.incident_ids
        }
        boundary_case = (
            None
            if separating_boundary_test_id is None
            else regression_by_test_id.get(separating_boundary_test_id)
        )
        if boundary_case is None:
            raise ValueError("contradiction resolution requires a separating-boundary test")
        if (
            separating_boundary_test_id not in candidate_rule.regression_test_ids
            or len(reviewed_incidents) < 2
            or not reviewed_incidents.issubset(boundary_case.incident_ids)
        ):
            raise ValueError(
                "separating-boundary regression must cover every contradictory incident"
            )
    elif separating_boundary_test_id is not None:
        raise ValueError("non-conflict consolidation cannot name a separating-boundary test")

    if recurrence_incident_ids:
        covered = {incident_id for item in regression_cases for incident_id in item.incident_ids}
        if not set(recurrence_incident_ids).issubset(covered):
            raise ValueError("every recurrence incident requires a retained regression case")

    preserved_findings = tuple(
        f"{item.assessment_id}: {finding}"
        for item in ordered_assessments
        for finding in item.findings
    )
    preserved_dissent = tuple(
        (
            *(
                f"{item.assessment_id}: {uncertainty}"
                for item in ordered_assessments
                for uncertainty in item.uncertainty
            ),
            *(
                f"{item.assessment_id}: rejected {item.recommended_action.value}: "
                f"{item.explanation}"
                for item in ordered_dispositions
                if not item.accepted
            ),
        )
    )
    return ConsolidationProposal(
        consolidation_decision_id=consolidation_decision_id,
        review_proposal_id=review_proposal_id,
        assessment_ids=tuple(item.assessment_id for item in ordered_assessments),
        incident_ids=incident_ids,
        candidate_rule=candidate_rule,
        regression_cases=tuple(sorted(regression_cases, key=lambda item: item.regression_case_id)),
        action=action,
        overlap=overlap,
        conflict=conflict,
        separating_variable=separating_variable,
        separating_boundary_test_id=separating_boundary_test_id,
        recommendation_dispositions=ordered_dispositions,
        preserved_findings=preserved_findings,
        preserved_dissent=preserved_dissent,
        recurrence_incident_ids=tuple(sorted(recurrence_incident_ids)),
        recurrence_repairs=tuple(sorted(recurrence_repairs, key=lambda item: item.value)),
        integrated_by=integrator,
        integrated_at=integrated_at,
        governing_policy_hash=governing_policy_hash,
    )


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _normalized_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_normalize(value) for value in values)


def _normalized_set(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(_normalized_tuple(values))


def _action_signature(rule: BehavioralRuleVersion) -> tuple[str, ...]:
    return (
        *sorted(_normalized_tuple(rule.required_behavior)),
        "--prohibited--",
        *sorted(_normalized_tuple(rule.prohibited_behavior)),
    )


def _exact_signature(rule: BehavioralRuleVersion) -> tuple[object, ...]:
    return (
        _normalize(rule.canonical_statement),
        _normalized_set(rule.scope),
        _normalized_set(rule.triggers),
        _action_signature(rule),
        _normalized_set(rule.exceptions),
        _normalize(rule.decision_boundary),
    )


def _single_conflict(
    assessments: tuple[ReviewerAssessment, ...],
) -> ConflictClassification | None:
    conflicts = tuple(item.conflict for item in assessments if item.conflict is not None)
    if not conflicts:
        return None
    if len(set(conflicts)) != 1:
        raise ValueError("conflict reviewers must preserve conflicting classifications as dissent")
    return conflicts[0]


def rule_actors_are_independent(
    left: ActorIdentity,
    right: ActorIdentity,
) -> bool:
    """Reject actor aliases and correlated model/configuration identities."""

    if not are_independent(left, right):
        return False
    correlated_fields = (
        (left.provider_id, right.provider_id),
        (left.model_id, right.model_id),
        (left.adapter_id, right.adapter_id),
        (left.configuration_hash, right.configuration_hash),
    )
    if any(
        left_value is not None and right_value is not None and left_value == right_value
        for left_value, right_value in correlated_fields
    ):
        return False
    if left.kind is not ActorKind.MODEL or right.kind is not ActorKind.MODEL:
        return True
    return left.configuration_hash is not None and right.configuration_hash is not None


def _has_exact_review_quality(assessment: ReviewerAssessment) -> bool:
    provenance = assessment.provenance
    return bool(
        provenance.category is VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        and provenance.deterministic_or_learned == "DETERMINISTIC"
        and provenance.result is AssessmentOutcome.PASSED
        and provenance.meaningful_confidence is None
        and provenance.checks_run == (f"{assessment.role.value.lower()}-review",)
    )
