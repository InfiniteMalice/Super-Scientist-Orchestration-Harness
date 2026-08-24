from __future__ import annotations

from collections.abc import Callable
from typing import Never
from weakref import WeakKeyDictionary

from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.kernel.transactions.models import Proposal, TransactionDecision


def _service_owner_methods() -> tuple[
    Callable[..., None],
    Callable[..., TransactionDecision],
]:
    owners: WeakKeyDictionary[object, TransactionCoordinator] = WeakKeyDictionary()

    def initialize(
        instance: CognitiveOrchestrationService,
        coordinator: TransactionCoordinator,
    ) -> None:
        if type(coordinator) is not TransactionCoordinator:
            raise TypeError("cognitive service requires the exact transaction coordinator")
        owners[instance] = coordinator

    def submit(
        instance: CognitiveOrchestrationService,
        proposal: Proposal,
    ) -> TransactionDecision:
        try:
            coordinator = owners[instance]
        except (KeyError, TypeError):
            raise RuntimeError("cognitive submission capability is unavailable") from None
        return coordinator.submit(proposal)

    return initialize, submit


class CognitiveOrchestrationService:
    """Sealed submission capability for governed cognitive proposals."""

    __slots__ = ("__weakref__",)

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("cognitive orchestration service cannot be subclassed")

    __init__, submit = _service_owner_methods()

    def __copy__(self) -> Never:
        raise TypeError("cognitive orchestration service cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        del memo
        raise TypeError("cognitive orchestration service cannot be copied")


def _research_owner_methods() -> tuple[
    Callable[..., None],
    Callable[..., tuple[TransactionDecision, ...]],
]:
    owners: WeakKeyDictionary[object, CognitiveOrchestrationService] = WeakKeyDictionary()

    def initialize(
        instance: ResearchCoordinator,
        submitter: CognitiveOrchestrationService,
    ) -> None:
        if type(submitter) is not CognitiveOrchestrationService:
            raise TypeError("research coordinator requires the sealed submit capability")
        owners[instance] = submitter

    def run_declared_slice(
        instance: ResearchCoordinator,
        proposals: tuple[Proposal, ...],
    ) -> tuple[TransactionDecision, ...]:
        try:
            submitter = owners[instance]
        except (KeyError, TypeError):
            raise RuntimeError("research coordination capability is unavailable") from None
        decisions: list[TransactionDecision] = []
        for proposal in proposals:
            decision = submitter.submit(proposal)
            decisions.append(decision)
            if not decision.accepted:
                break
        return tuple(decisions)

    return initialize, run_declared_slice


class ResearchCoordinator:
    """Sequence a declared proposal slice without admission or storage authority."""

    __slots__ = ("__weakref__",)

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("research coordinator cannot be subclassed")

    __init__, run_declared_slice = _research_owner_methods()

    def __copy__(self) -> Never:
        raise TypeError("research coordinator cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        del memo
        raise TypeError("research coordinator cannot be copied")


del _research_owner_methods, _service_owner_methods

__all__ = ["CognitiveOrchestrationService", "ResearchCoordinator"]
