from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import Connection

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.application.representations.records import (
    primitive_evaluation_to_storage,
    primitive_version_from_storage,
    primitive_version_to_storage,
)
from super_scientist.application.representations.service import (
    evaluator_independence_rejection,
    primitive_mutation_authority_rejection,
    projected_primitive_status,
    status_is_promotable,
)
from super_scientist.application.transactions.contracts import (
    HandlerReadCapability,
    HandlerWriteCapability,
    ProposalHandler,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.evidence_trails.authority import parse_external_grounding
from super_scientist.domain.improvement.classification import (
    ExternalGrounding,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    EvaluatorAuditRecord,
    MeasurementDecision,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import Sha256Hex, UtcTimestamp, canonical_json_bytes
from super_scientist.domain.representations.models import (
    AcceptedPrimitiveReceiptRef,
    EvaluatorAuditReceiptRef,
    NewFrameEvaluation,
    OldFrameEvaluation,
    PrimitiveEvaluation,
    PrimitiveEvaluationReceiptRef,
    PrimitiveReceiptRef,
    PrimitiveStatus,
    PrimitiveVersion,
    PrimitiveVersionReceiptRef,
    SelfImprovementMeasurementReceiptRef,
    semantic_change_between,
    validate_semantic_version_change,
)
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AdmitPrimitiveVersion,
    Proposal,
    ProposePrimitiveVersion,
    RecordEvaluatorAudit,
    RecordPrimitiveEvaluation,
    RecordSelfImprovementMeasurement,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.domain_records import (
    EvaluatorAuditRepository,
    PrimitiveEvaluationRecord,
    PrimitiveEvaluationRepository,
    PrimitiveHeadRepository,
    PrimitiveVersionRecord,
    PrimitiveVersionRepository,
    SelfImprovementMeasurementRepository,
    VerificationMechanismCategory,
    VerificationMechanismSpecRecord,
    VerificationMechanismSpecRepository,
    VerificationOutcome,
    VerificationResultRecord,
    VerificationResultRepository,
)
from super_scientist.providers.storage.domain_records import (
    PrimitiveStatus as StoredPrimitiveStatus,
)
from super_scientist.providers.storage.repositories import (
    AuditRepository,
    EvidenceRepository,
    StoredTransaction,
    TransactionRepository,
)

type FixedRepresentationHandler = ProposalHandler[BaseModel, BaseModel]
type ReceiptProposal = (
    ProposePrimitiveVersion
    | RecordPrimitiveEvaluation
    | RecordEvaluatorAudit
    | RecordSelfImprovementMeasurement
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)


class RepresentationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    reference: PrimitiveReceiptRef
    proposal: ReceiptProposal
    transaction_created_at: UtcTimestamp
    audit_sequence: int
    audit_occurred_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class RepresentationReceiptReader:
    """Read one accepted transaction through its one exact audit event."""

    def __init__(self, connection: Connection) -> None:
        self._transactions = TransactionRepository(connection)
        self._audit = AuditRepository(connection)
        self._receipts: dict[str, RepresentationReceipt] | None = None

    def resolve(self, reference: AcceptedPrimitiveReceiptRef) -> RepresentationReceipt | None:
        if self._receipts is None:
            self._receipts = representation_receipts(
                self._transactions.list_all(),
                self._audit.list_all(),
            )
        receipt = self._receipts.get(reference.proposal_id)
        return receipt if receipt is not None and receipt.reference == reference else None


def representation_receipts(
    transactions: tuple[StoredTransaction, ...],
    events: tuple[AuditEvent, ...],
) -> dict[str, RepresentationReceipt]:
    """Build the same exact receipt index without live repository authority."""

    receipts: dict[str, RepresentationReceipt] = {}
    for transaction in transactions:
        proposal = transaction.proposal
        if not transaction.decision.accepted or not isinstance(
            proposal,
            (
                ProposePrimitiveVersion,
                RecordPrimitiveEvaluation,
                RecordEvaluatorAudit,
                RecordSelfImprovementMeasurement,
            ),
        ):
            continue
        matches = tuple(event for event in events if _audit_matches(event, proposal, transaction))
        if len(matches) != 1:
            continue
        event = matches[0]
        payload = json_compatible_payload(event.payload)
        try:
            governing_policy_hash = SHA256_ADAPTER.validate_python(payload["policy_hash"])
        except (KeyError, ValidationError):
            continue
        receipts[proposal.proposal_id] = RepresentationReceipt(
            reference=_receipt_reference(proposal, transaction, event),
            proposal=proposal,
            transaction_created_at=transaction.created_at,
            audit_sequence=event.sequence,
            audit_occurred_at=event.occurred_at,
            governing_policy_hash=governing_policy_hash,
        )
    return receipts


@dataclass(frozen=True)
class RetainedRepresentationEvidenceReader:
    _evidence: EvidenceRepository
    _artifacts: ArtifactStore

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        evidence = self._evidence.get(evidence_id)
        if evidence is not None:
            verify_artifact_binding(evidence, self._artifacts)
        return evidence


@dataclass(frozen=True, slots=True)
class StoredPrimitiveResolver:
    _versions: PrimitiveVersionRepository
    _heads: PrimitiveHeadRepository

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self._versions.get(version_id)

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        head = self._heads.get(primitive_id)
        return None if head is None else (head[0], head[1], PrimitiveStatus(head[2].value))


@dataclass(frozen=True, slots=True)
class PrimitiveStageReader:
    _versions: PrimitiveVersionRepository
    _transactions: TransactionRepository
    _heads: PrimitiveHeadRepository

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self._versions.get(version_id)

    def list_staged_versions(self) -> tuple[PrimitiveVersion, ...]:
        staged: dict[str, PrimitiveVersion] = {}
        for transaction in self._transactions.list_all():
            proposal = transaction.proposal
            if transaction.decision.accepted and isinstance(proposal, ProposePrimitiveVersion):
                primitive = proposal.primitive_version
                staged[primitive.primitive_version_id] = primitive
        return tuple(staged.values())

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        head = self._heads.get(primitive_id)
        return None if head is None else (head[0], head[1], PrimitiveStatus(head[2].value))


@dataclass(frozen=True, slots=True)
class PrimitiveEvaluationReader:
    _receipts: RepresentationReceiptReader
    _versions: PrimitiveVersionRepository
    _evaluations: PrimitiveEvaluationRepository
    _results: VerificationResultRepository
    _mechanisms: VerificationMechanismSpecRepository
    _evidence: RetainedRepresentationEvidenceReader

    def resolve_receipt(
        self,
        reference: AcceptedPrimitiveReceiptRef,
    ) -> RepresentationReceipt | None:
        return self._receipts.resolve(reference)

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self._versions.get(version_id)

    def get_evaluation(self, evaluation_id: str) -> PrimitiveEvaluationRecord | None:
        return self._evaluations.get(evaluation_id)

    def get_result(self, result_id: str) -> VerificationResultRecord | None:
        return self._results.get(result_id)

    def get_mechanism(self, mechanism_id: str) -> VerificationMechanismSpecRecord | None:
        return self._mechanisms.get(mechanism_id)

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self._evidence.get(evidence_id)


@dataclass(frozen=True, slots=True)
class PrimitiveAdmissionReader:
    _evaluation: PrimitiveEvaluationReader
    _measurements: SelfImprovementMeasurementRepository
    _evaluator_audits: EvaluatorAuditRepository
    _heads: PrimitiveHeadRepository

    def resolve_receipt(
        self,
        reference: AcceptedPrimitiveReceiptRef,
    ) -> RepresentationReceipt | None:
        return self._evaluation.resolve_receipt(reference)

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self._evaluation.get_stored_version(version_id)

    def get_evaluation(self, evaluation_id: str) -> PrimitiveEvaluationRecord | None:
        return self._evaluation.get_evaluation(evaluation_id)

    def get_result(self, result_id: str) -> VerificationResultRecord | None:
        return self._evaluation.get_result(result_id)

    def get_mechanism(self, mechanism_id: str) -> VerificationMechanismSpecRecord | None:
        return self._evaluation.get_mechanism(mechanism_id)

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self._evaluation.get_evidence(evidence_id)

    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None:
        return self._measurements.get(measurement_id)

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None:
        return self._evaluator_audits.get(audit_id)

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        head = self._heads.get(primitive_id)
        return None if head is None else (head[0], head[1], PrimitiveStatus(head[2].value))


@dataclass(frozen=True, slots=True)
class PrimitiveStageCapabilities:
    active_policy: PolicySnapshot
    reader: PrimitiveStageReader

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self.reader.get_stored_version(version_id)

    def list_staged_versions(self) -> tuple[PrimitiveVersion, ...]:
        return self.reader.list_staged_versions()

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        return self.reader.get_head(primitive_id)


@dataclass(frozen=True, slots=True)
class PrimitiveEvaluationCapabilities:
    active_policy: PolicySnapshot
    reader: PrimitiveEvaluationReader

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def resolve_receipt(
        self,
        reference: AcceptedPrimitiveReceiptRef,
    ) -> RepresentationReceipt | None:
        return self.reader.resolve_receipt(reference)

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self.reader.get_stored_version(version_id)

    def get_evaluation(self, evaluation_id: str) -> PrimitiveEvaluationRecord | None:
        return self.reader.get_evaluation(evaluation_id)

    def get_result(self, result_id: str) -> VerificationResultRecord | None:
        return self.reader.get_result(result_id)

    def get_mechanism(self, mechanism_id: str) -> VerificationMechanismSpecRecord | None:
        return self.reader.get_mechanism(mechanism_id)

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.reader.get_evidence(evidence_id)


@dataclass(frozen=True, slots=True)
class PrimitiveAdmissionCapabilities:
    active_policy: PolicySnapshot
    reader: PrimitiveAdmissionReader

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def resolve_receipt(
        self,
        reference: AcceptedPrimitiveReceiptRef,
    ) -> RepresentationReceipt | None:
        return self.reader.resolve_receipt(reference)

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self.reader.get_stored_version(version_id)

    def get_evaluation(self, evaluation_id: str) -> PrimitiveEvaluationRecord | None:
        return self.reader.get_evaluation(evaluation_id)

    def get_result(self, result_id: str) -> VerificationResultRecord | None:
        return self.reader.get_result(result_id)

    def get_mechanism(self, mechanism_id: str) -> VerificationMechanismSpecRecord | None:
        return self.reader.get_mechanism(mechanism_id)

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.reader.get_evidence(evidence_id)

    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None:
        return self.reader.get_measurement(measurement_id)

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None:
        return self.reader.get_evaluator_audit(audit_id)

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        return self.reader.get_head(primitive_id)


@dataclass(frozen=True, slots=True)
class PrimitiveVersionAppender:
    _versions: PrimitiveVersionRepository
    _stages: PrimitiveStageReader

    def append_version(self, primitive: PrimitiveVersion) -> None:
        if self._versions.get(primitive.primitive_version_id) is not None:
            return
        status = projected_primitive_status(primitive, self._stages.list_staged_versions())
        record = primitive_version_to_storage(
            primitive,
            status=StoredPrimitiveStatus(status.value),
        )
        self._versions.add(record.primitive_version_id, record, record.created_at)


@dataclass(frozen=True, slots=True)
class PrimitiveEvaluationAppender:
    _evaluations: PrimitiveEvaluationRepository

    def append_evaluation(self, evaluation: PrimitiveEvaluation) -> None:
        record = primitive_evaluation_to_storage(evaluation)
        if self._evaluations.get(record.primitive_evaluation_id) == record:
            return
        self._evaluations.add(record.primitive_evaluation_id, record, record.evaluated_at)


@dataclass(frozen=True, slots=True)
class PrimitiveHeadSetter:
    _receipts: RepresentationReceiptReader
    _versions: PrimitiveVersionRepository
    _heads: PrimitiveHeadRepository

    def set_head_from_candidate_receipt(self, reference: PrimitiveVersionReceiptRef) -> None:
        receipt = self._receipts.resolve(reference)
        candidate_proposal = None if receipt is None else receipt.proposal
        if not isinstance(candidate_proposal, ProposePrimitiveVersion):
            raise ValueError("accepted primitive admission lost its candidate receipt")
        candidate = candidate_proposal.primitive_version
        stored = self._versions.get(candidate.primitive_version_id)
        if (
            stored is None
            or primitive_version_to_storage(candidate, status=stored.status) != stored
        ):
            raise ValueError("accepted primitive admission lost its retained candidate")
        retained = primitive_version_from_storage(stored)
        self._heads.set(
            retained.primitive_id,
            retained.primitive_version_id,
            retained.semantic_version,
            stored.status,
        )


@dataclass(frozen=True, slots=True)
class RepresentationCapabilitySet:
    reads: HandlerReadCapability
    writes: HandlerWriteCapability


def stored_primitive_resolver(connection: Connection) -> StoredPrimitiveResolver:
    return StoredPrimitiveResolver(
        _versions=PrimitiveVersionRepository(connection),
        _heads=PrimitiveHeadRepository(connection),
    )


class _StageReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None: ...

    def list_staged_versions(self) -> tuple[PrimitiveVersion, ...]: ...

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None: ...


class _EvaluationReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def resolve_receipt(
        self,
        reference: AcceptedPrimitiveReceiptRef,
    ) -> RepresentationReceipt | None: ...

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None: ...

    def get_evaluation(self, evaluation_id: str) -> PrimitiveEvaluationRecord | None: ...

    def get_result(self, result_id: str) -> VerificationResultRecord | None: ...

    def get_mechanism(self, mechanism_id: str) -> VerificationMechanismSpecRecord | None: ...

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None: ...


class _AdmissionReadCapability(_EvaluationReadCapability, Protocol):
    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None: ...

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None: ...

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None: ...


class _StageWriteCapability(Protocol):
    def append_version(self, primitive: PrimitiveVersion) -> None: ...


class _EvaluationWriteCapability(Protocol):
    def append_evaluation(self, evaluation: PrimitiveEvaluation) -> None: ...


class _AdmissionWriteCapability(Protocol):
    def set_head_from_candidate_receipt(self, reference: PrimitiveVersionReceiptRef) -> None: ...


class _StageContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing: PrimitiveVersionRecord | None
    retained: tuple[PrimitiveVersion, ...]
    head: tuple[str, str, PrimitiveStatus] | None


class _EvaluationContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    candidate_receipt: RepresentationReceipt | None
    stored_candidate: PrimitiveVersionRecord | None
    existing: PrimitiveEvaluationRecord | None
    results: tuple[VerificationResultRecord | None, ...]
    mechanisms: tuple[VerificationMechanismSpecRecord | None, ...]
    evidence: tuple[EvidenceRecord | None, ...]


class _AdmissionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    candidate_receipt: RepresentationReceipt | None
    old_receipt: RepresentationReceipt | None
    new_receipt: RepresentationReceipt | None
    evaluator_audit_receipt: RepresentationReceipt | None
    measurement_receipt: RepresentationReceipt | None
    stored_candidate: PrimitiveVersionRecord | None
    rollback: PrimitiveVersionRecord | None
    old_evaluation: PrimitiveEvaluationRecord | None
    new_evaluation: PrimitiveEvaluationRecord | None
    old_results: tuple[VerificationResultRecord | None, ...]
    new_results: tuple[VerificationResultRecord | None, ...]
    old_mechanisms: tuple[VerificationMechanismSpecRecord | None, ...]
    new_mechanisms: tuple[VerificationMechanismSpecRecord | None, ...]
    old_evidence: tuple[EvidenceRecord | None, ...]
    new_evidence: tuple[EvidenceRecord | None, ...]
    measurement: SelfImprovementMeasurementRecord | None
    evaluator_audit: EvaluatorAuditRecord | None
    head: tuple[str, str, PrimitiveStatus] | None


class ProposePrimitiveVersionHandler:
    proposal_type = "propose_primitive_version"

    def build_context(
        self,
        proposal: ProposePrimitiveVersion,
        reads: HandlerReadCapability,
    ) -> _StageContext:
        capability = cast(_StageReadCapability, reads)
        primitive = proposal.primitive_version
        return _StageContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_stored_version(primitive.primitive_version_id),
            retained=capability.list_staged_versions(),
            head=capability.get_head(primitive.primitive_id),
        )

    def decide(
        self,
        proposal: ProposePrimitiveVersion,
        context: _StageContext,
    ) -> TransactionDecision:
        primitive = proposal.primitive_version
        rejection = primitive_mutation_authority_rejection(proposal, context.active_policy)
        if rejection is not None:
            return rejection
        if primitive.proposer != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "primitive author must match proposal actor",
            )
        if primitive.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_rejection(proposal.proposal_id, "primitive version")
        approval = proposal.approval
        if approval is None or primitive.created_at >= approval.approved_at:
            return _chronology_rejection(proposal.proposal_id, "primitive version")
        if context.existing is not None:
            retained_exact = any(
                item.primitive_version_id == primitive.primitive_version_id and item == primitive
                for item in context.retained
            )
            expected = primitive_version_to_storage(
                primitive,
                status=context.existing.status,
            )
            if retained_exact and context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _rejected(
                proposal.proposal_id,
                RejectionCode.IDEMPOTENCY_CONFLICT,
                "primitive version stable key was reused with changed content",
            )
        same_primitive = tuple(
            item for item in context.retained if item.primitive_id == primitive.primitive_id
        )
        if any(item.semantic_version == primitive.semantic_version for item in same_primitive):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.IDEMPOTENCY_CONFLICT,
                "primitive semantic version already exists under another stable key",
            )
        dependency_ids = set(primitive.dependency_primitive_version_ids)
        retained_ids = {item.primitive_version_id for item in context.retained}
        if not dependency_ids.issubset(retained_ids):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_DEPENDENCY,
                "primitive dependencies must resolve to retained versions",
            )
        lineage = _stage_lineage_rejection(proposal, context, same_primitive)
        if lineage is not None:
            return lineage
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: ProposePrimitiveVersion,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_StageWriteCapability, writes).append_version(proposal.primitive_version)


