from __future__ import annotations

from typing import Never
from weakref import WeakKeyDictionary

from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.kernel.transactions.models import Proposal, TransactionDecision


class _SubmissionToken:
    __slots__ = ("__weakref__",)


_SUBMISSION_REGISTRY: WeakKeyDictionary[_SubmissionToken, TransactionCoordinator] = (
    WeakKeyDictionary()
)


class CognitiveOrchestrationService:
    """Sealed submission capability for governed cognitive proposals."""

    __slots__ = ("_token",)

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("cognitive orchestration service cannot be subclassed")

    def __init__(self, coordinator: TransactionCoordinator) -> None:
        if type(coordinator) is not TransactionCoordinator:
            raise TypeError("cognitive service requires the exact transaction coordinator")
        token = _SubmissionToken()
        _SUBMISSION_REGISTRY[token] = coordinator
        self._token = token

    def submit(self, proposal: Proposal) -> TransactionDecision:
        token = object.__getattribute__(self, "_token")
        try:
            coordinator = _SUBMISSION_REGISTRY[token]
        except (KeyError, TypeError):
            raise RuntimeError("cognitive submission capability is unavailable") from None
        return coordinator.submit(proposal)

    def __copy__(self) -> Never:
        raise TypeError("cognitive orchestration service cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        del memo
        raise TypeError("cognitive orchestration service cannot be copied")


class ResearchCoordinator:
    """Sequence a declared proposal slice without admission or storage authority."""

    __slots__ = ("_submitter",)

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("research coordinator cannot be subclassed")

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

    def __copy__(self) -> Never:
        raise TypeError("research coordinator cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        del memo
        raise TypeError("research coordinator cannot be copied")


__all__ = ["CognitiveOrchestrationService", "ResearchCoordinator"]
