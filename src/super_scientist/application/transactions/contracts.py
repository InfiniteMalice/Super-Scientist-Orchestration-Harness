from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from super_scientist.config.models import PolicySnapshot
from super_scientist.kernel.transactions.models import TransactionDecision


class HandlerReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...


class HandlerWriteCapability(Protocol):
    def append_authoritative(self, record: BaseModel) -> None: ...

    def update_projection(self, record: BaseModel) -> None: ...


class ProposalHandler[ProposalT: BaseModel, ContextT: BaseModel](Protocol):
    proposal_type: str

    def build_context(self, proposal: ProposalT, reads: HandlerReadCapability) -> ContextT: ...

    def decide(self, proposal: ProposalT, context: ContextT) -> TransactionDecision: ...

    def project(
        self,
        proposal: ProposalT,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None: ...
