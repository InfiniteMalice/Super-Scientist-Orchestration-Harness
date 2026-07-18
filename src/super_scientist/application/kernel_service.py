from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from super_scientist.application.transactions.coordinator import (
    TransactionCoordinator,
    _attempt_fingerprint,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.primitives import UtcTimestamp
from super_scientist.kernel.transactions.models import ProposalAttempt, TransactionDecision
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.database import DatabaseUnitOfWork

__all__ = ["Clock", "KernelService", "ProposalFactory", "SystemClock", "_attempt_fingerprint"]


class Clock(Protocol):
    def now(self) -> UtcTimestamp:
        raise NotImplementedError


class SystemClock:
    def now(self) -> UtcTimestamp:
        return datetime.now(UTC)


type ProposalFactory = Callable[[], object]


class KernelService:
    """Backward-compatible facade over the shared transaction coordinator."""

    def __init__(
        self,
        uow_factory: Callable[[], DatabaseUnitOfWork],
        active_policy: PolicySnapshot,
        clock: Clock,
        artifact_store: ArtifactStore,
    ) -> None:
        self._coordinator = TransactionCoordinator(
            uow_factory,
            active_policy,
            clock,
            artifact_store,
        )

    @property
    def coordinator(self) -> TransactionCoordinator:
        return self._coordinator

    def submit(self, proposal: object) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def submit_intent(
        self,
        attempt: ProposalAttempt,
        proposal_factory: ProposalFactory,
    ) -> TransactionDecision:
        return self._coordinator.submit_intent(attempt, proposal_factory)