class RecordPrimitiveEvaluationHandler:
    proposal_type = "record_primitive_evaluation"

    def build_context(
        self,
        proposal: RecordPrimitiveEvaluation,
        reads: HandlerReadCapability,
    ) -> _EvaluationContext:
        capability = cast(_EvaluationReadCapability, reads)
        evaluation = proposal.evaluation
        results = tuple(
            capability.get_result(identifier) for identifier in evaluation.verification_result_ids
        )
        return _EvaluationContext(
            active_policy=capability.policy_snapshot(),
            candidate_receipt=capability.resolve_receipt(proposal.candidate_receipt),
            stored_candidate=capability.get_stored_version(evaluation.primitive_version_id),
            existing=capability.get_evaluation(evaluation.primitive_evaluation_id),
            results=results,
            mechanisms=tuple(
                None if result is None else capability.get_mechanism(result.mechanism_spec_id)
                for result in results
            ),
            evidence=tuple(capability.get_evidence(item) for item in evaluation.evidence_ids),
        )

    def decide(
        self,
        proposal: RecordPrimitiveEvaluation,
        context: _EvaluationContext,
    ) -> TransactionDecision:
        receipt = context.candidate_receipt
        candidate_proposal = None if receipt is None else receipt.proposal
        if not isinstance(candidate_proposal, ProposePrimitiveVersion):
            return _receipt_rejection(proposal.proposal_id, "candidate primitive")
        receipt = cast(RepresentationReceipt, receipt)
        candidate = candidate_proposal.primitive_version
        authority = primitive_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=(candidate.proposer, *proposal.evaluation.check_actors),
        )
        if authority is not None:
            return authority
        evaluation = proposal.evaluation
        if (
            evaluation.primitive_version_id != candidate.primitive_version_id
            or context.stored_candidate is None
            or context.stored_candidate.primitive_version_id != candidate.primitive_version_id
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "primitive evaluation must bind one retained candidate receipt",
            )
        if evaluation.provenance.actor != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "primitive evaluator must match proposal actor",
            )
        approval = proposal.approval
        independence = (
            None
            if approval is None
            else evaluator_independence_rejection(
                primitive_author=candidate.proposer,
                evaluator=evaluation.provenance.actor,
                check_actors=evaluation.check_actors,
                approver=approval.approver,
            )
        )
        if independence is not None:
            return _rejected(
                proposal.proposal_id,
                independence,
                "primitive author, evaluator, check actors, and approver must be independent",
            )
        chronology = _evaluation_chronology_rejection(proposal, receipt)
        if chronology is not None:
            return chronology
        quality = _evaluation_quality_rejection(
            proposal.proposal_id,
            evaluation,
            context.results,
            context.mechanisms,
            context.evidence,
            context.active_policy.policy_hash,
            require_passed=False,
        )
        if quality is not None:
            return quality
        expected = primitive_evaluation_to_storage(evaluation)
        if context.existing is not None:
            if context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _rejected(
                proposal.proposal_id,
                RejectionCode.IDEMPOTENCY_CONFLICT,
                "primitive evaluation stable key was reused with changed content",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordPrimitiveEvaluation,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_EvaluationWriteCapability, writes).append_evaluation(proposal.evaluation)


