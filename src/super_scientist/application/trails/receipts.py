from __future__ import annotations

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import Connection

from super_scientist.domain.evidence_trails.models import (
    AddEvidenceReceiptRef,
    EvidenceTrailNodeStageReceiptRef,
    EvidenceTrailRelationStageReceiptRef,
    ProposeClaimReceiptRef,
    TrailReceiptRef,
    TransitionClaimReceiptRef,
)
from super_scientist.domain.primitives import (
    Sha256Hex,
    UtcTimestamp,
    canonical_json_bytes,
)
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    ProposeClaim,
    ProposeEvidenceTrailNodes,
    ProposeEvidenceTrailRelations,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.repositories import (
    AuditRepository,
    StoredTransaction,
    TransactionRepository,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)

type ReceiptProposal = (
    AddEvidence
    | ProposeEvidenceTrailNodes
    | ProposeEvidenceTrailRelations
    | ProposeClaim
    | TransitionClaim
)


class AcceptedProposalReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    reference: TrailReceiptRef
    proposal: ReceiptProposal
    transaction_created_at: UtcTimestamp
    audit_sequence: int
    audit_occurred_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class AcceptedProposalReceiptReader:
    """Resolve accepted immutable transactions through their exact audit event."""

    def __init__(self, connection: Connection) -> None:
        self._transactions = TransactionRepository(connection)
        self._audit = AuditRepository(connection)

    def get(self, proposal_id: str) -> AcceptedProposalReceipt | None:
        transaction = self._transactions.get_by_proposal_id(proposal_id)
        if transaction is None or not transaction.decision.accepted:
            return None
        proposal = transaction.proposal
        if not isinstance(
            proposal,
            (
                AddEvidence,
                ProposeEvidenceTrailNodes,
                ProposeEvidenceTrailRelations,
                ProposeClaim,
                TransitionClaim,
            ),
        ):
            return None
        matching_events = tuple(
            event
            for event in self._audit.list_all()
            if _audit_event_matches(event, proposal, transaction.decision)
        )
        if len(matching_events) != 1:
            return None
        event = matching_events[0]
        payload = json_compatible_payload(event.payload)
        try:
            governing_policy_hash = SHA256_ADAPTER.validate_python(payload["policy_hash"])
        except (KeyError, ValidationError):
            return None
        return AcceptedProposalReceipt(
            reference=_receipt_reference(proposal, transaction.proposal_hash, event),
            proposal=proposal,
            transaction_created_at=transaction.created_at,
            audit_sequence=event.sequence,
            audit_occurred_at=event.occurred_at,
            governing_policy_hash=governing_policy_hash,
        )

    def resolve(self, reference: TrailReceiptRef) -> AcceptedProposalReceipt | None:
        receipt = self.get(reference.proposal_id)
        return receipt if receipt is not None and receipt.reference == reference else None


def accepted_proposal_receipts(
    transactions: tuple[StoredTransaction, ...],
    events: tuple[AuditEvent, ...],
) -> dict[str, AcceptedProposalReceipt]:
    """Build a pure exact receipt index for chronological workspace replay."""

    receipts: dict[str, AcceptedProposalReceipt] = {}
    for transaction in transactions:
        proposal = transaction.proposal
        if not transaction.decision.accepted or not isinstance(
            proposal,
            (
                AddEvidence,
                ProposeEvidenceTrailNodes,
                ProposeEvidenceTrailRelations,
                ProposeClaim,
                TransitionClaim,
            ),
        ):
            continue
        matches = tuple(
            event
            for event in events
            if _audit_event_matches(event, proposal, transaction.decision)
        )
        if len(matches) != 1:
            continue
        event = matches[0]
        payload = json_compatible_payload(event.payload)
        try:
            governing_policy_hash = SHA256_ADAPTER.validate_python(
                payload["policy_hash"]
            )
        except (KeyError, ValidationError):
            continue
        receipts[proposal.proposal_id] = AcceptedProposalReceipt(
            reference=_receipt_reference(
                proposal,
                transaction.proposal_hash,
                event,
            ),
            proposal=proposal,
            transaction_created_at=transaction.created_at,
            audit_sequence=event.sequence,
            audit_occurred_at=event.occurred_at,
            governing_policy_hash=governing_policy_hash,
        )
    return receipts


def _audit_event_matches(
    event: AuditEvent,
    proposal: ReceiptProposal,
    decision: TransactionDecision,
) -> bool:
    payload = json_compatible_payload(event.payload)
    if payload.get("transaction_persisted") is not True:
        return False
    try:
        audited_proposal = PROPOSAL_ADAPTER.validate_json(
            canonical_json_bytes(payload["proposal"])
        )
        audited_decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(payload["decision"])
        )
        SHA256_ADAPTER.validate_python(payload["policy_hash"])
    except (KeyError, ValidationError):
        return False
    return audited_proposal == proposal and audited_decision == decision


def _receipt_reference(
    proposal: ReceiptProposal,
    proposal_hash: str,
    event: AuditEvent,
) -> TrailReceiptRef:
    if isinstance(proposal, AddEvidence):
        return AddEvidenceReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, ProposeEvidenceTrailNodes):
        return EvidenceTrailNodeStageReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, ProposeEvidenceTrailRelations):
        return EvidenceTrailRelationStageReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, ProposeClaim):
        return ProposeClaimReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    return TransitionClaimReceiptRef(
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal_hash,
        audit_event_id=event.event_id,
        audit_event_hash=event.event_hash,
    )
