from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import Connection

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.application.transactions.adaptation import (
    adaptation_capabilities,
    fixed_adaptation_handlers,
)
from super_scientist.application.transactions.contracts import (
    HandlerReadCapability,
    HandlerWriteCapability,
)
from super_scientist.application.transactions.hypotheses import (
    fixed_hypothesis_handlers,
    hypothesis_capabilities,
)
from super_scientist.application.transactions.progress import (
    fixed_progress_handlers,
    progress_capabilities,
)
from super_scientist.application.transactions.representations import (
    fixed_representation_handlers,
    representation_capabilities,
)
from super_scientist.application.transactions.router import ProposalRouter
from super_scientist.application.transactions.rules import (
    fixed_rule_handlers,
    rule_capabilities,
)
from super_scientist.application.transactions.trails import (
    fixed_trail_handlers,
    trail_capabilities,
)
from super_scientist.application.workspace_integrity import require_workspace_integrity
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.identity import ActorIdentity
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
    AdmitHypothesis,
    AdmitPrimitiveVersion,
    AppendProgressEvent,
    BindReportSentence,
    ConsolidateBehavioralRule,
    DecideCompletion,
    ImportReviewerAssessment,
    InvalidProposal,
    Proposal,
    ProposalAttempt,
    ProposalKind,
    ProposeBehavioralRule,
    ProposeClaim,
    ProposeEvidenceTrailNodes,
    ProposeEvidenceTrailRelations,
    ProposeGovernancePolicyTransition,
    ProposeHypothesisVersion,
    ProposePrimitiveVersion,
    RecordCounterexample,
    RecordEvidenceTrailVersion,
    RecordPrimitiveEvaluation,
    RecordProgressPlan,
    RecordRuleIncident,
    RecordRunBudget,
    RecordRunCheckpoint,
    RecordSimulationResult,
    RecordVerificationResult,
    RegisterExecutableModel,
    RegisterVerificationMechanism,
    RejectionCode,
    ReviseHypothesis,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from super_scientist.providers.storage.repositories import RepositorySet


class Clock(Protocol):
    def now(self) -> UtcTimestamp: ...


type ProposalFactory = Callable[[], object]

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
IDENTIFIER_ADAPTER: TypeAdapter[StableIdentifier] = TypeAdapter(StableIdentifier)
PROPOSAL_KIND_ADAPTER: TypeAdapter[ProposalKind] = TypeAdapter(ProposalKind)


@dataclass(frozen=True)
class _CompatibilityReadCapability:
    repositories: RepositorySet
    active_policy: PolicySnapshot

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy


@dataclass(frozen=True)
class _CompatibilityWriteCapability:
    repositories: RepositorySet

    def append_authoritative(self, record: BaseModel) -> None:
        if isinstance(record, EvidenceRecord):
            self.repositories.evidence.add(record)
            return
        if isinstance(record, AtomicClaim):
            self.repositories.claims.add_version(record)
            return
        raise TypeError(f"unsupported compatibility authoritative record: {type(record)!r}")

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("compatibility handler has no independent projection writes")