class AdmitPrimitiveVersionHandler:
    proposal_type = "admit_primitive_version"

    def build_context(
        self,
        proposal: AdmitPrimitiveVersion,
        reads: HandlerReadCapability,
    ) -> _AdmissionContext:
        capability = cast(_AdmissionReadCapability, reads)
        candidate_receipt = capability.resolve_receipt(proposal.candidate_receipt)
        old_receipt = capability.resolve_receipt(proposal.old_frame_evaluation_receipt)
        new_receipt = capability.resolve_receipt(proposal.new_frame_evaluation_receipt)
        audit_receipt = (
            None
            if proposal.evaluator_audit_receipt is None
            else capability.resolve_receipt(proposal.evaluator_audit_receipt)
        )
        measurement_receipt = (
            None
            if proposal.measurement_receipt is None
            else capability.resolve_receipt(proposal.measurement_receipt)
        )
        candidate_proposal = None if candidate_receipt is None else candidate_receipt.proposal
        candidate = (
            candidate_proposal.primitive_version
            if isinstance(candidate_proposal, ProposePrimitiveVersion)
            else None
        )
        old_proposal = None if old_receipt is None else old_receipt.proposal
        new_proposal = None if new_receipt is None else new_receipt.proposal
        old_evaluation = (
            old_proposal.evaluation if isinstance(old_proposal, RecordPrimitiveEvaluation) else None
        )
        new_evaluation = (
            new_proposal.evaluation if isinstance(new_proposal, RecordPrimitiveEvaluation) else None
        )
        old_results = tuple(
            capability.get_result(item)
            for item in (() if old_evaluation is None else old_evaluation.verification_result_ids)
        )
        new_results = tuple(
            capability.get_result(item)
            for item in (() if new_evaluation is None else new_evaluation.verification_result_ids)
        )
        measurement_proposal = None if measurement_receipt is None else measurement_receipt.proposal
        audit_proposal = None if audit_receipt is None else audit_receipt.proposal
        return _AdmissionContext(
            active_policy=capability.policy_snapshot(),
            candidate_receipt=candidate_receipt,
            old_receipt=old_receipt,
            new_receipt=new_receipt,
            evaluator_audit_receipt=audit_receipt,
            measurement_receipt=measurement_receipt,
            stored_candidate=(
                None
                if candidate is None
                else capability.get_stored_version(candidate.primitive_version_id)
            ),
            rollback=capability.get_stored_version(proposal.rollback_primitive_version_id),
            old_evaluation=(
                None
                if old_evaluation is None
                else capability.get_evaluation(old_evaluation.primitive_evaluation_id)
            ),
            new_evaluation=(
                None
                if new_evaluation is None
                else capability.get_evaluation(new_evaluation.primitive_evaluation_id)
            ),
            old_results=old_results,
            new_results=new_results,
            old_mechanisms=tuple(
                None if result is None else capability.get_mechanism(result.mechanism_spec_id)
                for result in old_results
            ),
            new_mechanisms=tuple(
                None if result is None else capability.get_mechanism(result.mechanism_spec_id)
                for result in new_results
            ),
            old_evidence=tuple(
                capability.get_evidence(item)
                for item in (() if old_evaluation is None else old_evaluation.evidence_ids)
            ),
            new_evidence=tuple(
                capability.get_evidence(item)
                for item in (() if new_evaluation is None else new_evaluation.evidence_ids)
            ),
            measurement=(
                None
                if not isinstance(measurement_proposal, RecordSelfImprovementMeasurement)
                else capability.get_measurement(measurement_proposal.measurement.measurement_id)
            ),
            evaluator_audit=(
                None
                if not isinstance(audit_proposal, RecordEvaluatorAudit)
                else capability.get_evaluator_audit(
                    audit_proposal.evaluator_audit.evaluator_audit_id
                )
            ),
            head=(None if candidate is None else capability.get_head(candidate.primitive_id)),
        )

    def decide(
        self,
        proposal: AdmitPrimitiveVersion,
        context: _AdmissionContext,
    ) -> TransactionDecision:
        resolved = (
            context.candidate_receipt,
            context.old_receipt,
            context.new_receipt,
            context.evaluator_audit_receipt,
            context.measurement_receipt,
        )
        if any(item is None for item in resolved):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "primitive admission requires exact candidate, evaluation, audit, "
                "and measurement receipts",
            )
        candidate_receipt = cast(RepresentationReceipt, context.candidate_receipt)
        old_receipt = cast(RepresentationReceipt, context.old_receipt)
        new_receipt = cast(RepresentationReceipt, context.new_receipt)
        audit_receipt = cast(RepresentationReceipt, context.evaluator_audit_receipt)
        measurement_receipt = cast(RepresentationReceipt, context.measurement_receipt)
        candidate_proposal = candidate_receipt.proposal
        old_proposal = old_receipt.proposal
        new_proposal = new_receipt.proposal
        if (
            not isinstance(candidate_proposal, ProposePrimitiveVersion)
            or not isinstance(old_proposal, RecordPrimitiveEvaluation)
            or not isinstance(new_proposal, RecordPrimitiveEvaluation)
            or old_proposal.candidate_receipt != proposal.candidate_receipt
            or new_proposal.candidate_receipt != proposal.candidate_receipt
        ):
            return _receipt_rejection(proposal.proposal_id, "primitive admission chain")
        candidate = candidate_proposal.primitive_version
        old_evaluation = old_proposal.evaluation
        new_evaluation = new_proposal.evaluation
        evaluation_actors = (
            old_evaluation.provenance.actor,
            *old_evaluation.check_actors,
            new_evaluation.provenance.actor,
            *new_evaluation.check_actors,
        )
        protected = context.measurement is not None and bool(context.measurement.protected_metrics)
        authority = primitive_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=(candidate.proposer, *evaluation_actors),
            promotion=True,
            protected_evaluation=protected,
            rollback_present=context.rollback is not None,
        )
        if authority is not None:
            return authority
        approval = proposal.approval
        if approval is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "primitive admission requires approval",
            )
        independence = evaluator_independence_rejection(
            primitive_author=candidate.proposer,
            evaluator=old_evaluation.provenance.actor,
            check_actors=(
                *old_evaluation.check_actors,
                new_evaluation.provenance.actor,
                *new_evaluation.check_actors,
            ),
            approver=approval.approver,
        )
        if independence is not None:
            return _rejected(
                proposal.proposal_id,
                independence,
                "candidate, evaluators, check actors, and admission approver must be independent",
            )
        lineage = _admission_lineage_rejection(proposal, context, candidate)
        if lineage is not None:
            return lineage
        if (
            not isinstance(old_evaluation.frame_evaluation, OldFrameEvaluation)
            or not isinstance(new_evaluation.frame_evaluation, NewFrameEvaluation)
            or old_evaluation.primitive_version_id != candidate.primitive_version_id
            or new_evaluation.primitive_version_id != candidate.primitive_version_id
            or context.old_evaluation != primitive_evaluation_to_storage(old_evaluation)
            or context.new_evaluation != primitive_evaluation_to_storage(new_evaluation)
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "admission requires exact retained old-frame and new-frame evaluations",
            )
        for evaluation, results, mechanisms, evidence in (
            (
                old_evaluation,
                context.old_results,
                context.old_mechanisms,
                context.old_evidence,
            ),
            (
                new_evaluation,
                context.new_results,
                context.new_mechanisms,
                context.new_evidence,
            ),
        ):
            quality = _evaluation_quality_rejection(
                proposal.proposal_id,
                evaluation,
                results,
                mechanisms,
                evidence,
                context.active_policy.policy_hash,
                require_passed=True,
            )
            if quality is not None:
                return quality
        support = _promotion_support_rejection(
            proposal,
            context,
            candidate,
            old_evaluation,
            new_evaluation,
        )
        if support is not None:
            return support
        if not _receipt_chain_precedes(
            candidate_receipt,
            old_receipt,
            new_receipt,
            audit_receipt,
            measurement_receipt,
        ):
            return _chronology_rejection(proposal.proposal_id, "primitive admission receipts")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: AdmitPrimitiveVersion,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_AdmissionWriteCapability, writes).set_head_from_candidate_receipt(
            proposal.candidate_receipt
        )


