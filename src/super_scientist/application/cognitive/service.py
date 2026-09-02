from __future__ import annotations

from typing import Never

from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.kernel.transactions.models import Proposal, TransactionDecision


class CognitiveOrchestrationService:
    """Stateless sealed submission adapter with no retained authority."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("cognitive orchestration service cannot be subclassed")

    def submit(
        self,
        coordinator: TransactionCoordinator,
        proposal: Proposal,
    ) -> TransactionDecision:
        if type(self) is not CognitiveOrchestrationService:
            raise TypeError("submission requires the exact cognitive service")
        if type(coordinator) is not TransactionCoordinator:
            raise TypeError("cognitive service requires the exact transaction coordinator")
        return coordinator.submit(proposal)

    def __copy__(self) -> Never:
        raise TypeError("cognitive orchestration service cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        del memo
        raise TypeError("cognitive orchestration service cannot be copied")


class ResearchCoordinator:
    """Statelessly sequence a declared proposal slice and retain no authority."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("research coordinator cannot be subclassed")

    def run_declared_slice(
        self,
        submitter: CognitiveOrchestrationService,
        coordinator: TransactionCoordinator,
        proposals: tuple[Proposal, ...],
    ) -> tuple[TransactionDecision, ...]:
        if type(self) is not ResearchCoordinator:
            raise TypeError("coordination requires the exact research coordinator")
        if type(submitter) is not CognitiveOrchestrationService:
            raise TypeError("research coordinator requires the sealed cognitive service")
        if type(coordinator) is not TransactionCoordinator:
            raise TypeError("research coordinator requires the exact transaction coordinator")
        if type(proposals) is not tuple:
            raise TypeError("research coordinator requires an exact proposal tuple")
        decisions: list[TransactionDecision] = []
        for proposal in proposals:
            decision = submitter.submit(coordinator, proposal)
            decisions.append(decision)
            if not decision.accepted:
                break
        return tuple(decisions)

    def __copy__(self) -> Never:
        raise TypeError("research coordinator cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        del memo
        raise TypeError("research coordinator cannot be copied")


__all__ = ["CognitiveOrchestrationService", "ResearchCoordinator"]
