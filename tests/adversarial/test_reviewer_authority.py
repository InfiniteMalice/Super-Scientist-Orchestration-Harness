from __future__ import annotations

from dataclasses import fields

import pytest

from super_scientist.application.rules.service import FIXED_RULE_CLASSIFICATION
from super_scientist.application.transactions.rules import (
    ReviewerAssessmentWriter,
    RuleIntegratorCapabilities,
    RuleIntegratorReadFacade,
    RuleIntegratorWriter,
    RuleReviewerImportCapabilities,
    RuleReviewerReadFacade,
)
from super_scientist.domain.behavioral_rules.consolidation import (
    build_candidate_diff,
    rule_actors_are_independent,
)
from super_scientist.domain.behavioral_rules.models import (
    RecurrenceRepair,
    ReviewerRole,
    RuleAction,
)
from super_scientist.domain.cognition import (
    DiversityAssessment,
    DiversityAxisStatus,
    assess_diversity,
)
from super_scientist.domain.identity import ActorKind
from tests.rule_fixtures import (
    NOW,
    POLICY_HASH,
    actor,
    assessment,
    dispositions,
    five_assessments,
    regression,
    rule,
)
from tests.unit.cognition.test_diversity import _cohort, _profile


def test_reviewer_capability_has_no_rule_head_governance_or_quality_writer() -> None:
    names = {item.name for item in fields(RuleReviewerImportCapabilities)}
    assert "head" not in names
    assert "governance" not in names
    assert "quality_registry" not in names
    assert "decisions" not in names
    assert "regressions" not in names
    assert names == {"active_policy", "reader", "writer"}
    assert not names & {"incidents", "rules", "assessments", "transactions"}


def test_only_integrator_capability_can_project_rule_head() -> None:
    reviewer_names = {item.name for item in fields(RuleReviewerImportCapabilities)}
    integrator_names = {item.name for item in fields(RuleIntegratorCapabilities)}
    assert "head" not in reviewer_names
    assert integrator_names == {"active_policy", "reader", "writer"}
    assert "governance" not in integrator_names
    assert "quality_registry" not in integrator_names
    assert not hasattr(ReviewerAssessmentWriter, "set_rule_head")
    assert hasattr(RuleIntegratorWriter, "set_rule_head")


@pytest.mark.parametrize(
    "authority_type",
    (
        RuleReviewerReadFacade,
        ReviewerAssessmentWriter,
        RuleIntegratorReadFacade,
        RuleIntegratorWriter,
    ),
)
def test_nested_rule_authorities_expose_no_generic_repository_mutation(
    authority_type: type,
) -> None:
    public_api = {name for name in dir(authority_type) if not name.startswith("_")}
    assert not public_api & {"add", "update", "set", "delete", "execute"}


@pytest.mark.parametrize(
    "capability_type",
    (RuleReviewerImportCapabilities, RuleIntegratorCapabilities),
)
def test_rule_role_capabilities_have_no_generic_write_escape(
    capability_type: type,
) -> None:
    public_api = {name for name in dir(capability_type) if not name.startswith("_")}
    assert "append_authoritative" not in public_api
    assert "update_projection" not in public_api


def test_dependent_reviewer_roles_cannot_form_a_candidate_diff() -> None:
    shared = actor("shared-reviewer")
    assessments = tuple(assessment(role, reviewer=shared) for role in ReviewerRole)
    with pytest.raises(ValueError, match="independent reviewer actors"):
        build_candidate_diff(
            consolidation_decision_id="decision-1",
            review_proposal_id="proposal-rule-1",
            assessments=assessments,
            candidate_rule=rule("rule-1-v2", semantic_version="1.1.0"),
            regression_cases=(regression("regression-1", "incident-1"),),
            action=RuleAction.ACCEPT_WITH_REVISION,
            recommendation_dispositions=dispositions(assessments),
            separating_variable=None,
            recurrence_incident_ids=("incident-1",),
            recurrence_repairs=(RecurrenceRepair.ENFORCEMENT,),
            integrator=actor("integrator"),
            integrated_at=NOW,
            governing_policy_hash=POLICY_HASH,
        )


def test_shared_model_fingerprint_cannot_alias_an_independent_reviewer() -> None:
    assessments = list(five_assessments())
    first_actor = assessments[0].provenance.actor.model_copy(
        update={"adapter_id": "shared-adapter"}
    )
    alias_actor = assessments[1].provenance.actor.model_copy(
        update={
            "provider_id": first_actor.provider_id,
            "model_id": first_actor.model_id,
            "adapter_id": first_actor.adapter_id,
        }
    )
    assessments[0] = assessments[0].model_copy(
        update={"provenance": assessments[0].provenance.model_copy(update={"actor": first_actor})}
    )
    assessments[1] = assessments[1].model_copy(
        update={"provenance": assessments[1].provenance.model_copy(update={"actor": alias_actor})}
    )

    with pytest.raises(ValueError, match="independent reviewer actors"):
        _build_candidate(tuple(assessments), actor("integrator"))