def fixed_representation_handlers() -> tuple[FixedRepresentationHandler, ...]:
    return (  # type: ignore[return-value]
        ProposePrimitiveVersionHandler(),
        RecordPrimitiveEvaluationHandler(),
        AdmitPrimitiveVersionHandler(),
    )


def representation_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
    artifact_store: ArtifactStore,
) -> RepresentationCapabilitySet:
    if isinstance(proposal, ProposePrimitiveVersion):
        versions = PrimitiveVersionRepository(connection)
        reader = PrimitiveStageReader(
            _versions=versions,
            _transactions=TransactionRepository(connection),
            _heads=PrimitiveHeadRepository(connection),
        )
        stage_reads = PrimitiveStageCapabilities(
            active_policy=active_policy,
            reader=reader,
        )
        writes = PrimitiveVersionAppender(
            _versions=versions,
            _stages=reader,
        )
        return RepresentationCapabilitySet(
            reads=cast(HandlerReadCapability, stage_reads),
            writes=cast(HandlerWriteCapability, writes),
        )
    evidence = RetainedRepresentationEvidenceReader(
        _evidence=EvidenceRepository(connection),
        _artifacts=artifact_store,
    )
    receipt_reader = RepresentationReceiptReader(connection)
    versions = PrimitiveVersionRepository(connection)
    evaluations = PrimitiveEvaluationRepository(connection)
    evaluation_reader = PrimitiveEvaluationReader(
        _receipts=receipt_reader,
        _versions=versions,
        _evaluations=evaluations,
        _results=VerificationResultRepository(connection),
        _mechanisms=VerificationMechanismSpecRepository(connection),
        _evidence=evidence,
    )
    if isinstance(proposal, RecordPrimitiveEvaluation):
        evaluation_reads = PrimitiveEvaluationCapabilities(
            active_policy=active_policy,
            reader=evaluation_reader,
        )
        return RepresentationCapabilitySet(
            reads=cast(HandlerReadCapability, evaluation_reads),
            writes=cast(
                HandlerWriteCapability,
                PrimitiveEvaluationAppender(_evaluations=evaluations),
            ),
        )
    if isinstance(proposal, AdmitPrimitiveVersion):
        heads = PrimitiveHeadRepository(connection)
        admission_reads = PrimitiveAdmissionCapabilities(
            active_policy=active_policy,
            reader=PrimitiveAdmissionReader(
                _evaluation=evaluation_reader,
                _measurements=SelfImprovementMeasurementRepository(connection),
                _evaluator_audits=EvaluatorAuditRepository(connection),
                _heads=heads,
            ),
        )
        return RepresentationCapabilitySet(
            reads=cast(HandlerReadCapability, admission_reads),
            writes=cast(
                HandlerWriteCapability,
                PrimitiveHeadSetter(
                    _receipts=receipt_reader,
                    _versions=versions,
                    _heads=heads,
                ),
            ),
        )
    raise TypeError(f"no fixed representation capability for proposal: {type(proposal)!r}")


