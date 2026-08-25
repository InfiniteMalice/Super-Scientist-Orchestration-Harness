from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast, get_args

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import Connection

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.application.harness_eval import fixed_harness_extension_handlers
from super_scientist.application.harness_eval.service import fixed_harness_eval_handlers
from super_scientist.application.transactions.adaptation import (
    adaptation_capabilities,
    fixed_adaptation_handlers,
)
from super_scientist.application.transactions.cognition import (
    cognition_capabilities,
    fixed_cognition_handlers,
)
from super_scientist.application.transactions.collaboration import (
    collaboration_capabilities,
    fixed_collaboration_handlers,
)
from super_scientist.application.transactions.contracts import (
    HandlerReadCapability,
    HandlerWriteCapability,
)
from super_scientist.application.transactions.harness_eval import harness_eval_capabilities
from super_scientist.application.transactions.harness_extensions import (
    harness_extension_capabilities,
)
from super_scientist.application.transactions.hypotheses import (
    fixed_hypothesis_handlers,
    hypothesis_capabilities,
)
from super_scientist.application.transactions.procedures import (
    fixed_procedure_handlers,
    procedure_capabilities,
)
from super_scientist.application.transactions.progress import (
    fixed_progress_handlers,
    progress_capabilities,
)
from super_scientist.application.transactions.representations import (
    fixed_representation_handlers,
    representation_capabilities,
)
from super_scientist.application.transactions.router import ProposalRouter, RoutedHandler
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
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.kernel.admission.engine import AdmissionContext, AdmissionEngine
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    GOVERNED_PROPOSAL_CLASSES,
    MAX_PROPOSAL_BYTES,
    MAX_PROPOSAL_JSON_CONTAINER_ITEMS,
    MAX_PROPOSAL_JSON_DEPTH,
    MAX_PROPOSAL_JSON_NODES,
    AddEvidence,
    AdmitHypothesis,
    AdmitPrimitiveVersion,
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    AppendPeerContribution,
    AppendPeerRequest,
    AppendProgressEvent,
    AppendTopologyEvent,
    BindCompiledProgressPlan,
    BindReportSentence,
    ConsolidateBehavioralRule,
    CreateHarnessCampaign,
    DecideCompletion,
    DecideHarnessCampaign,
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
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    RecordCounterexample,
    RecordDiversityAssessment,
    RecordEvidenceTrailVersion,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessConfound,
    RecordHarnessExecutionTrace,
    RecordHarnessIteration,
    RecordHarnessProtectedResult,
    RecordMethodDirectionOutcome,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordPrimitiveEvaluation,
    RecordProcedureCompilation,
    RecordProgressPlan,
    RecordRewardAssessment,
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
    _fresh_actor_identity,
    _governed_proposal_state_is_safe,
    expected_hash_verified_evidence,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from super_scientist.providers.storage.procedure_sources import (
    procedure_source_snapshot_audit_metadata_from_store,
)
from super_scientist.providers.storage.repositories import RepositorySet


class Clock(Protocol):
    def now(self) -> UtcTimestamp: ...


type ProposalFactory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class RetainedIntentProposalFactory:
    """Replay one canonical proposal with its retained source intent identity."""

    proposal: Proposal
    proposal_hash: Sha256Hex
    intent_fingerprint: Sha256Hex | None

    def __call__(self) -> Proposal:
        return cast(Proposal, object.__getattribute__(self, "proposal"))


PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
IDENTIFIER_ADAPTER: TypeAdapter[StableIdentifier] = TypeAdapter(StableIdentifier)
PROPOSAL_KIND_ADAPTER: TypeAdapter[ProposalKind] = TypeAdapter(ProposalKind)
_PROPOSAL_EXACT_TYPES = frozenset(get_args(get_args(Proposal)[0]))


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
        harness_eval_handlers = tuple(
            (handler.proposal_type, handler) for handler in fixed_harness_eval_handlers()
        )
        cognitive_handlers: tuple[tuple[str, RoutedHandler], ...] = tuple(
            (handler.proposal_type, cast(RoutedHandler, handler))
            for handler in (
                *fixed_cognition_handlers(),
                *fixed_collaboration_handlers(),
                *fixed_procedure_handlers(),
                *fixed_harness_extension_handlers(),
            )
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
                *harness_eval_handlers,
                *cognitive_handlers,
            )
        )

    @property
    def router(self) -> ProposalRouter:
        return self._router

    def submit(self, proposal: object) -> TransactionDecision:
        normalized = _normalize_proposal(proposal)
        if type(normalized) is TransactionDecision:
            return normalized
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            require_workspace_integrity(repositories, self._artifact_store)
            connection = _active_connection(uow.connection)
            return self._submit_locked(cast(Proposal, normalized), repositories, connection)

    def submit_batch(
        self,
        proposals: tuple[object, ...],
    ) -> tuple[TransactionDecision, ...]:
        """Atomically submit proposals in order after one starting integrity check."""
        if not proposals:
            return ()
        normalized = tuple(_normalize_proposal(proposal) for proposal in proposals)
        decisions: list[TransactionDecision] = []
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            require_workspace_integrity(repositories, self._artifact_store)
            connection = _active_connection(uow.connection)
            for proposal in normalized:
                if type(proposal) is TransactionDecision:
                    decisions.append(proposal)
                else:
                    decisions.append(
                        self._submit_locked(cast(Proposal, proposal), repositories, connection)
                    )
        return tuple(decisions)

    def submit_intent(
        self,
        attempt: ProposalAttempt,
        proposal_factory: ProposalFactory,
    ) -> TransactionDecision:
        intent_fingerprint = (
            object.__getattribute__(proposal_factory, "intent_fingerprint")
            if type(proposal_factory) is RetainedIntentProposalFactory
            else _attempt_fingerprint(attempt)
        )
        retained_proposal_hash = (
            object.__getattribute__(proposal_factory, "proposal_hash")
            if type(proposal_factory) is RetainedIntentProposalFactory
            else None
        )
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            require_workspace_integrity(repositories, self._artifact_store)
            prior = repositories.transactions.get_by_idempotency_key(attempt.idempotency_key)
            if prior is not None:
                if (
                    prior.intent_fingerprint == intent_fingerprint
                    and (
                        retained_proposal_hash is None
                        or prior.proposal_hash == retained_proposal_hash
                    )
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
                        intent_fingerprint=intent_fingerprint,
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
                if type(normalized_result) is TransactionDecision:
                    normalized = _invalid_attempt(
                        attempt,
                        "proposal factory returned a malformed proposal",
                    )
                elif not _matches_attempt(cast(Proposal, normalized_result), attempt):
                    normalized = _invalid_attempt(
                        attempt,
                        "proposal factory result did not match the trusted attempt envelope",
                    )
                else:
                    normalized = cast(Proposal, normalized_result)
            return self._submit_locked(
                normalized,
                repositories,
                _active_connection(uow.connection),
                intent_fingerprint=intent_fingerprint,
            )

    def _submit_locked(
        self,
        proposal: Proposal,
        repositories: RepositorySet,
        connection: Connection,
        *,
        intent_fingerprint: str | None = None,
    ) -> TransactionDecision:
        governed_type = _trusted_governed_proposal_type(proposal)
        governed_state_is_safe = governed_type is None or _governed_proposal_state_is_safe(
            proposal,
            governed_type,
        )
        stored_proposal = _durable_proposal(
            proposal,
            governed_type,
            state_is_safe=governed_state_is_safe,
        )
        invalid_governed_type = (
            governed_type
            if governed_type is not None
            and (type(proposal) is not governed_type or not governed_state_is_safe)
            else None
        )
        if governed_type is None and type(stored_proposal) is InvalidProposal:
            invalid_governed_type = _attempted_governed_proposal_type(stored_proposal)
        if invalid_governed_type is not None:
            governed_type = None
        proposal_hash = sha256_hex(
            canonical_json_bytes(BaseModel.model_dump(stored_proposal, mode="json"))
        )
        stored_policy = repositories.policies.get_active()
        prior = repositories.transactions.get_by_idempotency_key(stored_proposal.idempotency_key)
        if prior is not None:
            if (
                prior.proposal_hash != proposal_hash
                or prior.intent_fingerprint != intent_fingerprint
            ):
                decision = AdmissionEngine.rejected(
                    stored_proposal.proposal_id,
                    RejectionCode.IDEMPOTENCY_CONFLICT,
                    "idempotency key was reused with different proposal content",
                )
                if stored_policy is not None:
                    self._audit(
                        stored_proposal,
                        decision,
                        repositories,
                        stored_policy,
                        intent_fingerprint=intent_fingerprint,
                    )
                return decision
            return prior.decision.model_copy(update={"replayed": True})
        existing_proposal = repositories.transactions.get_by_proposal_id(
            stored_proposal.proposal_id
        )
        if stored_policy is None or stored_policy.policy_hash != self._active_policy.policy_hash:
            decision = AdmissionEngine.rejected(
                stored_proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "stored active policy does not match the configured policy snapshot",
            )
            if stored_policy is None:
                return decision
            if existing_proposal is None:
                repositories.transactions.add(
                    stored_proposal,
                    decision,
                    self._clock.now(),
                    intent_fingerprint=intent_fingerprint,
                )
            self._audit(
                stored_proposal,
                decision,
                repositories,
                stored_policy,
                intent_fingerprint=intent_fingerprint,
            )
            return decision
        if existing_proposal is not None:
            decision = AdmissionEngine.rejected(
                stored_proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "proposal id already exists",
            )
            self._audit(
                stored_proposal,
                decision,
                repositories,
                stored_policy,
                intent_fingerprint=intent_fingerprint,
            )
            return decision

        admitted_proposal: Proposal = proposal
        if governed_type is None and isinstance(proposal, AddEvidence):
            try:
                admitted_proposal = self._verified_evidence_proposal(proposal)
            except (OSError, UnicodeError, ValueError) as error:
                decision = AdmissionEngine.rejected(
                    stored_proposal.proposal_id,
                    RejectionCode.EVIDENCE_HASH_MISMATCH,
                    f"evidence artifact verification failed: {error}",
                )
                repositories.transactions.add(
                    stored_proposal,
                    decision,
                    self._clock.now(),
                    intent_fingerprint=intent_fingerprint,
                )
                self._audit(
                    stored_proposal,
                    decision,
                    repositories,
                    stored_policy,
                    intent_fingerprint=intent_fingerprint,
                )
                return decision

        transaction_created_at = self._clock.now()
        reads: HandlerReadCapability
        writes: HandlerWriteCapability
        if invalid_governed_type is not None:
            reads = _CompatibilityReadCapability(repositories, stored_policy)
            decision = _governed_handler_failure_decision(
                stored_proposal.proposal_id,
                invalid_governed_type,
            )
        elif governed_type is None and isinstance(admitted_proposal, InvalidProposal):
            reads = _CompatibilityReadCapability(repositories, stored_policy)
            compatibility_handler = _CompatibilityProposalHandler(
                self._engine,
                "invalid_proposal",
            )
            context = compatibility_handler.build_context(admitted_proposal, reads)
            decision = self._engine.decide(admitted_proposal, context)
        else:
            route_type = (
                admitted_proposal.proposal_type
                if governed_type is None
                else cast(str, governed_type.model_fields["proposal_type"].default)
            )
            handler = self._router.resolve(route_type)
            if governed_type is None and isinstance(
                admitted_proposal,
                (AddEvidence, ProposeClaim, TransitionClaim),
            ):
                reads = _CompatibilityReadCapability(repositories, stored_policy)
                writes = _CompatibilityWriteCapability(repositories)
            elif governed_type is None and isinstance(
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
            elif governed_type is None and isinstance(
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
            elif governed_type is None and isinstance(
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
            elif governed_type is None and isinstance(
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
            elif governed_type is None and isinstance(
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
                    current_transaction_created_at=transaction_created_at,
                )
                reads = hypothesis_io.reads
                writes = hypothesis_io.writes
            elif governed_type is None and isinstance(
                admitted_proposal,
                (
                    CreateHarnessCampaign,
                    RecordHarnessIteration,
                    RecordHarnessProtectedResult,
                    RecordHarnessConfound,
                    DecideHarnessCampaign,
                ),
            ):
                harness_eval_io = harness_eval_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                )
                reads = harness_eval_io
                writes = harness_eval_io
            elif governed_type in (
                RecordCapabilityProfile,
                RecordCohortPlan,
                RecordDiversityAssessment,
            ):
                cognition_io = cognition_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                    current_transaction_created_at=transaction_created_at,
                )
                reads = cognition_io
                writes = cognition_io
            elif governed_type in (
                RecordCollaborationSession,
                AppendPeerRequest,
                AppendPeerContribution,
                AppendTopologyEvent,
                RecordCollaborationTermination,
            ):
                collaboration_io = collaboration_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                    current_transaction_created_at=transaction_created_at,
                )
                reads = collaboration_io
                writes = collaboration_io
            elif governed_type in (
                RecordProcedureCompilation,
                RecordMethodDirectionOutcome,
                BindCompiledProgressPlan,
            ):
                procedure_io = procedure_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                    self._artifact_store,
                    current_transaction_created_at=transaction_created_at,
                )
                reads = procedure_io
                writes = procedure_io
            elif governed_type in (
                RecordGuidanceEvaluationProtocol,
                AppendGuidanceEvaluationCell,
                RecordModelHarnessProtocol,
                AppendModelHarnessCell,
                RecordModelHarnessAnalysis,
                RecordHarnessExecutionTrace,
                RecordRewardAssessment,
            ):
                harness_extension_io = harness_extension_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                    current_transaction_created_at=transaction_created_at,
                )
                reads = harness_extension_io
                writes = harness_extension_io
            else:
                adaptation_io = adaptation_capabilities(
                    admitted_proposal,
                    connection,
                    stored_policy,
                )
                reads = adaptation_io
                writes = adaptation_io
            try:
                decision = handler.decide(
                    admitted_proposal,
                    handler.build_context(admitted_proposal, reads),
                )
            except (
                ArithmeticError,
                AttributeError,
                MemoryError,
                OverflowError,
                RecursionError,
                TypeError,
                UnicodeError,
                ValueError,
            ):
                if governed_type is None:
                    raise
                decision = _governed_handler_failure_decision(
                    stored_proposal.proposal_id,
                    governed_type,
                )
            if (
                decision.accepted
                and governed_type is not None
                and type(stored_proposal) is InvalidProposal
            ):
                decision = _governed_handler_failure_decision(
                    stored_proposal.proposal_id,
                    governed_type,
                )
            elif decision.accepted:
                handler.project(
                    admitted_proposal,
                    decision,
                    writes,
                )
        repositories.transactions.add(
            stored_proposal,
            decision,
            transaction_created_at,
            intent_fingerprint=intent_fingerprint,
        )
        self._audit(
            stored_proposal,
            decision,
            repositories,
            stored_policy,
            intent_fingerprint=intent_fingerprint,
        )
        return decision

    def _verified_evidence_proposal(self, proposal: AddEvidence) -> AddEvidence:
        verify_artifact_binding(proposal.evidence, self._artifact_store)
        verified = expected_hash_verified_evidence(proposal)
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
        if (
            type(proposal) is AddEvidence
            and decision.accepted
            and transaction_persisted
            and proposal.evidence.evidence_type == "procedure-source"
        ):
            try:
                snapshot_metadata = procedure_source_snapshot_audit_metadata_from_store(
                    proposal.evidence,
                    self._artifact_store,
                )
            except (
                MemoryError,
                OSError,
                OverflowError,
                RecursionError,
                TypeError,
                UnicodeError,
                ValueError,
            ):
                snapshot_metadata = None
            if snapshot_metadata is not None:
                payload["procedure_source_snapshot"] = snapshot_metadata
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


