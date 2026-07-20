from __future__ import annotations

from itertools import permutations

import pytest
from pydantic import ValidationError

from super_scientist.domain.behavioral_rules.consolidation import build_candidate_diff
from super_scientist.domain.behavioral_rules.models import RecurrenceRepair, RuleAction
from tests.rule_fixtures import (
    NOW,
    POLICY_HASH,
    actor,
    dispositions,
    five_assessments,
    incident,
    regression,
    rule,
)


def test_candidate_diff_is_stable_across_reviewer_input_order() -> None:
    assessments = five_assessments()
    integrator = actor("integrator")
    candidate = rule(
        "rule-1-v2",
        semantic_version="1.1.0",
        incidents=("incident-1", "incident-2"),
        creator=integrator,
        supersedes=("rule-1-v1",),
    )
    expected = None
    for permuted in tuple(permutations(assessments))[:12]:
        proposal = build_candidate_diff(
            consolidation_decision_id="decision-1",
            review_proposal_id="proposal-rule-1",
            assessments=permuted,
            candidate_rule=candidate,
            regression_cases=(
                regression("regression-1", "incident-1"),
                regression("regression-2", "incident-2"),
            ),
            action=RuleAction.ACCEPT_WITH_REVISION,
            recommendation_dispositions=dispositions(assessments),
            separating_variable=None,
            recurrence_incident_ids=("incident-2",),
            recurrence_repairs=(RecurrenceRepair.ABSTRACTION,),
            integrator=integrator,
            integrated_at=NOW,
            governing_policy_hash=POLICY_HASH,
            prior_incident_ids=("incident-1",),
        )
        expected = proposal if expected is None else expected
        assert proposal == expected


def test_dispositions_follow_canonical_role_order_with_arbitrary_assessment_ids() -> None:
    assessments = tuple(
        item.model_copy(update={"assessment_id": f"review-{6 - index}"})
        for index, item in enumerate(five_assessments(), start=1)
    )
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
            regression("regression-1", "incident-1", creator=integrator),
            regression("regression-2", "incident-2", creator=integrator),
        ),
        action=RuleAction.ACCEPT_WITH_REVISION,
        recommendation_dispositions=dispositions(assessments),
        separating_variable=None,
        recurrence_incident_ids=("incident-2",),
        recurrence_repairs=(RecurrenceRepair.SCOPE,),
        integrator=integrator,
        integrated_at=NOW,
        governing_policy_hash=POLICY_HASH,
        prior_incident_ids=("incident-1",),
    )

    assert (
        tuple(item.assessment_id for item in proposal.recommendation_dispositions)
        == proposal.assessment_ids
    )


def test_rule_history_contracts_are_frozen_and_reject_duplicate_incidents() -> None:
    retained = incident("incident-1")
    with pytest.raises(ValidationError, match="frozen"):
        retained.summary = "mutated"  # type: ignore[misc]

    baseline = rule()
    with pytest.raises(ValidationError, match="source_incident_ids"):
        baseline.__class__.model_validate(
            baseline.model_dump(mode="python")
            | {"source_incident_ids": ("incident-1", "incident-1")}
        )