def _stage_lineage_rejection(
    proposal: ProposePrimitiveVersion,
    context: _StageContext,
    same_primitive: tuple[PrimitiveVersion, ...],
) -> TransactionDecision | None:
    primitive = proposal.primitive_version
    if not same_primitive:
        if primitive.predecessor_primitive_version_ids:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "first primitive version cannot name a predecessor",
            )
        return None
    expected_predecessor_id = (
        context.head[0] if context.head is not None else same_primitive[-1].primitive_version_id
    )
    if primitive.predecessor_primitive_version_ids != (expected_predecessor_id,):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "primitive version must name the exact current lineage predecessor",
        )
    predecessor = next(
        (item for item in same_primitive if item.primitive_version_id == expected_predecessor_id),
        None,
    )
    if predecessor is None:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "primitive predecessor is unavailable",
        )
    semver = validate_semantic_version_change(
        predecessor.semantic_version,
        primitive.semantic_version,
        change=semantic_change_between(predecessor, primitive),
    )
    if not semver.accepted:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            semver.code,
        )
    return None


def _evaluation_chronology_rejection(
    proposal: RecordPrimitiveEvaluation,
    candidate_receipt: RepresentationReceipt,
) -> TransactionDecision | None:
    evaluation = proposal.evaluation
    approval = proposal.approval
    if (
        approval is None
        or candidate_receipt.governing_policy_hash != evaluation.governing_policy_hash
        or candidate_receipt.transaction_created_at >= evaluation.evaluated_at
        or candidate_receipt.audit_occurred_at >= evaluation.evaluated_at
        or evaluation.evaluated_at >= approval.approved_at
    ):
        return _chronology_rejection(proposal.proposal_id, "primitive evaluation")
    return None


