from __future__ import annotations

import pytest
from pydantic import ValidationError

from super_scientist.domain.behavioral_rules.consolidation import (
    build_candidate_diff,
    classify_overlap,
    semantic_version_increases,
)
from super_scientist.domain.behavioral_rules.models import (
    ConflictClassification,
    ConsolidationProposal,
    OverlapClassification,
    RecurrenceRepair,
    ReviewerRole,
    RuleAction,
)
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from tests.rule_fixtures import (
    NOW,
    POLICY_HASH,
    actor,
    dispositions,
    five_assessments,
    regression,
    rule,
)


def test_classify_overlap_distinguishes_exact_and_semantic_duplicates() -> None:
    existing = rule(statement="Retain every motivating incident.")
    exact = existing.model_copy(
        update={
            "rule_version_id": "rule-copy-v1",
            "rule_id": "rule-copy",
            "canonical_statement": "  retain EVERY motivating incident.  ",
        }
    )
    semantic = exact.model_copy(
        update={"canonical_statement": "Never discard the event that motivated a rule."}
    )

    assert classify_overlap(exact, existing) is OverlapClassification.EXACT_DUPLICATE
    assert classify_overlap(semantic, existing) is OverlapClassification.SEMANTIC_DUPLICATE


@pytest.mark.parametrize(
    ("candidate_updates", "expected"),
    [
        (
            {"required_behavior": ("use a different action",)},
            OverlapClassification.SAME_TRIGGER_DIFFERENT_ACTION,
        ),
        (
            {"triggers": ("a distinct trigger occurs",)},
            OverlapClassification.DIFFERENT_TRIGGER_SAME_ACTION,
        ),
        (
            {
                "scope": ("behavioral-rule governance", "security review"),
                "triggers": (
                    "a canonical behavior change is proposed",
                    "a security rule changes",
                ),
            },
            OverlapClassification.BROADER_REFORMULATION,
        ),
    ],
)
def test_classify_overlap_has_deterministic_structural_categories(
    candidate_updates: dict[str, tuple[str, ...]],
    expected: OverlapClassification,
) -> None:
    existing = rule()
    candidate = existing.model_copy(
        update={
            "rule_version_id": "rule-2-v1",
            "rule_id": "rule-2",
            "canonical_statement": "Distinct wording for structural comparison.",
            **candidate_updates,
        }
    )

    assert classify_overlap(candidate, existing) is expected


def test_classify_overlap_canonicalizes_conjunctive_action_order() -> None:
    existing = rule(
        required_behavior=("retain the incident", "run the regression"),
        prohibited_behavior=("erase history", "skip verification"),
    )
    reordered = existing.model_copy(
        update={
            "rule_version_id": "rule-2-v1",
            "rule_id": "rule-2",
            "canonical_statement": "Equivalent behavior expressed with reordered conjunctions.",
            "required_behavior": tuple(reversed(existing.required_behavior)),
            "prohibited_behavior": tuple(reversed(existing.prohibited_behavior)),
        }
    )

    assert classify_overlap(reordered, existing) is OverlapClassification.SEMANTIC_DUPLICATE


def test_disjoint_scope_is_non_redundant_even_with_identical_trigger_and_action() -> None:
    existing = rule().model_copy(update={"scope": ("compiler pipelines",)})
    disjoint = existing.model_copy(
        update={
            "rule_version_id": "rule-2-v1",
            "rule_id": "rule-2",
            "canonical_statement": "Retain incident history in clinical workflows.",
            "scope": ("clinical workflows",),
        }
    )

    assert classify_overlap(disjoint, existing) is OverlapClassification.NON_REDUNDANT


@pytest.mark.parametrize(
    ("candidate", "predecessor", "expected"),
    (
        ("1.0.0", "1.0.0-rc.1", True),
        ("1.0.0-rc.2", "1.0.0-rc.1", True),
        ("1.0.0-rc.1", "1.0.0", False),
        ("1.0.0+build.2", "1.0.0+build.1", False),
        ("2.0.0-alpha", "1.9.9", True),
    ),
)
def test_semantic_version_monotonicity_uses_semver_precedence(
    candidate: str,
    predecessor: str,
    expected: bool,
) -> None:
    assert semantic_version_increases(candidate, predecessor) is expected


