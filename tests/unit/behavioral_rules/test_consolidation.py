from __future__ import annotations

import pytest

from super_scientist.domain.behavioral_rules.consolidation import (
    build_candidate_diff,
    classify_overlap,
)
from super_scientist.domain.behavioral_rules.models import (
    ConflictClassification,
    OverlapClassification,
    RecurrenceRepair,
    ReviewerRole,
    RuleAction,
)
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
    assert proposal.assessment_ids == tuple(
        item.assessment_id for item in sorted(assessments, key=lambda item: item.role.value)
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
    with pytest.raises(ValueError, match="regression case for every contradictory incident"):
        build_candidate_diff(
            separating_variable="deployment environment",
            **(kwargs | {"candidate_rule": bounded}),
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
