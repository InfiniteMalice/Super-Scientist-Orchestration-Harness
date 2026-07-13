from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.application.workspace_integrity import require_workspace_integrity
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.evidence.models import VerificationState
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
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from super_scientist.providers.storage.repositories import RepositorySet


class Clock(Protocol):
    def now(self) -> UtcTimestamp:
        raise NotImplementedError


class SystemClock:
    def now(self) -> UtcTimestamp:
        return datetime.now(UTC)


type ProposalFactory = Callable[[], Proposal]


class KernelService:
    def __init__(
        self,
        uow_factory: Callable[[], DatabaseUnitOfWork],
        active_policy: PolicySnapshot,
        clock: Clock,
        artifact_store: ArtifactStore,
    ) -> None:
        self._uow_factory = uow_factory
        self._active_policy = active_policy
        self._clock = clock
        self._artifact_store = artifact_store
        self._engine = AdmissionEngine()

    def submit(self, proposal: Proposal) -> TransactionDecision:
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            require_workspace_integrity(repositories, self._artifact_store)
            return self._submit_locked(proposal, repositories)

    def submit_intent(
        self,
        idempotency_key: str,
        proposal_factory: ProposalFactory,
    ) -> TransactionDecision:
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            require_workspace_integrity(repositories, self._artifact_store)
            prior = repositories.transactions.get_by_idempotency_key(idempotency_key)
            if prior is not None:
                return prior.decision.model_copy(update={"replayed": True})
            proposal = proposal_factory()
            if proposal.idempotency_key != idempotency_key:
                raise ValueError("proposal factory returned a different idempotency key")
            return self._submit_locked(proposal, repositories)

    def _submit_locked(
        self,
        proposal: Proposal,
        repositories: RepositorySet,
    ) -> TransactionDecision:
        proposal_hash = sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json")))
        prior = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
        if prior is not None:
            if prior.proposal_hash != proposal_hash:
                decision = AdmissionEngine.rejected(
                    proposal.proposal_id,
                    RejectionCode.IDEMPOTENCY_CONFLICT,
                    "idempotency key was reused with different proposal content",
                )
                self._audit(proposal, decision, repositories)
                return decision
            return prior.decision.model_copy(update={"replayed": True})
        stored_policy = repositories.policies.get_active()
        existing_proposal = repositories.transactions.get_by_proposal_id(proposal.proposal_id)
        if stored_policy is None or stored_policy.policy_hash != self._active_policy.policy_hash:
            decision = AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "stored active policy does not match the configured policy snapshot",
            )
            if existing_proposal is None:
                repositories.transactions.add(proposal, decision, self._clock.now())
            self._audit(proposal, decision, repositories)
            return decision
        if existing_proposal is not None:
            decision = AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "proposal id already exists",
            )
            self._audit(proposal, decision, repositories)
            return decision
        context = AdmissionContext(
            active_policy=stored_policy,
            evidence_by_id={item.evidence_id: item for item in repositories.evidence.list_all()},
            claim_by_id={item.claim_id: item for item in repositories.claims.list_heads()},
            prior_decision_by_idempotency_key={},
        )
        admitted_proposal: Proposal = proposal
        if isinstance(proposal, AddEvidence):
            try:
                admitted_proposal = self._verified_evidence_proposal(proposal)
            except (OSError, UnicodeError, ValueError) as error:
                decision = AdmissionEngine.rejected(
                    proposal.proposal_id,
                    RejectionCode.EVIDENCE_HASH_MISMATCH,
                    f"evidence artifact verification failed: {error}",
                )
                repositories.transactions.add(proposal, decision, self._clock.now())
                self._audit(proposal, decision, repositories)
                return decision
        decision = self._engine.decide(admitted_proposal, context)
        if decision.accepted:
            self._project(admitted_proposal, repositories)
        repositories.transactions.add(proposal, decision, self._clock.now())
        self._audit(proposal, decision, repositories)
        return decision

    def _verified_evidence_proposal(self, proposal: AddEvidence) -> AddEvidence:
        evidence = proposal.evidence
        if evidence.verification_state is not VerificationState.UNVERIFIED:
            raise ValueError("submitted evidence must be unverified")
        verify_artifact_binding(evidence, self._artifact_store)
        verified = evidence.model_copy(
            update={"verification_state": VerificationState.HASH_VERIFIED}
        )
        return proposal.model_copy(update={"evidence": verified})

    def _audit(
        self,
        proposal: Proposal,
        decision: TransactionDecision,
        repositories: RepositorySet,
    ) -> None:
        previous = repositories.audit.last()
        event = append_event(
            previous,
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
            repositories.claims.add_version(proposal.next_claim)
