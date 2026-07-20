from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING

from super_scientist.application.hypothesis_testing.simulators import SimulatorRegistry
from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.hypotheses.models import (
    ExecutableModelSpec,
    ExecutionMode,
    ModelInput,
    SimulationResult,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import ChangeClassification
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    AdmitHypothesis,
    ProposeHypothesisVersion,
    RecordCounterexample,
    RecordSimulationResult,
    RecordVerificationResult,
    RegisterExecutableModel,
    RegisterVerificationMechanism,
    RejectionCode,
    ReviseHypothesis,
    TransactionDecision,
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.coordinator import TransactionCoordinator


FIXED_HYPOTHESIS_CLASSIFICATION = ChangeClassification(
    target=ChangeTarget.RESEARCH_PROCESS,
    loop_closure=LoopClosure.HUMAN_IN_LOOP,
    persistence=PersistenceScope.RUN_LOCAL,
    verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    grounding=ExternalGrounding.CONTROLLED_EXPERIMENT,
    signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
)

type HypothesisMutationProposal = (
    ProposeHypothesisVersion
    | RegisterExecutableModel
    | RegisterVerificationMechanism
    | RecordSimulationResult
    | RecordVerificationResult
    | RecordCounterexample
    | ReviseHypothesis
    | AdmitHypothesis
)


def hypothesis_mutation_authority_rejection(
    proposal: HypothesisMutationProposal,
    snapshot: PolicySnapshot,
    *,
    authority_actors: tuple[ActorIdentity, ...] = (),
) -> TransactionDecision | None:
    if proposal.classification != FIXED_HYPOTHESIS_CLASSIFICATION:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "hypothesis records require the exact fixed classification",
        )
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "hypothesis records require an active governance policy V2",
        )
    requirement = next(
        (
            item
            for item in policy.adaptation_requirements
            if item.change_target is ChangeTarget.RESEARCH_PROCESS
            and item.persistence is PersistenceScope.RUN_LOCAL
        ),
        None,
    )
    if requirement is None:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "active policy does not govern run-local hypothesis records",
        )
    if (
        requirement.minimum_verification is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        or ExternalGrounding.CONTROLLED_EXPERIMENT not in requirement.permitted_grounding
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "active policy does not permit the fixed hypothesis verification and grounding",
        )
    if requirement.protected_evaluation_required or requirement.rollback_required:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "run-local hypothesis stages cannot inherit promotion-only policy flags",
        )
    approval = proposal.approval
    if (
        approval is None
        or requirement.required_approver_kind is not ActorKind.HUMAN
        or approval.approver.kind is not ActorKind.HUMAN
        or not are_independent(proposal.proposer, approval.approver)
        or any(
            not _fully_independent(left, right)
            for left, right in combinations(
                (proposal.proposer, approval.approver, *authority_actors),
                2,
            )
        )
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "hypothesis mutation requires independent human approval and review actors",
        )
    return None


class HypothesisTestingService:
    """Thin facade over bounded simulation and coordinator-owned durable mutations."""

    def __init__(
        self,
        coordinator: TransactionCoordinator,
        registry: SimulatorRegistry | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._registry = registry or SimulatorRegistry()

    def propose(self, proposal: ProposeHypothesisVersion) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def register_model(self, proposal: RegisterExecutableModel) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def register_verification_mechanism(
        self,
        proposal: RegisterVerificationMechanism,
    ) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def record_simulation(self, proposal: RecordSimulationResult) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def record_verification(self, proposal: RecordVerificationResult) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def record_counterexample(self, proposal: RecordCounterexample) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def revise(self, proposal: ReviseHypothesis) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def admit(self, proposal: AdmitHypothesis) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def simulate(
        self,
        model: ExecutableModelSpec,
        model_input: ModelInput,
        *,
        simulation_result_id: str,
        output_id: str,
        governing_policy_hash: str,
    ) -> SimulationResult:
        if model.governing_policy_hash != governing_policy_hash:
            raise ValueError("simulation must name the model's governing policy")
        output = self._registry.execute(model, model_input, output_id=output_id)
        return SimulationResult(
            simulation_result_id=simulation_result_id,
            hypothesis_version_id=model.hypothesis_version_id,
            model_spec_id=model.model_spec_id,
            execution_mode=ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
            model_input=model_input,
            model_output=output,
            deterministic_seed=model.deterministic_seed,
            completed_at=model.created_at,
            governing_policy_hash=governing_policy_hash,
        )


def _fully_independent(left: ActorIdentity, right: ActorIdentity) -> bool:
    if not are_independent(left, right):
        return False
    if left.kind is ActorKind.MODEL and right.kind is ActorKind.MODEL:
        return (
            left.configuration_hash is not None
            and right.configuration_hash is not None
            and left.configuration_hash != right.configuration_hash
        )
    return True


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)


__all__ = [
    "FIXED_HYPOTHESIS_CLASSIFICATION",
    "HypothesisTestingService",
    "hypothesis_mutation_authority_rejection",
]
