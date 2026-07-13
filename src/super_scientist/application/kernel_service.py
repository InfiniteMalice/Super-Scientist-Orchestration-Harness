from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes, sha256_hex
from super_scientist.kernel.admission.engine import AdmissionContext, AdmissionEngine
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    ProposeClaim,
    RejectionCode,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from super_scientist.providers.storage.repositories import RepositorySet


class Clock(Protocol):
    def now(self) -> UtcTimestamp:
        raise NotImplementedError


class SystemClock:
    def now(self) -> UtcTimestamp:
        return datetime.now(UTC)


class KernelService:
    def __init__(
        self,
        uow_factory: Callable[[], DatabaseUnitOfWork],
        active_policy: PolicySnapshot,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._active_policy = active_policy
        self._clock = clock
        self._engine = AdmissionEngine()

    def submit(self, proposal: Proposal) -> TransactionDecision:
        proposal_hash = sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json")))
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            prior = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
            if prior is not None:
                if prior.proposal_hash != proposal_hash:
                    decision = AdmissionEngine.rejected(
                        proposal.proposal_id,
                        RejectionCode.IDEMPOTENCY_CONFLICT,
                        "idempotency key was reused with different proposal content",
                    )
                    self._audit(proposal, decision, repositories, conflict=True)
                    return decision
                return prior.decision.model_copy(update={"replayed": True})
            context = AdmissionContext(
                active_policy=self._active_policy,
                evidence_by_id={
                    item.evidence_id: item for item in repositories.evidence.list_all()
                },
                claim_by_id={item.claim_id: item for item in repositories.claims.list_heads()},
                prior_decision_by_idempotency_key={},
            )
            decision = self._engine.decide(proposal, context)
            if decision.accepted:
                self._project(proposal, repositories)
            repositories.transactions.add(proposal, decision, self._clock.now())
            self._audit(proposal, decision, repositories)
            return decision

    def _audit(
        self,
        proposal: Proposal,
        decision: TransactionDecision,
        repositories: RepositorySet,
        conflict: bool = False,
    ) -> None:
        previous = repositories.audit.last()
        suffix = f"-{0 if previous is None else previous.sequence + 1}" if conflict else ""
        event = append_event(
            previous,
            f"audit-{proposal.proposal_id}{suffix}",
            "transaction_decision",
            {
                "proposal": proposal.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
                "policy_hash": self._active_policy.policy_hash,
            },
            self._clock.now(),
        )
        repositories.audit.add(event)

    def _project(self, proposal: Proposal, repositories: RepositorySet) -> None:
        if isinstance(proposal, AddEvidence):
            repositories.evidence.add(proposal.evidence)
        elif isinstance(proposal, ProposeClaim):
            repositories.claims.add_version(proposal.claim)
        elif isinstance(proposal, TransitionClaim):
            current = repositories.claims.get_head_required(proposal.claim_id)
            repositories.claims.add_version(
                current.model_copy(
                    update={
                        "version": current.version + 1,
                        "status": proposal.target_status,
                        "parent_version_id": f"{current.claim_id}:{current.version}",
                        "created_at": self._clock.now(),
                        "created_by": proposal.proposer.actor_id,
                    }
                )
            )
