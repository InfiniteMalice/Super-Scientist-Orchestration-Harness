from __future__ import annotations

from typing import cast

import pytest

from super_scientist.application.hypothesis_testing.service import (
    FIXED_HYPOTHESIS_CLASSIFICATION,
    HypothesisTestingService,
    hypothesis_mutation_authority_rejection,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV1,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.hypotheses.models import ExecutableModelSpec, ModelInput, NumericField
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.kernel.transactions.models import (
    Approval,
    ProposeHypothesisVersion,
    RejectionCode,
)

from .test_models import HASH, NOW, _actor, valid_hypothesis, valid_model_spec_payload


def _snapshot(policy: GovernancePolicyV1 | GovernancePolicyV2) -> PolicySnapshot:
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _proposal() -> ProposeHypothesisVersion:
    hypothesis = valid_hypothesis()
    return ProposeHypothesisVersion(
        proposal_id="policy-proposal",
        idempotency_key="intent-policy-proposal",
        proposer=hypothesis.proposer,
        approval=Approval(approver=_actor("policy-approver"), approved_at=NOW),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis=hypothesis,
    )


def _requirement(
    *,
    target: ChangeTarget = ChangeTarget.RESEARCH_PROCESS,
    persistence: PersistenceScope = PersistenceScope.RUN_LOCAL,
    verification: VerificationLevel = VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    grounding: frozenset[ExternalGrounding] = frozenset({ExternalGrounding.CONTROLLED_EXPERIMENT}),
    protected: bool = False,
    rollback: bool = False,
) -> AdaptationRequirement:
    return AdaptationRequirement(
        change_target=target,
        persistence=persistence,
        minimum_verification=verification,
        permitted_grounding=grounding,
        required_approver_kind=ActorKind.HUMAN,
        protected_evaluation_required=protected,
        rollback_required=rollback,
    )


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (
            GovernancePolicyV1(required_claim_checks=("source_exists",)),
            RejectionCode.PERMISSION_DENIED,
        ),
        (
            GovernancePolicyV2(
                required_claim_checks=("source_exists",),
                human_approval_for=frozenset(),
                adaptation_requirements=(
                    _requirement(
                        target=ChangeTarget.SKILL,
                        persistence=PersistenceScope.PERSISTENT_SKILL,
                    ),
                ),
            ),
            RejectionCode.PERMISSION_DENIED,
        ),
        (
            GovernancePolicyV2(
                required_claim_checks=("source_exists",),
                human_approval_for=frozenset(),
                adaptation_requirements=(
                    _requirement(verification=VerificationLevel.EXECUTION_FEEDBACK),
                ),
            ),
            RejectionCode.INSUFFICIENT_GROUNDING,
        ),
        (
            GovernancePolicyV2(
                required_claim_checks=("source_exists",),
                human_approval_for=frozenset(),
                adaptation_requirements=(_requirement(protected=True, rollback=True),),
            ),
            RejectionCode.INSUFFICIENT_GROUNDING,
        ),
    ],
)
def test_hypothesis_mutation_requires_exact_v2_policy_authority(
    policy: GovernancePolicyV1 | GovernancePolicyV2,
    expected: RejectionCode,
) -> None:
    decision = hypothesis_mutation_authority_rejection(_proposal(), _snapshot(policy))

    assert decision is not None
    assert decision.reasons[0].code is expected


def test_configuration_aliases_are_not_independent_hypothesis_reviewers() -> None:
    policy = GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset(),
        adaptation_requirements=(_requirement(),),
    )
    proposer = _proposal().proposer
    assert proposer.configuration_hash is not None
    aliased_reviewer = ActorIdentity(
        actor_id="aliased-model-reviewer",
        kind=ActorKind.MODEL,
        provider_id="independent-provider-label",
        model_id="independent-model-label",
        adapter_id="independent-adapter-label",
        configuration_hash=proposer.configuration_hash,
        created_at=NOW,
    )

    decision = hypothesis_mutation_authority_rejection(
        _proposal(),
        _snapshot(policy),
        authority_actors=(aliased_reviewer,),
    )

    assert decision is not None
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


def test_simulation_service_rejects_a_policy_hash_not_bound_to_the_model() -> None:
    model = ExecutableModelSpec.model_validate(valid_model_spec_payload())
    model_input = ModelInput(
        model_input_id="wrong-policy-input",
        schema_id=model.input_schema_id,
        values=(NumericField(name="steps", value=1),),
        deterministic_seed=model.deterministic_seed,
    )
    service = HypothesisTestingService(cast(TransactionCoordinator, object()))

    with pytest.raises(ValueError, match="must name the model's governing policy"):
        service.simulate(
            model,
            model_input,
            simulation_result_id="wrong-policy-result",
            output_id="wrong-policy-output",
            governing_policy_hash="f" * 64 if HASH != "f" * 64 else "e" * 64,
            completed_at=NOW,
        )