def _evaluation_quality_rejection(
    proposal_id: str,
    evaluation: PrimitiveEvaluation,
    results: tuple[VerificationResultRecord | None, ...],
    mechanisms: tuple[VerificationMechanismSpecRecord | None, ...],
    evidence: tuple[EvidenceRecord | None, ...],
    policy_hash: str,
    *,
    require_passed: bool,
) -> TransactionDecision | None:
    if (
        evaluation.governing_policy_hash != policy_hash
        or evaluation.provenance.governing_policy_hash != policy_hash
        or evaluation.provenance.category is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        or evaluation.provenance.deterministic_or_learned != "DETERMINISTIC"
        or len(results) != len(evaluation.verification_result_ids)
        or len(mechanisms) != len(results)
        or len(evidence) != len(evaluation.evidence_ids)
        or any(item is None for item in (*results, *mechanisms, *evidence))
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "primitive evaluation requires complete deterministic policy-bound checks and evidence",
        )
    retained_results = tuple(cast(VerificationResultRecord, item) for item in results)
    retained_mechanisms = tuple(cast(VerificationMechanismSpecRecord, item) for item in mechanisms)
    retained_evidence = tuple(cast(EvidenceRecord, item) for item in evidence)
    if any(
        result.verification_result_id != expected_id
        or result.mechanism_category
        is not VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER
        or result.governing_policy_hash != policy_hash
        or result.verified_by != check_actor.actor_id
        or result.completed_at > evaluation.evaluated_at
        or mechanism.mechanism_spec_id != result.mechanism_spec_id
        or mechanism.mechanism_category
        is not VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER
        or mechanism.governing_policy_hash != policy_hash
        or mechanism.created_by != check_actor.actor_id
        or mechanism.created_at > result.completed_at
        for expected_id, check_actor, result, mechanism in zip(
            evaluation.verification_result_ids,
            evaluation.check_actors,
            retained_results,
            retained_mechanisms,
            strict=True,
        )
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "primitive checks must be exact, deterministic, actor-bound, and chronological",
        )
    if require_passed and (
        evaluation.outcome is not AssessmentOutcome.PASSED
        or evaluation.provenance.result is not AssessmentOutcome.PASSED
        or any(result.outcome is not VerificationOutcome.PASS for result in retained_results)
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "primitive promotion requires passed old-frame and new-frame checks",
        )
    try:
        evidence_valid = all(
            item.evidence_id == expected_id
            and item.verification_state is VerificationState.HASH_VERIFIED
            and parse_external_grounding(item) is ExternalGrounding.CONTROLLED_EXPERIMENT
            and item.retrieved_at <= evaluation.evaluated_at
            for expected_id, item in zip(
                evaluation.evidence_ids,
                retained_evidence,
                strict=True,
            )
        )
    except ValueError:
        evidence_valid = False
    if not evidence_valid:
        return _rejected(
            proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "primitive evaluation requires exact artifact-verified controlled-experiment evidence",
        )
    return None