class _CompatibilityProposalHandler:
    """Characterized v0.1 evidence and claim admission behind the fixed router."""

    def __init__(self, engine: AdmissionEngine, proposal_type: str) -> None:
        self._engine = engine
        self.proposal_type = proposal_type

    def build_context(
        self,
        proposal: BaseModel,
        reads: HandlerReadCapability,
    ) -> AdmissionContext:
        del proposal
        compatibility_reads = cast(_CompatibilityReadCapability, reads)
        repositories = compatibility_reads.repositories
        return AdmissionContext(
            active_policy=compatibility_reads.policy_snapshot(),
            evidence_by_id={item.evidence_id: item for item in repositories.evidence.list_all()},
            claim_by_id={item.claim_id: item for item in repositories.claims.list_heads()},
            prior_decision_by_idempotency_key={},
        )

    def decide(self, proposal: BaseModel, context: BaseModel) -> TransactionDecision:
        return self._engine.decide(proposal, context)

    def project(
        self,
        proposal: BaseModel,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        if not decision.accepted:
            raise ValueError("rejected proposals cannot be projected")
        if isinstance(proposal, AddEvidence):
            writes.append_authoritative(proposal.evidence)
        elif isinstance(proposal, ProposeClaim):
            writes.append_authoritative(proposal.claim)
        elif isinstance(proposal, TransitionClaim):
            writes.append_authoritative(proposal.next_claim)
        else:
            raise TypeError(f"unsupported compatibility proposal: {type(proposal)!r}")


class TransactionCoordinator:
    """Owns the complete durable transaction and fixed proposal routing boundary."""

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
        compatibility_handlers = tuple(
            (
                proposal_type,
                _CompatibilityProposalHandler(self._engine, proposal_type),
            )
            for proposal_type in ("add_evidence", "propose_claim", "transition_claim")
        )
        adaptation_handlers = tuple(
            (handler.proposal_type, handler) for handler in fixed_adaptation_handlers()
        )
        progress_handlers = tuple(
            (handler.proposal_type, handler) for handler in fixed_progress_handlers()
        )
        trail_handlers = tuple(
            (handler.proposal_type, handler) for handler in fixed_trail_handlers()
        )
        rule_handlers = tuple((handler.proposal_type, handler) for handler in fixed_rule_handlers())
        representation_handlers = tuple(
            (handler.proposal_type, handler) for handler in fixed_representation_handlers()
        )
        hypothesis_handlers = tuple(
            (handler.proposal_type, handler) for handler in fixed_hypothesis_handlers()
        )
        self._router = ProposalRouter(
            (
                *compatibility_handlers,
                *adaptation_handlers,
                *progress_handlers,
                *trail_handlers,
                *rule_handlers,
                *representation_handlers,
                *hypothesis_handlers,
            )
        )

    @property
    def router(self) -> ProposalRouter:
        return self._router

    def submit(self, proposal: object) -> TransactionDecision:
        normalized = _normalize_proposal(proposal)
        if isinstance(normalized, TransactionDecision):
            return normalized
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            require_workspace_integrity(repositories, self._artifact_store)
            connection = _active_connection(uow.connection)
            return self._submit_locked(normalized, repositories, connection)

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
                if (
                    prior.intent_fingerprint == _attempt_fingerprint(attempt)
                    and prior.proposal.proposal_id == attempt.proposal_id
                    and prior.decision.proposal_id == attempt.proposal_id
                ):
                    return prior.decision.model_copy(update={"replayed": True})
                conflict_proposal = _invalid_attempt(
                    attempt,
                    "idempotency key was reused with a different trusted attempt envelope",
                )
                decision = AdmissionEngine.rejected(
                    attempt.proposal_id,
                    RejectionCode.IDEMPOTENCY_CONFLICT,
                    "idempotency key was reused with a different trusted attempt envelope",
                )
                stored_policy = repositories.policies.get_active()
                if stored_policy is not None:
                    self._audit(
                        conflict_proposal,
                        decision,
                        repositories,
                        stored_policy,
                        intent_fingerprint=_attempt_fingerprint(attempt),
                    )
                return decision
            try:
                candidate = proposal_factory()
            except ValidationError as error:
                normalized: Proposal = _invalid_attempt(
                    attempt,
                    _sanitized_validation_message(error),
                )
            except UnicodeError:
                normalized = _invalid_attempt(
                    attempt,
                    "proposal factory input decoding failed",
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
            return self._submit_locked(
                normalized,
                repositories,
                _active_connection(uow.connection),
                intent_fingerprint=_attempt_fingerprint(attempt),
            )

    def _submit_locked(
        self,
        proposal: Proposal,
        repositories: RepositorySet,
        connection: Connection,
        *,
        intent_fingerprint: str | None = None,
    ) -> TransactionDecision:
        proposal_hash = sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json")))
        stored_policy = repositories.policies.get_active()
        prior = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
        if prior is not None:
            if (
                prior.proposal_hash != proposal_hash
                or prior.intent_fingerprint != intent_fingerprint
            ):
                decision = AdmissionEngine.rejected(
                    proposal.proposal_id,
                    RejectionCode.IDEMPOTENCY_CONFLICT,
                    "idempotency key was reused with different proposal content",
                )
                if stored_policy is not None:
                    self._audit(
                        proposal,
                        decision,
                        repositories,
                        stored_policy,
                        intent_fingerprint=intent_fingerprint,
                    )
                return decision
            return prior.decision.model_copy(update={"replayed": True})
        existing_proposal = repositories.transactions.get_by_proposal_id(proposal.proposal_id)
        if stored_policy is None or stored_policy.policy_hash != self._active_policy.policy_hash:
            decision = AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "stored active policy does not match the configured policy snapshot",
            )
            if stored_policy is None:
                return decision
            if existing_proposal is None:
                repositories.transactions.add(
                    proposal,
                    decision,
                    self._clock.now(),
                    intent_fingerprint=intent_fingerprint,
                )
            self._audit(
                proposal,
                decision,
                repositories,
                stored_policy,
                intent_fingerprint=intent_fingerprint,
            )
            return decision
        if existing_proposal is not None:
            decision = AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "proposal id already exists",
            )
            self._audit(
                proposal,
                decision,
                repositories,
                stored_policy,
                intent_fingerprint=intent_fingerprint,
            )
            return decision

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
                repositories.transactions.add(
                    proposal,
                    decision,
                    self._clock.now(),
                    intent_fingerprint=intent_fingerprint,
                )
                self._audit(
                    proposal,
                    decision,
                    repositories,
                    stored_policy,
                    intent_fingerprint=intent_fingerprint,
                )
                return decision

        reads: HandlerReadCapability
        writes: HandlerWriteCapability
        if isinstance(admitted_proposal, InvalidProposal):
            reads = _CompatibilityReadCapability(repositories, stored_policy)
            compatibility_handler = _CompatibilityProposalHandler(
                self._engine,
                "invalid_proposal",
            )
            context = compatibility_handler.build_context(admitted_proposal, reads)
            decision = self._engine.decide(admitted_proposal, context)
        else:
            handler = self._router.resolve(admitted_proposal.proposal_type)
            if isinstance(admitted_proposal, (AddEvidence, ProposeClaim, TransitionClaim)):
                reads = _CompatibilityReadCapability(repositories, stored_policy)
                writes = _CompatibilityWriteCapability(repositories)
            elif isinstance(
                admitted_proposal,
                (
                    RecordProgressPlan,
                    AppendProgressEvent,
                    RecordRunBudget,
                    RecordRunCheckpoint,
                    DecideCompletion,
                ),
            ):
                progress_io = progress_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                )
                reads = progress_io
                writes = progress_io
            elif isinstance(
                admitted_proposal,
                (
                    ProposeEvidenceTrailNodes,
                    ProposeEvidenceTrailRelations,
                    RecordEvidenceTrailVersion,
                    BindReportSentence,
                ),
            ):
                trail_io = trail_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                    self._artifact_store,
                )
                reads = trail_io
                writes = trail_io
            elif isinstance(
                admitted_proposal,
                (
                    RecordRuleIncident,
                    ProposeBehavioralRule,
                    ImportReviewerAssessment,
                    ConsolidateBehavioralRule,
                ),
            ):
                rule_io = rule_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                    self._artifact_store,
                )
                reads = rule_io
                writes = cast(HandlerWriteCapability, rule_io)
            elif isinstance(
                admitted_proposal,
                (
                    ProposePrimitiveVersion,
                    RecordPrimitiveEvaluation,
                    AdmitPrimitiveVersion,
                ),
            ):
                representation_io = representation_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                    self._artifact_store,
                )
                reads = representation_io.reads
                writes = representation_io.writes
            elif isinstance(
                admitted_proposal,
                (
                    ProposeHypothesisVersion,
                    RegisterExecutableModel,
                    RegisterVerificationMechanism,
                    RecordSimulationResult,
                    RecordVerificationResult,
                    RecordCounterexample,
                    ReviseHypothesis,
                    AdmitHypothesis,
                ),
            ):
                hypothesis_io = hypothesis_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                    self._artifact_store,
                )
                reads = hypothesis_io.reads
                writes = hypothesis_io.writes
            else:
                adaptation_io = adaptation_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                )
                reads = adaptation_io
                writes = adaptation_io
            decision = handler.decide(
                admitted_proposal,
                handler.build_context(admitted_proposal, reads),
            )
            if decision.accepted:
                handler.project(
                    admitted_proposal,
                    decision,
                    writes,
                )
        repositories.transactions.add(
            proposal,
            decision,
            self._clock.now(),
            intent_fingerprint=intent_fingerprint,
        )
        self._audit(
            proposal,
            decision,
            repositories,
            stored_policy,
            intent_fingerprint=intent_fingerprint,
        )
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
        *,
        intent_fingerprint: str | None = None,
    ) -> None:
        previous = repositories.audit.last()
        stored_transaction = repositories.transactions.get_by_idempotency_key(
            proposal.idempotency_key
        )
        transaction_persisted = (
            stored_transaction is not None
            and stored_transaction.proposal == proposal
            and stored_transaction.decision == decision
            and stored_transaction.intent_fingerprint == intent_fingerprint
        )
        payload: dict[str, object] = {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "policy_hash": stored_policy.policy_hash,
            "stored_policy_hash": stored_policy.policy_hash,
            "transaction_persisted": transaction_persisted,
        }
        if repositories.policies.get(self._active_policy.policy_hash) is not None:
            payload["configured_policy_hash"] = self._active_policy.policy_hash
        if intent_fingerprint is not None:
            payload["intent_fingerprint"] = intent_fingerprint
        if isinstance(proposal, ProposeGovernancePolicyTransition):
            payload["prior_policy_hash"] = proposal.prior_policy_hash
            payload["candidate_policy_hash"] = proposal.candidate_policy_snapshot.policy_hash
            payload["rollback_policy_hash"] = proposal.rollback_policy_hash
        event = append_event(
            previous,
            "transaction_decision",
            payload,
            self._clock.now(),
        )
        repositories.audit.add(event)