def test_build_candidate_diff_requires_all_roles_and_preserves_every_finding() -> None:
    assessments = five_assessments()
    integrator = actor("integrator")
    candidate = rule(
        "rule-1-v2",
        semantic_version="1.1.0",
        incidents=("incident-1", "incident-2"),
        creator=integrator,
        supersedes=("rule-1-v1",),
    )

    proposal = build_candidate_diff(
        consolidation_decision_id="decision-1",
        review_proposal_id="proposal-rule-1",
        assessments=assessments,
        candidate_rule=candidate,
        regression_cases=(
            regression("regression-1", "incident-1"),
            regression("regression-2", "incident-2"),
        ),
        action=RuleAction.ACCEPT_WITH_REVISION,
        recommendation_dispositions=dispositions(assessments),
        separating_variable=None,
        recurrence_incident_ids=("incident-2",),
        recurrence_repairs=(RecurrenceRepair.RETRIEVAL, RecurrenceRepair.SCOPE),
        integrator=integrator,
        integrated_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    assert {item.role for item in assessments} == set(ReviewerRole)
    expected_role_order = (
        ReviewerRole.SEMANTIC,
        ReviewerRole.CONFLICT,
        ReviewerRole.ABSTRACTION,
        ReviewerRole.ADVERSARIAL,
        ReviewerRole.VERIFICATION,
    )
    by_role = {item.role: item for item in assessments}
    assert proposal.assessment_ids == tuple(
        by_role[role].assessment_id for role in expected_role_order
    )
    assert len(proposal.preserved_findings) == len(assessments)
    assert all(item.assessment_id in " ".join(proposal.preserved_findings) for item in assessments)
    assert any("assessment-adversarial" in item for item in proposal.preserved_dissent)
    assert {item.accepted for item in proposal.recommendation_dispositions} == {False, True}


def test_build_candidate_diff_rejects_missing_reviewer_role() -> None:
    assessments = five_assessments()[:-1]
    with pytest.raises(ValueError, match="five reviewer roles"):
        build_candidate_diff(
            consolidation_decision_id="decision-1",
            review_proposal_id="proposal-rule-1",
            assessments=assessments,
            candidate_rule=rule("rule-1-v2", semantic_version="1.1.0"),
            regression_cases=(regression("regression-1", "incident-1"),),
            action=RuleAction.ACCEPT_WITH_REVISION,
            recommendation_dispositions=dispositions(assessments),
            separating_variable=None,
            recurrence_incident_ids=(),
            recurrence_repairs=(),
            integrator=actor("integrator"),
            integrated_at=NOW,
            governing_policy_hash=POLICY_HASH,
        )


def test_build_candidate_diff_revalidates_exact_deterministic_review_quality() -> None:
    assessments = list(five_assessments())
    assessments[0] = assessments[0].model_copy(
        update={
            "provenance": assessments[0].provenance.model_copy(
                update={"category": VerificationLevel.SELF_CRITIQUE}
            )
        }
    )
    integrator = actor("integrator")

    with pytest.raises(ValueError, match="independent deterministic passed"):
        build_candidate_diff(
            consolidation_decision_id="decision-1",
            review_proposal_id="proposal-rule-1",
            assessments=tuple(assessments),
            candidate_rule=rule(
                "rule-1-v2",
                semantic_version="1.1.0",
                incidents=("incident-1", "incident-2"),
                creator=integrator,
            ),
            regression_cases=(
                regression("regression-1", "incident-1", creator=integrator),
                regression("regression-2", "incident-2", creator=integrator),
            ),
            action=RuleAction.ACCEPT_WITH_REVISION,
            recommendation_dispositions=dispositions(tuple(assessments)),
            separating_variable=None,
            recurrence_incident_ids=(),
            recurrence_repairs=(),
            integrator=integrator,
            integrated_at=NOW,
            governing_policy_hash=POLICY_HASH,
        )


def test_contradiction_requires_variable_boundary_both_incidents_and_regressions() -> None:
    assessments = five_assessments(conflict=ConflictClassification.TRUE_LOGICAL_CONTRADICTION)
    integrator = actor("integrator")
    candidate = rule(
        "rule-1-v2",
        semantic_version="2.0.0",
        incidents=("incident-1", "incident-2"),
        creator=integrator,
        supersedes=("rule-1-v1",),
        exceptions=(),
        decision_boundary="Apply the newest rule.",
    )
    kwargs = dict(
        consolidation_decision_id="decision-1",
        review_proposal_id="proposal-rule-1",
        assessments=assessments,
        candidate_rule=candidate,
        regression_cases=(regression("regression-1", "incident-1"),),
        action=RuleAction.ACCEPT_WITH_REVISION,
        recommendation_dispositions=dispositions(assessments),
        recurrence_incident_ids=(),
        recurrence_repairs=(),
        integrator=integrator,
        integrated_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    with pytest.raises(ValueError, match="separating variable"):
        build_candidate_diff(separating_variable=None, **kwargs)
    with pytest.raises(ValueError, match="explicit precondition or exception boundary"):
        build_candidate_diff(separating_variable="deployment environment", **kwargs)

    bounded = candidate.model_copy(
        update={
            "exceptions": ("Use the legacy behavior in environment B.",),
            "decision_boundary": "Use behavior A in environment A and behavior B in environment B.",
        }
    )
    with pytest.raises(ValueError, match="separating-boundary"):
        build_candidate_diff(
            separating_variable="deployment environment",
            **(kwargs | {"candidate_rule": bounded}),
        )


def test_contradiction_requires_explicit_declared_separating_boundary_case() -> None:
    assessments = five_assessments(conflict=ConflictClassification.TRUE_LOGICAL_CONTRADICTION)
    integrator = actor("integrator")
    candidate = rule(
        "rule-1-v2",
        semantic_version="2.0.0",
        incidents=("incident-1", "incident-2"),
        creator=integrator,
        supersedes=("rule-1-v1",),
        exceptions=("Use the legacy behavior in environment B.",),
        decision_boundary="Use behavior A in environment A and behavior B in environment B.",
    )
    boundary = regression(
        "regression-boundary",
        "incident-1",
        creator=integrator,
    ).model_copy(update={"incident_ids": ("incident-1", "incident-2")})
    cases = (
        boundary,
        regression("regression-2", "incident-2", creator=integrator),
    )
    kwargs = dict(
        consolidation_decision_id="decision-1",
        review_proposal_id="proposal-rule-1",
        assessments=assessments,
        candidate_rule=candidate,
        regression_cases=cases,
        action=RuleAction.ACCEPT_WITH_REVISION,
        recommendation_dispositions=dispositions(assessments),
        separating_variable="deployment environment",
        recurrence_incident_ids=(),
        recurrence_repairs=(),
        integrator=integrator,
        integrated_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    with pytest.raises(ValueError, match="separating-boundary"):
        build_candidate_diff(**kwargs)

    proposal = build_candidate_diff(
        separating_boundary_test_id=boundary.test_id,
        **kwargs,
    )
    assert proposal.separating_boundary_test_id == boundary.test_id
    serialized = proposal.model_dump(mode="json")
    assert serialized["separating_boundary_test_id"] == boundary.test_id
    without_boundary = dict(serialized)
    del without_boundary["separating_boundary_test_id"]
    assert sha256_hex(canonical_json_bytes(serialized)) != sha256_hex(
        canonical_json_bytes(without_boundary)
    )
    with pytest.raises(ValidationError, match="separating-boundary"):
        ConsolidationProposal.model_validate_json(
            canonical_json_bytes(without_boundary),
            strict=True,
        )


def test_regression_cases_must_be_declared_by_candidate() -> None:
    assessments = five_assessments()
    integrator = actor("integrator")
    undeclared = regression(
        "regression-undeclared",
        "incident-1",
        creator=integrator,
    ).model_copy(update={"test_id": "test-not-declared-by-candidate"})

    with pytest.raises(ValueError, match="declared by the candidate"):
        build_candidate_diff(
            consolidation_decision_id="decision-1",
            review_proposal_id="proposal-rule-1",
            assessments=assessments,
            candidate_rule=rule(
                "rule-1-v2",
                semantic_version="1.1.0",
                incidents=("incident-1", "incident-2"),
                creator=integrator,
            ),
            regression_cases=(
                undeclared,
                regression("regression-2", "incident-2", creator=integrator),
            ),
            action=RuleAction.ACCEPT_WITH_REVISION,
            recommendation_dispositions=dispositions(assessments),
            separating_variable=None,
            recurrence_incident_ids=(),
            recurrence_repairs=(),
            integrator=integrator,
            integrated_at=NOW,
            governing_policy_hash=POLICY_HASH,
        )


def test_recurrence_requires_named_repair_and_retains_prior_incidents() -> None:
    assessments = five_assessments()
    candidate = rule(
        "rule-1-v2",
        semantic_version="1.1.0",
        incidents=("incident-2",),
        supersedes=("rule-1-v1",),
    )

    with pytest.raises(ValueError, match="recurrence repair"):
        build_candidate_diff(
            consolidation_decision_id="decision-1",
            review_proposal_id="proposal-rule-1",
            assessments=assessments,
            candidate_rule=candidate,
            regression_cases=(regression("regression-2", "incident-2"),),
            action=RuleAction.ACCEPT_WITH_REVISION,
            recommendation_dispositions=dispositions(assessments),
            separating_variable=None,
            recurrence_incident_ids=("incident-2",),
            recurrence_repairs=(),
            integrator=actor("integrator"),
            integrated_at=NOW,
            governing_policy_hash=POLICY_HASH,
            prior_incident_ids=("incident-1",),
        )

    with pytest.raises(ValueError, match="must not delete prior incidents"):
        build_candidate_diff(
            consolidation_decision_id="decision-1",
            review_proposal_id="proposal-rule-1",
            assessments=assessments,
            candidate_rule=candidate,
            regression_cases=(regression("regression-2", "incident-2"),),
            action=RuleAction.ACCEPT_WITH_REVISION,
            recommendation_dispositions=dispositions(assessments),
            separating_variable=None,
            recurrence_incident_ids=("incident-2",),
            recurrence_repairs=(RecurrenceRepair.TRIGGER,),
            integrator=actor("integrator"),
            integrated_at=NOW,
            governing_policy_hash=POLICY_HASH,
            prior_incident_ids=("incident-1",),
        )