def _admission_lineage_rejection(
    proposal: AdmitPrimitiveVersion,
    context: _AdmissionContext,
    candidate: PrimitiveVersion,
) -> TransactionDecision | None:
    if (
        context.stored_candidate is None
        or context.stored_candidate.status.value != candidate.status.value
        or not status_is_promotable(candidate.status)
        or context.rollback is None
        or context.rollback.primitive_version_id != proposal.rollback_primitive_version_id
        or context.rollback.primitive_id != candidate.primitive_id
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED,
            "only an exact retained non-duplicate candidate with rollback can be admitted",
        )
    expected_predecessor = (
        proposal.rollback_primitive_version_id if context.head is None else context.head[0]
    )
    if (
        proposal.rollback_primitive_version_id != expected_predecessor
        or candidate.predecessor_primitive_version_ids != (expected_predecessor,)
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "primitive admission must bind the exact current head and rollback lineage",
        )
    return None


def _promotion_support_rejection(
    proposal: AdmitPrimitiveVersion,
    context: _AdmissionContext,
    candidate: PrimitiveVersion,
    old_evaluation: PrimitiveEvaluation,
    new_evaluation: PrimitiveEvaluation,
) -> TransactionDecision | None:
    measurement = context.measurement
    audit = context.evaluator_audit
    approval = proposal.approval
    measurement_receipt = context.measurement_receipt
    audit_receipt = context.evaluator_audit_receipt
    measurement_proposal = None if measurement_receipt is None else measurement_receipt.proposal
    audit_proposal = None if audit_receipt is None else audit_receipt.proposal
    if (
        measurement is None
        or audit is None
        or approval is None
        or not isinstance(measurement_proposal, RecordSelfImprovementMeasurement)
        or not isinstance(audit_proposal, RecordEvaluatorAudit)
        or measurement_proposal.measurement != measurement
        or audit_proposal.evaluator_audit != audit
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "primitive promotion requires retained measurement and evaluator audit",
        )
    evidence_ids = tuple(
        dict.fromkeys((*old_evaluation.evidence_ids, *new_evaluation.evidence_ids))
    )
    check_ids = tuple(
        dict.fromkeys(
            (*old_evaluation.verification_result_ids, *new_evaluation.verification_result_ids)
        )
    )
    measurement_source_ids = tuple(
        dict.fromkeys(
            metric.source_id
            for metric in (*measurement.protected_metrics, *measurement.countermetrics)
        )
    )
    if not (
        measurement.decision is MeasurementDecision.ACCEPTED
        and measurement.classification == proposal.classification
        and measurement.proposer == candidate.proposer
        and measurement.candidate_version_id == candidate.primitive_version_id
        and measurement.baseline_version_id == proposal.rollback_primitive_version_id
        and measurement.rollback_target_id == proposal.rollback_primitive_version_id
        and measurement.evaluator_audit_id == audit.evaluator_audit_id
        and measurement.decision_authority == approval.approver
        and measurement.governing_policy_hash == context.active_policy.policy_hash
        and audit.result is AssessmentOutcome.PASSED
        and audit.evaluator == measurement.evaluator
        and audit.evaluator_version == measurement.evaluator_version
        and audit.proposer == candidate.proposer
        and audit.candidate_producer == candidate.proposer
        and audit.evidence_ids == evidence_ids
        and audit.checks_run == check_ids
        and measurement_source_ids == evidence_ids
        and audit.governing_policy_hash == context.active_policy.policy_hash
        and max(old_evaluation.evaluated_at, new_evaluation.evaluated_at) < audit.audited_at
        and audit.audited_at < measurement.decided_at
        and measurement.decided_at < proposal.integrated_at
        and proposal.integrated_at < approval.approved_at
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "primitive promotion support is not exactly bound or causally ordered",
        )
    return None


