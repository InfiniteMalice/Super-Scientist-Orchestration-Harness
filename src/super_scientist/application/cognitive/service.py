from __future__ import annotations

from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.kernel.transactions.models import Proposal, TransactionDecision


class CognitiveOrchestrationService:
    """Sealed submission capability for governed cognitive proposals."""

    __slots__ = ("_coordinator",)

    def __init__(self, coordinator: TransactionCoordinator) -> None:
        if type(coordinator) is not TransactionCoordinator:
            raise TypeError("cognitive service requires the exact transaction coordinator")
        self._coordinator = coordinator

    def submit(self, proposal: Proposal) -> TransactionDecision:
        return self._coordinator.submit(proposal)


class ResearchCoordinator:
    """Sequence a declared proposal slice without admission or storage authority."""

    __slots__ = ("_submitter",)

    def __init__(self, submitter: CognitiveOrchestrationService) -> None:
        if type(submitter) is not CognitiveOrchestrationService:
            raise TypeError("research coordinator requires the sealed submit capability")
        self._submitter = submitter

    def run_declared_slice(
        self,
        proposals: tuple[Proposal, ...],
    ) -> tuple[TransactionDecision, ...]:
        decisions: list[TransactionDecision] = []
        for proposal in proposals:
            decision = self._submitter.submit(proposal)
            decisions.append(decision)
            if not decision.accepted:
                break
        return tuple(decisions)


__all__ = ["CognitiveOrchestrationService", "ResearchCoordinator"]