def _normalize_proposal(value: object) -> Proposal | TransactionDecision:
    try:
        if isinstance(
            value,
            (
                RecordRuleIncident,
                ProposeBehavioralRule,
                ImportReviewerAssessment,
                ConsolidateBehavioralRule,
                ProposePrimitiveVersion,
                RecordPrimitiveEvaluation,
                AdmitPrimitiveVersion,
                ProposeHypothesisVersion,
                RegisterExecutableModel,
                RegisterVerificationMechanism,
                RecordSimulationResult,
                RecordVerificationResult,
                RecordCounterexample,
                ReviseHypothesis,
                AdmitHypothesis,
            ),
        ):
            return PROPOSAL_ADAPTER.validate_json(
                canonical_json_bytes(value.model_dump(mode="json"))
            )
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
            proposer=_safe_proposer(value),
            attempted_proposal_kind=_safe_proposal_kind(value),
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


def _attempt_fingerprint(attempt: ProposalAttempt) -> str:
    proposer = attempt.proposer.model_dump(mode="json")
    proposer.pop("created_at")
    return sha256_hex(
        canonical_json_bytes(
            {
                "proposal_id": attempt.proposal_id,
                "idempotency_key": attempt.idempotency_key,
                "proposer": proposer,
                "proposal_kind": attempt.proposal_kind,
                "intent_digest": attempt.intent_digest,
            }
        )
    )


def _sanitized_validation_message(error: ValidationError) -> str:
    del error
    return "proposal factory input validation failed"


def _safe_proposer(value: object) -> ActorIdentity | None:
    candidate = _raw_proposal_field(value, "proposer")
    if candidate is None:
        return None
    try:
        if isinstance(candidate, Mapping):
            return ActorIdentity.model_validate_json(canonical_json_bytes(candidate))
        return ActorIdentity.model_validate(candidate)
    except (TypeError, ValueError):
        return None


def _safe_proposal_kind(value: object) -> ProposalKind | None:
    candidate = _raw_proposal_field(value, "proposal_type")
    try:
        return PROPOSAL_KIND_ADAPTER.validate_python(candidate)
    except (TypeError, ValueError):
        return None


def _raw_proposal_field(value: object, field: str) -> object:
    try:
        if isinstance(value, BaseModel):
            return value.__dict__.get(field)
        if isinstance(value, Mapping):
            return value.get(field)
        return getattr(value, field, None)
    except Exception:
        return None


def _active_connection(connection: Connection | None) -> Connection:
    if connection is None or connection.closed:
        raise RuntimeError("unit of work is not active")
    return connection