def _receipt_chain_precedes(
    candidate: RepresentationReceipt,
    old: RepresentationReceipt,
    new: RepresentationReceipt,
    audit: RepresentationReceipt,
    measurement: RepresentationReceipt,
) -> bool:
    evaluation_receipts = sorted(
        (old, new),
        key=lambda item: (item.transaction_created_at, item.audit_sequence),
    )
    return bool(
        candidate.transaction_created_at < evaluation_receipts[0].transaction_created_at
        and candidate.audit_sequence < evaluation_receipts[0].audit_sequence
        and evaluation_receipts[-1].transaction_created_at < audit.transaction_created_at
        and evaluation_receipts[-1].audit_sequence < audit.audit_sequence
        and audit.transaction_created_at < measurement.transaction_created_at
        and audit.audit_sequence < measurement.audit_sequence
    )


def _audit_matches(
    event: AuditEvent,
    proposal: ReceiptProposal,
    transaction: StoredTransaction,
) -> bool:
    payload = json_compatible_payload(event.payload)
    if payload.get("transaction_persisted") is not True:
        return False
    try:
        audited_proposal = PROPOSAL_ADAPTER.validate_json(canonical_json_bytes(payload["proposal"]))
        audited_decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(payload["decision"])
        )
    except (KeyError, ValidationError):
        return False
    return audited_proposal == proposal and audited_decision == transaction.decision


def _receipt_reference(
    proposal: ReceiptProposal,
    transaction: StoredTransaction,
    event: AuditEvent,
) -> PrimitiveReceiptRef:
    if isinstance(proposal, ProposePrimitiveVersion):
        return PrimitiveVersionReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, RecordPrimitiveEvaluation):
        return PrimitiveEvaluationReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, RecordEvaluatorAudit):
        return EvaluatorAuditReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    return SelfImprovementMeasurementReceiptRef(
        proposal_id=proposal.proposal_id,
        proposal_hash=transaction.proposal_hash,
        audit_event_id=event.event_id,
        audit_event_hash=event.event_hash,
    )


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected primitive proposal cannot be projected")


def _policy_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.POLICY_HASH_MISMATCH,
        f"{label} must name the active policy",
    )


def _receipt_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.INVALID_LINEAGE,
        f"{label} receipt does not resolve to one accepted audited transaction",
    )


def _chronology_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.INVALID_LINEAGE,
        f"{label} violates required causal chronology",
    )


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)