def test_shared_configuration_cannot_alias_an_independent_reviewer() -> None:
    assessments = list(five_assessments())
    first_actor = assessments[0].provenance.actor
    assert first_actor.configuration_hash is not None
    alias_actor = assessments[1].provenance.actor.model_copy(
        update={"configuration_hash": first_actor.configuration_hash}
    )
    assessments[1] = assessments[1].model_copy(
        update={"provenance": assessments[1].provenance.model_copy(update={"actor": alias_actor})}
    )

    with pytest.raises(ValueError, match="independent reviewer actors"):
        _build_candidate(tuple(assessments), actor("integrator"))


def test_integrator_cannot_alias_a_reviewer_configuration() -> None:
    assessments = five_assessments()
    reviewer = assessments[0].provenance.actor
    assert reviewer.configuration_hash is not None
    integrator = actor("integrator", ActorKind.MODEL).model_copy(
        update={"configuration_hash": reviewer.configuration_hash}
    )

    with pytest.raises(ValueError, match="independent reviewer actors"):
        _build_candidate(assessments, integrator)


@pytest.mark.parametrize(
    "shared_field",
    ("actor_id", "provider_id", "model_id", "adapter_id", "configuration_hash"),
)
def test_rule_reviewer_independence_checks_every_identity_dimension(
    shared_field: str,
) -> None:
    left = actor("left-reviewer", ActorKind.MODEL).model_copy(update={"adapter_id": "left-adapter"})
    right = actor("right-reviewer", ActorKind.MODEL).model_copy(
        update={"adapter_id": "right-adapter"}
    )
    if shared_field == "configuration_hash":
        assert left.configuration_hash is not None
        shared_value = left.configuration_hash
    else:
        shared_value = getattr(left, shared_field)
    right = right.model_copy(update={shared_field: shared_value})

    assert rule_actors_are_independent(left, right) is False


@pytest.mark.parametrize(
    "shared_field",
    ("provider_id", "model_id", "adapter_id", "configuration_hash"),
)
def test_human_and_service_aliases_are_not_treated_as_independent(
    shared_field: str,
) -> None:
    shared_value = POLICY_HASH if shared_field == "configuration_hash" else f"shared-{shared_field}"
    human = actor("human-reviewer").model_copy(update={shared_field: shared_value})
    service = actor("service-reviewer", ActorKind.SERVICE).model_copy(
        update={shared_field: shared_value}
    )

    assert rule_actors_are_independent(human, service) is False


def test_fixed_rule_classification_is_not_a_generic_authority_grant() -> None:
    assert FIXED_RULE_CLASSIFICATION.target.value == "BEHAVIORAL_RULE"
    assert FIXED_RULE_CLASSIFICATION.persistence.value == "PERSISTENT_RULE"
    assert FIXED_RULE_CLASSIFICATION.loop_closure.value == "HUMAN_IN_LOOP"
    assert FIXED_RULE_CLASSIFICATION.verification_level.value == ("INDEPENDENT_DETERMINISTIC_CHECK")
    assert FIXED_RULE_CLASSIFICATION.grounding.value == "PRIMARY_SOURCE"
    assert FIXED_RULE_CLASSIFICATION.signal.value == "EXTRINSIC_GROUNDED_EXPERIENCE"


def test_same_model_prompt_diversity_does_not_grant_reviewer_independence() -> None:
    left = _profile("reviewer-a", prompt_strategy="critique-first")
    right = _profile("reviewer-b", prompt_strategy="direct")

    assessment = assess_diversity(_cohort(left, right), (left, right), ())

    assert assessment.axes.prompt_strategy is DiversityAxisStatus.DIFFERENT
    assert assessment.axes.model_family is DiversityAxisStatus.SAME
    assert rule_actors_are_independent(left.actor, right.actor) is False
    assert "is_independent" not in DiversityAssessment.model_fields
    assert "governance_authority" not in DiversityAssessment.model_fields


def _build_candidate(
    assessments: tuple,
    integrator,
):  # type: ignore[no-untyped-def]
    return build_candidate_diff(
        consolidation_decision_id="decision-1",
        review_proposal_id="proposal-rule-1",
        assessments=assessments,
        candidate_rule=rule(
            "rule-1-v2",
            semantic_version="1.1.0",
            creator=integrator,
        ),
        regression_cases=(
            regression("regression-1", "incident-1", creator=integrator),
            regression("regression-2", "incident-2", creator=integrator),
        ),
        action=RuleAction.ACCEPT_WITH_REVISION,
        recommendation_dispositions=dispositions(assessments),
        separating_variable=None,
        recurrence_incident_ids=("incident-1",),
        recurrence_repairs=(RecurrenceRepair.ENFORCEMENT,),
        integrator=integrator,
        integrated_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
