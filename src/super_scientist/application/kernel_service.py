from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, TypeAdapter, ValidationError

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.application.workspace_integrity import require_workspace_integrity
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.evidence.models import VerificationState
from super_scientist.domain.primitives import (
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.kernel.admission.engine import AdmissionContext, AdmissionEngine
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    InvalidProposal,
    Proposal,
    ProposalAttempt,
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


type ProposalFactory = Callable[[], object]

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
IDENTIFIER_ADAPTER: TypeAdapter[StableIdentifier] = TypeAdapter(StableIdentifier)


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

    def submit(self, proposal: object) -> TransactionDecision:
        normalized = _normalize_proposal(proposal)
        if isinstance(normalized, TransactionDecision):
            return normalized
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            require_workspace_integrity(repositories, self._artifact_store)
            return self._submit_locked(normalized, repositories)

    def submit_intent(
        self,
        attempt: ProposalAttempt,
        proposal_factory: ProposalFactory,
    ) -> TransactionDecision:
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            require_workspace_integrity(repositories, self._artifact_store)
            prior = repositories.transactions.get_by_idempotency_key(attempt.idempotency_key)
            if prior is not None:
                return prior.decision.model_copy(update={"replayed": True})
            try:
                candidate = proposal_factory()
            except (UnicodeError, ValidationError) as error:
                normalized: Proposal = _invalid_attempt(
                    attempt,
                    f"proposal factory input validation failed: {error}",
                )
            else:
                normalized_result = _normalize_proposal(candidate)
                if isinstance(normalized_result, TransactionDecision):
                    normalized = _invalid_attempt(
                        attempt,
                        "proposal factory returned a malformed proposal",
                    )
                elif not _matches_attempt(normalized_result, attempt):
                    normalized = _invalid_attempt(
                        attempt,
                        "proposal factory result did not match the trusted attempt envelope",
                    )
                else:
                    normalized = normalized_result
            return self._submit_locked(normalized, repositories)

    def _submit_locked(
        self,
        proposal: Proposal,
        repositories: RepositorySet,
    ) -> TransactionDecision:
        proposal_hash = sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json")))
        stored_policy = repositories.policies.get_active()
        prior = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
        if prior is not None:
            if prior.proposal_hash != proposal_hash:
                decision = AdmissionEngine.rejected(
                    proposal.proposal_id,
                    RejectionCode.IDEMPOTENCY_CONFLICT,
                    "idempotency key was reused with different proposal content",
                )
                if stored_policy is not None:
                    self._audit(proposal, decision, repositories, stored_policy)
                return decision
            return prior.decision.model_copy(update={"replayed": True})
        existing_proposal = repositories.transactions.get_by_proposal_id(proposal.proposal_id)
        if stored_policy is None or stored_policy.policy_hash != self._active_policy.policy_hash:
            decision = AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "stored active policy does not match the configured policy snapshot",
            )
            configured_policy = repositories.policies.get(self._active_policy.policy_hash)
            if stored_policy is None or configured_policy is None:
                return decision
            if existing_proposal is None:
                repositories.transactions.add(proposal, decision, self._clock.now())
            self._audit(proposal, decision, repositories, stored_policy)
            return decision
        if existing_proposal is not None:
            decision = AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "proposal id already exists",
            )
            self._audit(proposal, decision, repositories, stored_policy)
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
                self._audit(proposal, decision, repositories, stored_policy)
                return decision
        decision = self._engine.decide(admitted_proposal, context)
        if decision.accepted:
            self._project(admitted_proposal, repositories)
        repositories.transactions.add(proposal, decision, self._clock.now())
        self._audit(proposal, decision, repositories, stored_policy)
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
        stored_policy: PolicySnapshot,
    ) -> None:
        previous = repositories.audit.last()
        payload: dict[str, object] = {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "policy_hash": stored_policy.policy_hash,
            "stored_policy_hash": stored_policy.policy_hash,
        }
        if repositories.policies.get(self._active_policy.policy_hash) is not None:
            payload["configured_policy_hash"] = self._active_policy.policy_hash
        event = append_event(
            previous,
            "transaction_decision",
            payload,
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


def _normalize_proposal(value: object) -> Proposal | TransactionDecision:
    try:
        if isinstance(value, BaseModel):
            return PROPOSAL_ADAPTER.validate_python(dict(value.__dict__))
        if isinstance(value, Mapping):
            return PROPOSAL_ADAPTER.validate_json(canonical_json_bytes(value))
        return PROPOSAL_ADAPTER.validate_python(value)
    except (TypeError, ValueError):
        proposal_id = _safe_proposal_field(value, "proposal_id")
        idempotency_key = _safe_proposal_field(value, "idempotency_key")
        if proposal_id is None or idempotency_key is None:
            return _invalid_proposal_decision(proposal_id or "invalid-proposal")
        return InvalidProposal(
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            validation_error="proposal failed service boundary validation",
        )


def _safe_proposal_field(value: object, field: str) -> str | None:
    try:
        if isinstance(value, BaseModel):
            candidate = value.__dict__.get(field)
        elif isinstance(value, Mapping):
            candidate = value.get(field)
        else:
            candidate = getattr(value, field, None)
    except Exception:
        return None
    return _safe_identifier(candidate)


def _safe_identifier(value: object) -> str | None:
    try:
        return IDENTIFIER_ADAPTER.validate_python(value)
    except Exception:
        return None


def _invalid_proposal_decision(proposal_id: str) -> TransactionDecision:
    return AdmissionEngine.rejected(
        proposal_id,
        RejectionCode.INVALID_PROPOSAL,
        "proposal failed service boundary validation and could not be stored",
    )


def _matches_attempt(proposal: Proposal, attempt: ProposalAttempt) -> bool:
    return (
        not isinstance(proposal, InvalidProposal)
        and proposal.proposal_id == attempt.proposal_id
        and proposal.idempotency_key == attempt.idempotency_key
        and proposal.proposer == attempt.proposer
        and proposal.proposal_type == attempt.proposal_kind
    )


def _invalid_attempt(attempt: ProposalAttempt, message: str) -> InvalidProposal:
    return InvalidProposal(
        proposal_id=attempt.proposal_id,
        idempotency_key=attempt.idempotency_key,
        validation_error=message,
        proposer=attempt.proposer,
        attempted_proposal_kind=attempt.proposal_kind,
    )