def _trusted_governed_proposal_type(value: object) -> type[BaseModel] | None:
    value_type = type(value)
    try:
        value_mro = type.__getattribute__(value_type, "__mro__")
    except (AttributeError, TypeError):
        return None
    return next(
        (candidate for candidate in GOVERNED_PROPOSAL_CLASSES if candidate in value_mro),
        None,
    )


def _safe_model_state(value: object) -> dict[str, object] | None:
    try:
        state = object.__getattribute__(value, "__dict__")
    except (AttributeError, MemoryError, RecursionError, TypeError):
        return None
    if type(state) is not dict or any(type(key) is not str for key in state):
        return None
    return state


def _trusted_model_dump(
    value: object,
    model_type: type[BaseModel],
    *,
    mode: Literal["json", "python"],
) -> dict[str, object]:
    serializer = type.__getattribute__(model_type, "__pydantic_serializer__")
    dumped = serializer.to_python(value, mode=mode, warnings=False)
    if type(dumped) is not dict or any(type(key) is not str for key in dumped):
        raise ValueError("trusted model serializer returned an invalid mapping")
    return dumped


def _durable_proposal(
    proposal: Proposal,
    governed_type: type[BaseModel] | None,
    *,
    state_is_safe: bool | None = None,
) -> Proposal:
    if governed_type is None:
        return proposal
    try:
        if type(proposal) is not governed_type:
            raise TypeError("governed proposal subclasses are not durable input")
        if state_is_safe is None:
            state_is_safe = _governed_proposal_state_is_safe(proposal, governed_type)
        if not state_is_safe:
            raise TypeError("governed proposal contains unsafe exact state")
        dumped = _trusted_model_dump(proposal, governed_type, mode="python")
        return PROPOSAL_ADAPTER.validate_python(dumped)
    except (
        AssertionError,
        MemoryError,
        OverflowError,
        RecursionError,
        RuntimeError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        state = _safe_model_state(proposal) or {}
        proposal_id = _safe_identifier(state.get("proposal_id")) or (
            "invalid-reward-proposal"
            if governed_type is RecordRewardAssessment
            else "invalid-governed-proposal"
        )
        idempotency_key = _safe_identifier(state.get("idempotency_key")) or proposal_id
        proposer = _safe_proposer_state(state.get("proposer"))
        return InvalidProposal(
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            validation_error="governed proposal failed safe durable normalization",
            proposer=proposer,
            attempted_proposal_kind=PROPOSAL_KIND_ADAPTER.validate_python(
                governed_type.model_fields["proposal_type"].default
            ),
        )


def _mapping_is_within_proposal_bounds(value: dict[str, object]) -> bool:
    pending: list[tuple[object, int]] = [(value, 0)]
    visited_nodes = 0
    canonical_bytes = 0

    def consume(count: int) -> bool:
        nonlocal canonical_bytes
        if count < 0 or count > MAX_PROPOSAL_BYTES - canonical_bytes:
            return False
        canonical_bytes += count
        return True

    def consume_json_string(candidate: str) -> bool:
        if not consume(2):
            return False
        if len(candidate) > MAX_PROPOSAL_BYTES - canonical_bytes:
            return False
        try:
            for offset in range(0, len(candidate), 4_096):
                chunk = candidate[offset : offset + 4_096]
                if (
                    chunk.isascii()
                    and '"' not in chunk
                    and "\\" not in chunk
                    and all(ord(character) >= 0x20 for character in chunk)
                ):
                    if not consume(len(chunk)):
                        return False
                    continue
                for character in chunk:
                    codepoint = ord(character)
                    if character in ('"', "\\") or codepoint in (8, 9, 10, 12, 13):
                        encoded_length = 2
                    elif codepoint < 0x20:
                        encoded_length = 6
                    elif 0xD800 <= codepoint <= 0xDFFF:
                        return False
                    else:
                        encoded_length = len(character.encode("utf-8"))
                    if not consume(encoded_length):
                        return False
        except UnicodeError:
            return False
        return True

    while pending:
        current, depth = pending.pop()
        visited_nodes += 1
        if depth > MAX_PROPOSAL_JSON_DEPTH or visited_nodes > MAX_PROPOSAL_JSON_NODES:
            return False
        if type(current) is dict:
            if len(current) > MAX_PROPOSAL_JSON_CONTAINER_ITEMS or any(
                type(key) is not str for key in current
            ):
                return False
            if not consume(2 + max(0, len(current) - 1) + len(current)):
                return False
            if any(not consume_json_string(key) for key in current):
                return False
            pending.extend((item, depth + 1) for item in current.values())
        elif type(current) is list:
            if len(current) > MAX_PROPOSAL_JSON_CONTAINER_ITEMS:
                return False
            if not consume(2 + max(0, len(current) - 1)):
                return False
            pending.extend((item, depth + 1) for item in current)
        elif type(current) is str:
            if not consume_json_string(current):
                return False
        elif type(current) is int:
            bit_length = int.bit_length(current)
            decimal_upper_bound = max(1, (bit_length * 30_103 + 99_999) // 100_000)
            if current < 0:
                decimal_upper_bound += 1
            if not consume(decimal_upper_bound):
                return False
        elif type(current) is float:
            if not math.isfinite(current):
                return False
            if not consume(len(repr(current))):
                return False
        elif current is None:
            if not consume(4):
                return False
        elif type(current) is bool:
            if not consume(4 if current else 5):
                return False
        else:
            return False
    return True


def _governed_handler_failure_decision(
    proposal_id: str,
    governed_type: type[BaseModel],
) -> TransactionDecision:
    if governed_type is RecordRewardAssessment:
        return AdmissionEngine.rejected(
            proposal_id,
            RejectionCode.INVALID_REWARD,
            "reward assessment proposal is invalid",
        )
    if governed_type in (
        RecordProcedureCompilation,
        RecordMethodDirectionOutcome,
        BindCompiledProgressPlan,
    ):
        return AdmissionEngine.rejected(
            proposal_id,
            RejectionCode.INVALID_PROCEDURE,
            "procedure proposal is invalid",
        )
    if governed_type in (
        RecordGuidanceEvaluationProtocol,
        AppendGuidanceEvaluationCell,
        RecordModelHarnessProtocol,
        AppendModelHarnessCell,
        RecordModelHarnessAnalysis,
        RecordHarnessExecutionTrace,
    ):
        return AdmissionEngine.rejected(
            proposal_id,
            RejectionCode.UNMATCHED_EVALUATION,
            "harness evaluation proposal is invalid",
        )
    return AdmissionEngine.rejected(
        proposal_id,
        RejectionCode.DERIVATION_MISMATCH,
        "governed proposal failed fixed handler validation",
    )


def _attempted_governed_proposal_type(
    proposal: InvalidProposal,
) -> type[BaseModel] | None:
    attempted_kind = proposal.attempted_proposal_kind
    return next(
        (
            proposal_type
            for proposal_type in GOVERNED_PROPOSAL_CLASSES
            if proposal_type.model_fields["proposal_type"].default == attempted_kind
        ),
        None,
    )


def _normalize_proposal(value: object) -> Proposal | TransactionDecision:
    governed_type = _trusted_governed_proposal_type(value)
    if governed_type is not None:
        if type(value) is governed_type:
            return cast(Proposal, value)
        return _durable_proposal(cast(Proposal, value), governed_type)
    value_type = type(value)
    if value_type is not dict and value_type not in _PROPOSAL_EXACT_TYPES:
        return _invalid_proposal_decision("invalid-proposal")
    try:
        if value_type in (
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
            CreateHarnessCampaign,
            RecordHarnessIteration,
            RecordHarnessProtectedResult,
            RecordHarnessConfound,
            DecideHarnessCampaign,
        ):
            return PROPOSAL_ADAPTER.validate_json(
                canonical_json_bytes(
                    _trusted_model_dump(
                        value,
                        cast(type[BaseModel], value_type),
                        mode="json",
                    )
                )
            )
        if value_type in _PROPOSAL_EXACT_TYPES:
            return PROPOSAL_ADAPTER.validate_python(
                _trusted_model_dump(
                    value,
                    cast(type[BaseModel], value_type),
                    mode="python",
                )
            )
        if value_type is dict:
            exact_mapping = cast(dict[str, object], value)
            if not _mapping_is_within_proposal_bounds(exact_mapping):
                raise ValueError("proposal mapping exceeds fixed bounds")
            encoded = canonical_json_bytes(exact_mapping)
            if len(encoded) > MAX_PROPOSAL_BYTES:
                raise ValueError("proposal mapping exceeds fixed byte bound")
            return PROPOSAL_ADAPTER.validate_json(encoded)
        return PROPOSAL_ADAPTER.validate_python(value)
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
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
    state = _safe_model_state(value)
    if state is not None:
        candidate = state.get(field)
    elif type(value) is dict:
        candidate = dict.get(value, field)
    else:
        return None
    return _safe_identifier(candidate)


def _safe_identifier(value: object) -> str | None:
    try:
        return IDENTIFIER_ADAPTER.validate_python(value)
    except Exception:
        return None


def _safe_proposer_state(candidate: object) -> ActorIdentity | None:
    try:
        if type(candidate) is dict:
            return ActorIdentity.model_validate_json(canonical_json_bytes(candidate))
        if type(candidate) is ActorIdentity:
            return _fresh_actor_identity(candidate)
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        return None
    return None


def _invalid_proposal_decision(proposal_id: str) -> TransactionDecision:
    return AdmissionEngine.rejected(
        proposal_id,
        RejectionCode.INVALID_PROPOSAL,
        "proposal failed service boundary validation and could not be stored",
    )


def _matches_attempt(proposal: Proposal, attempt: ProposalAttempt) -> bool:
    proposal_kind = (
        proposal.attempted_proposal_kind
        if isinstance(proposal, InvalidProposal)
        else proposal.proposal_type
    )
    return (
        proposal.proposal_id == attempt.proposal_id
        and proposal.idempotency_key == attempt.idempotency_key
        and proposal.proposer == attempt.proposer
        and proposal_kind == attempt.proposal_kind
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
    return _safe_proposer_state(_raw_proposal_field(value, "proposer"))


def _safe_proposal_kind(value: object) -> ProposalKind | None:
    candidate = _raw_proposal_field(value, "proposal_type")
    try:
        return PROPOSAL_KIND_ADAPTER.validate_python(candidate)
    except (TypeError, ValueError):
        return None


def _raw_proposal_field(value: object, field: str) -> object:
    state = _safe_model_state(value)
    if state is not None:
        return state.get(field)
    if type(value) is dict:
        return dict.get(value, field)
    return None


def _active_connection(connection: Connection | None) -> Connection:
    if connection is None or connection.closed:
        raise RuntimeError("unit of work is not active")
    return connection
