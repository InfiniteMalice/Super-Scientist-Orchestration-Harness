from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy import Connection

from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord
from super_scientist.domain.primitives import (
    Sha256Hex,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.domain.procedures.models import (
    MAX_PROCEDURE_ITEMS,
    AcceptedSourceReceiptRef,
    ArtifactCatalogEntry,
    BoundedIdentifier,
    ProcedureEvidenceSourceKind,
    RegisteredTool,
    RegisteredValidator,
    catalog_snapshot_content_hash,
)
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    RecordCapabilityProfile,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.repositories import (
    AuditRepository,
    EvidenceRepository,
    StoredTransaction,
    TransactionRepository,
)

__all__ = [
    "AcceptedProcedureSourceReceipt",
    "AcceptedProcedureSourceReceiptReader",
    "ArtifactCatalogSnapshotRepository",
    "ArtifactCatalogSource",
    "ProcedureSourceBinding",
    "ProcedureSourceSnapshot",
    "ProcedureSourceSnapshotRepository",
    "ToolCatalogSnapshotRepository",
    "ToolCatalogSource",
    "ValidatorCatalogSnapshotRepository",
    "ValidatorCatalogSource",
    "procedure_source_snapshot_audit_metadata",
    "procedure_source_snapshot_audit_metadata_from_bytes",
]

_PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
_SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)
_MAX_SOURCE_ARTIFACT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AcceptedProcedureSourceReceipt:
    reference: AcceptedSourceReceiptRef
    proposal: RecordCapabilityProfile | AddEvidence
    transaction_created_at: UtcTimestamp
    audit_sequence: int
    audit_occurred_at: UtcTimestamp
    governing_policy_hash: str


@dataclass(frozen=True, slots=True)
class AcceptedProcedureSourceBatch:
    receipts: Mapping[str, AcceptedProcedureSourceReceipt]
    transactions: tuple[StoredTransaction, ...]
    audit_events: tuple[AuditEvent, ...]


class AcceptedProcedureSourceReceiptReader:
    """Resolve one source reference through its accepted transaction and exact audit event."""

    def __init__(self, connection: Connection) -> None:
        self._transactions = TransactionRepository(connection)
        self._audit = AuditRepository(connection)
        self._resolved_by_receipt_id: dict[str, AcceptedProcedureSourceReceipt] = {}
        self._duplicate_receipt_ids: set[str] = set()

    def get(self, receipt_id: str) -> AcceptedProcedureSourceReceipt | None:
        if receipt_id in self._duplicate_receipt_ids:
            return None
        return self._resolved_by_receipt_id.get(receipt_id)

    def resolve(
        self,
        reference: AcceptedSourceReceiptRef,
    ) -> AcceptedProcedureSourceReceipt | None:
        try:
            exact_reference = AcceptedSourceReceiptRef.model_validate(
                reference.model_dump(mode="python", warnings=False)
            )
        except (TypeError, ValueError):
            return None
        transaction = self._transactions.get_by_proposal_id(exact_reference.proposal_id)
        if (
            transaction is None
            or not transaction.decision.accepted
            or transaction.proposal_hash != exact_reference.proposal_hash
            or not isinstance(transaction.proposal, (RecordCapabilityProfile, AddEvidence))
        ):
            return None
        matches = tuple(
            event
            for event in self._audit.list_all()
            if _audit_event_matches_transaction(event, transaction)
            and event.event_id == exact_reference.audit_event_id
            and event.event_hash == exact_reference.audit_event_hash
        )
        if len(matches) != 1:
            return None
        event = matches[0]
        policy_hash = _audit_policy_hash(event)
        if policy_hash is None:
            return None
        if not _proposal_matches_source_kind(transaction.proposal, exact_reference.source_kind):
            return None
        resolved = AcceptedProcedureSourceReceipt(
            reference=exact_reference,
            proposal=transaction.proposal,
            transaction_created_at=transaction.created_at,
            audit_sequence=event.sequence,
            audit_occurred_at=event.occurred_at,
            governing_policy_hash=policy_hash,
        )
        existing = self._resolved_by_receipt_id.get(exact_reference.receipt_id)
        if existing is not None and existing != resolved:
            self._resolved_by_receipt_id.pop(exact_reference.receipt_id, None)
            self._duplicate_receipt_ids.add(exact_reference.receipt_id)
            return None
        if exact_reference.receipt_id in self._duplicate_receipt_ids:
            return None
        self._resolved_by_receipt_id[exact_reference.receipt_id] = resolved
        return resolved

    def resolve_many(
        self,
        references: tuple[AcceptedSourceReceiptRef, ...],
    ) -> AcceptedProcedureSourceBatch | None:
        try:
            exact_references = tuple(
                AcceptedSourceReceiptRef.model_validate(
                    reference.model_dump(mode="python", warnings=False)
                )
                for reference in references
            )
            transactions = self._transactions.list_all()
            audit_events = self._audit.list_all()
        except (MemoryError, OverflowError, RecursionError, TypeError, ValueError):
            return None
        transactions_by_id = {item.proposal.proposal_id: item for item in transactions}
        events_by_id = {item.event_id: item for item in audit_events}
        receipts: dict[str, AcceptedProcedureSourceReceipt] = {}
        for reference in exact_references:
            transaction = transactions_by_id.get(reference.proposal_id)
            event = events_by_id.get(reference.audit_event_id)
            if (
                transaction is None
                or event is None
                or not transaction.decision.accepted
                or transaction.proposal_hash != reference.proposal_hash
                or event.event_hash != reference.audit_event_hash
                or not isinstance(transaction.proposal, (RecordCapabilityProfile, AddEvidence))
                or not _audit_event_matches_transaction(event, transaction)
                or not _proposal_matches_source_kind(transaction.proposal, reference.source_kind)
            ):
                return None
            policy_hash = _audit_policy_hash(event)
            if policy_hash is None or reference.receipt_id in receipts:
                return None
            receipts[reference.receipt_id] = AcceptedProcedureSourceReceipt(
                reference=reference,
                proposal=transaction.proposal,
                transaction_created_at=transaction.created_at,
                audit_sequence=event.sequence,
                audit_occurred_at=event.occurred_at,
                governing_policy_hash=policy_hash,
            )
        return AcceptedProcedureSourceBatch(
            receipts=MappingProxyType(receipts),
            transactions=transactions,
            audit_events=audit_events,
        )


def _proposal_matches_source_kind(
    proposal: RecordCapabilityProfile | AddEvidence,
    source_kind: ProcedureEvidenceSourceKind,
) -> bool:
    return (
        source_kind is ProcedureEvidenceSourceKind.CAPABILITY_PROFILE
        and isinstance(proposal, RecordCapabilityProfile)
    ) or (
        source_kind is not ProcedureEvidenceSourceKind.CAPABILITY_PROFILE
        and isinstance(proposal, AddEvidence)
    )


def _audit_policy_hash(event: AuditEvent) -> str | None:
    payload = json_compatible_payload(event.payload)
    try:
        policy_hash = _SHA256_ADAPTER.validate_python(payload["policy_hash"])
        stored_policy_hash = _SHA256_ADAPTER.validate_python(payload["stored_policy_hash"])
    except (KeyError, ValidationError):
        return None
    return policy_hash if policy_hash == stored_policy_hash else None


def _audit_event_matches_transaction(
    event: AuditEvent,
    transaction: StoredTransaction,
) -> bool:
    if event.event_type != "transaction_decision":
        return False
    payload = json_compatible_payload(event.payload)
    if payload.get("transaction_persisted") is not True:
        return False
    if _audit_policy_hash(event) is None:
        return False
    try:
        audited_proposal = _PROPOSAL_ADAPTER.validate_json(
            canonical_json_bytes(payload["proposal"])
        )
        audited_decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(payload["decision"])
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        audited_proposal == transaction.proposal
        and audited_decision == transaction.decision
        and audited_proposal.proposal_id == transaction.proposal.proposal_id
    )


@dataclass(frozen=True, slots=True)
class ArtifactCatalogSource:
    source_record_id: str
    source_content_hash: str
    entries: tuple[ArtifactCatalogEntry, ...]
    complete: bool
    evidence: EvidenceRecord
    receipt: AcceptedProcedureSourceReceipt


@dataclass(frozen=True, slots=True)
class ToolCatalogSource:
    source_record_id: str
    source_content_hash: str
    entries: tuple[RegisteredTool, ...]
    complete: bool
    evidence: EvidenceRecord
    receipt: AcceptedProcedureSourceReceipt


@dataclass(frozen=True, slots=True)
class ValidatorCatalogSource:
    source_record_id: str
    source_content_hash: str
    entries: tuple[RegisteredValidator, ...]
    complete: bool
    evidence: EvidenceRecord
    receipt: AcceptedProcedureSourceReceipt


class _FixedCatalogSnapshotRepository[EntryT: BaseModel, CatalogSourceT]:
    def __init__(
        self,
        connection: Connection,
        artifact_store: ArtifactStore,
        *,
        source_kind: ProcedureEvidenceSourceKind,
        entry_adapter: TypeAdapter[tuple[EntryT, ...]],
        entry_key: Callable[[EntryT], str | tuple[str, str]],
        result_factory: Callable[..., CatalogSourceT],
    ) -> None:
        self._receipts = AcceptedProcedureSourceReceiptReader(connection)
        self._evidence = EvidenceRepository(connection)
        self._artifacts = artifact_store
        self._source_kind = source_kind
        self._entry_adapter = entry_adapter
        self._entry_key = entry_key
        self._result_factory = result_factory

    def resolve(self, reference: AcceptedSourceReceiptRef) -> CatalogSourceT | None:
        receipt = self._receipts.resolve(reference)
        stored_evidence = self._evidence.get(reference.source_record_id)
        return self.resolve_verified(reference, receipt, stored_evidence)

    def resolve_verified(
        self,
        reference: AcceptedSourceReceiptRef,
        receipt: AcceptedProcedureSourceReceipt | None,
        stored_evidence: EvidenceRecord | None,
    ) -> CatalogSourceT | None:
        if (
            receipt is None
            or reference.source_kind is not self._source_kind
            or reference.source_schema_version != 1
            or not isinstance(receipt.proposal, AddEvidence)
        ):
            return None
        proposal_evidence = receipt.proposal.evidence
        if (
            stored_evidence is None
            or stored_evidence != proposal_evidence
            or proposal_evidence.evidence_id != reference.source_record_id
            or proposal_evidence.artifact.sha256 != reference.source_content_hash
        ):
            return None
        artifact = proposal_evidence.artifact
        if artifact.size_bytes > _MAX_SOURCE_ARTIFACT_BYTES:
            return None
        try:
            artifact_bytes = self._artifacts.read(artifact)
            if (
                len(artifact_bytes) != artifact.size_bytes
                or sha256_hex(artifact_bytes) != artifact.sha256
            ):
                return None
            entries, complete = self._decode(artifact_bytes)
        except (MemoryError, OSError, OverflowError, RecursionError, TypeError, ValueError):
            return None
        expected_hash = catalog_snapshot_content_hash(
            self._source_kind.value,
            entries,
            complete,
        )
        if (
            expected_hash != reference.source_content_hash
            or proposal_evidence.artifact.sha256 != expected_hash
        ):
            return None
        return self._result_factory(
            source_record_id=reference.source_record_id,
            source_content_hash=expected_hash,
            entries=entries,
            complete=complete,
            evidence=stored_evidence,
            receipt=receipt,
        )

    def _decode(self, artifact_bytes: bytes) -> tuple[tuple[EntryT, ...], bool]:
        if not artifact_bytes or len(artifact_bytes) > _MAX_SOURCE_ARTIFACT_BYTES:
            raise ValueError("catalog artifact is empty or oversized")
        decoded = json.loads(artifact_bytes)
        if type(decoded) is not dict or set(decoded) != {"catalog_kind", "entries", "complete"}:
            raise ValueError("catalog artifact has the wrong shape")
        if decoded["catalog_kind"] != self._source_kind.value:
            raise ValueError("catalog artifact has the wrong kind")
        if type(decoded["entries"]) is not list or type(decoded["complete"]) is not bool:
            raise ValueError("catalog artifact has invalid field types")
        entries = self._entry_adapter.validate_json(
            canonical_json_bytes(decoded["entries"]),
            strict=True,
        )
        if len(entries) > MAX_PROCEDURE_ITEMS:
            raise ValueError("catalog artifact contains too many entries")
        if canonical_json_bytes(decoded) != artifact_bytes:
            raise ValueError("catalog artifact is not canonical JSON")
        keys = tuple(self._entry_key(item) for item in entries)
        if len(set(keys)) != len(keys) or keys != tuple(sorted(keys)):
            raise ValueError("catalog entries are not uniquely canonically ordered")
        return entries, decoded["complete"]


class ArtifactCatalogSnapshotRepository(
    _FixedCatalogSnapshotRepository[ArtifactCatalogEntry, ArtifactCatalogSource]
):
    def __init__(self, connection: Connection, artifact_store: ArtifactStore) -> None:
        super().__init__(
            connection,
            artifact_store,
            source_kind=ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
            entry_adapter=TypeAdapter(tuple[ArtifactCatalogEntry, ...]),
            entry_key=lambda item: item.artifact_id,
            result_factory=ArtifactCatalogSource,
        )


class ToolCatalogSnapshotRepository(
    _FixedCatalogSnapshotRepository[RegisteredTool, ToolCatalogSource]
):
    def __init__(self, connection: Connection, artifact_store: ArtifactStore) -> None:
        super().__init__(
            connection,
            artifact_store,
            source_kind=ProcedureEvidenceSourceKind.TOOL_CATALOG,
            entry_adapter=TypeAdapter(tuple[RegisteredTool, ...]),
            entry_key=lambda item: item.tool.actor_id,
            result_factory=ToolCatalogSource,
        )


class ValidatorCatalogSnapshotRepository(
    _FixedCatalogSnapshotRepository[RegisteredValidator, ValidatorCatalogSource]
):
    def __init__(self, connection: Connection, artifact_store: ArtifactStore) -> None:
        super().__init__(
            connection,
            artifact_store,
            source_kind=ProcedureEvidenceSourceKind.VALIDATOR_CATALOG,
            entry_adapter=TypeAdapter(tuple[RegisteredValidator, ...]),
            entry_key=lambda item: (item.validator.actor_id, item.validator_version),
            result_factory=ValidatorCatalogSource,
        )


class ProcedureSourceBinding(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    source_record_id: BoundedIdentifier
    source_content_hash: Sha256Hex


class ProcedureSourceSnapshot(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    schema_version: Literal[1] = 1
    snapshot_family_id: BoundedIdentifier
    snapshot_id: BoundedIdentifier
    source_bindings: tuple[ProcedureSourceBinding, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)

    @field_validator("source_bindings")
    @classmethod
    def require_canonical_source_bindings(
        cls,
        bindings: tuple[ProcedureSourceBinding, ...],
    ) -> tuple[ProcedureSourceBinding, ...]:
        keys = tuple(item.source_record_id for item in bindings)
        if len(set(keys)) != len(keys) or keys != tuple(sorted(keys)):
            raise ValueError("source bindings must use unique canonical record order")
        return bindings


class _ProcedureSourceSnapshotAuditMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_family_id: BoundedIdentifier
    snapshot_id: BoundedIdentifier
    evidence_id: BoundedIdentifier
    artifact_hash: Sha256Hex


def procedure_source_snapshot_audit_metadata(
    snapshot: ProcedureSourceSnapshot,
    evidence: EvidenceRecord,
) -> dict[str, object] | None:
    if snapshot.snapshot_id != evidence.evidence_id or evidence.evidence_type != "procedure-source":
        return None
    metadata = _ProcedureSourceSnapshotAuditMetadata(
        snapshot_family_id=snapshot.snapshot_family_id,
        snapshot_id=snapshot.snapshot_id,
        evidence_id=evidence.evidence_id,
        artifact_hash=evidence.artifact.sha256,
    )
    return metadata.model_dump(mode="json", warnings=False)


def procedure_source_snapshot_audit_metadata_from_bytes(
    evidence: EvidenceRecord,
    artifact_bytes: bytes,
) -> dict[str, object] | None:
    if evidence.evidence_type != "procedure-source" or not artifact_bytes:
        return None
    try:
        snapshot = ProcedureSourceSnapshot.model_validate_json(artifact_bytes, strict=True)
        if canonical_json_bytes(snapshot.model_dump(mode="json", warnings=False)) != artifact_bytes:
            return None
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        return None
    return procedure_source_snapshot_audit_metadata(snapshot, evidence)


@dataclass(frozen=True, slots=True)
class _AcceptedProcedureSourceSnapshot:
    snapshot_family_id: str
    snapshot_id: str
    snapshot: ProcedureSourceSnapshot | None
    artifact: ArtifactRef
    evidence: EvidenceRecord | None
    audit_sequence: int


class ProcedureSourceSnapshotRepository:
    def __init__(self, connection: Connection, artifact_store: ArtifactStore) -> None:
        self._transactions = TransactionRepository(connection)
        self._audit = AuditRepository(connection)
        self._evidence = EvidenceRepository(connection)
        self._artifacts = artifact_store

    def resolve_exact(
        self,
        source_snapshot_id: str,
        source_snapshot_hash: str,
    ) -> ProcedureSourceSnapshot | None:
        accepted_snapshots = self._accepted_snapshots((source_snapshot_id,))
        matches = tuple(
            item
            for item in accepted_snapshots
            if item.snapshot_id == source_snapshot_id
            and item.artifact.sha256 == source_snapshot_hash
            and item.snapshot is not None
        )
        if len(matches) != 1:
            return None
        return matches[0].snapshot

    def resolve(
        self,
        source_snapshot_id: str,
        source_snapshot_hash: str,
    ) -> ProcedureSourceSnapshot | None:
        return self.resolve_exact(source_snapshot_id, source_snapshot_hash)

    def is_current(self, source_snapshot_id: str, source_snapshot_hash: str) -> bool:
        snapshots = self._accepted_snapshots((source_snapshot_id,))
        matches = tuple(
            item
            for item in snapshots
            if item.snapshot_id == source_snapshot_id
            and item.artifact.sha256 == source_snapshot_hash
            and item.snapshot is not None
        )
        if len(matches) != 1:
            return False
        target = matches[0]
        family = tuple(
            item for item in snapshots if item.snapshot_family_id == target.snapshot_family_id
        )
        if not family:
            return False
        greatest_sequence = max(item.audit_sequence for item in family)
        greatest = tuple(item for item in family if item.audit_sequence == greatest_sequence)
        return len(greatest) == 1 and greatest[0] == target

    def _accepted_snapshots(
        self,
        declared_snapshot_ids: tuple[str, ...],
    ) -> tuple[_AcceptedProcedureSourceSnapshot, ...]:
        events = self._audit.list_all()
        transactions = self._transactions.list_all()
        evidence_by_id = {
            item.evidence_id: item for item in self._evidence.get_many(declared_snapshot_ids)
        }
        return self.accepted_snapshots_from(transactions, events, evidence_by_id)

    def accepted_snapshots_from(
        self,
        transactions: tuple[StoredTransaction, ...],
        events: tuple[AuditEvent, ...],
        evidence_by_id: Mapping[str, EvidenceRecord],
    ) -> tuple[_AcceptedProcedureSourceSnapshot, ...]:
        transactions_by_id = {
            transaction.proposal.proposal_id: transaction for transaction in transactions
        }
        matching_events_by_proposal_id: dict[str, list[AuditEvent]] = {}
        for event in events:
            payload = json_compatible_payload(event.payload)
            proposal_payload = payload.get("proposal")
            proposal_id = (
                proposal_payload.get("proposal_id") if type(proposal_payload) is dict else None
            )
            if type(proposal_id) is not str:
                continue
            transaction = transactions_by_id.get(proposal_id)
            if transaction is not None and _audit_event_matches_transaction(event, transaction):
                matching_events_by_proposal_id.setdefault(proposal_id, []).append(event)
        accepted: list[_AcceptedProcedureSourceSnapshot] = []
        for transaction in transactions:
            if not transaction.decision.accepted or not isinstance(
                transaction.proposal, AddEvidence
            ):
                continue
            matching_events = tuple(
                matching_events_by_proposal_id.get(transaction.proposal.proposal_id, ())
            )
            if len(matching_events) != 1:
                continue
            proposal_evidence = transaction.proposal.evidence
            metadata_payload = json_compatible_payload(matching_events[0].payload).get(
                "procedure_source_snapshot"
            )
            try:
                metadata = _ProcedureSourceSnapshotAuditMetadata.model_validate(
                    metadata_payload,
                    strict=True,
                )
            except (TypeError, ValueError):
                continue
            if (
                metadata.snapshot_id != proposal_evidence.evidence_id
                or metadata.evidence_id != proposal_evidence.evidence_id
                or metadata.artifact_hash != proposal_evidence.artifact.sha256
            ):
                continue
            stored_evidence = evidence_by_id.get(proposal_evidence.evidence_id)
            snapshot = None
            if stored_evidence is not None:
                if stored_evidence != proposal_evidence:
                    continue
                snapshot = self._decode_snapshot(proposal_evidence.artifact)
                if (
                    snapshot is None
                    or snapshot.snapshot_id != metadata.snapshot_id
                    or snapshot.snapshot_family_id != metadata.snapshot_family_id
                ):
                    continue
            accepted.append(
                _AcceptedProcedureSourceSnapshot(
                    snapshot_family_id=metadata.snapshot_family_id,
                    snapshot_id=metadata.snapshot_id,
                    snapshot=snapshot,
                    artifact=proposal_evidence.artifact,
                    evidence=stored_evidence,
                    audit_sequence=matching_events[0].sequence,
                )
            )
        id_counts: dict[str, int] = {}
        for item in accepted:
            snapshot_id = item.snapshot_id
            id_counts[snapshot_id] = id_counts.get(snapshot_id, 0) + 1
        duplicate_ids = {snapshot_id for snapshot_id, count in id_counts.items() if count > 1}
        return tuple(item for item in accepted if item.snapshot_id not in duplicate_ids)

    def _decode_snapshot(self, artifact: ArtifactRef) -> ProcedureSourceSnapshot | None:
        if artifact.size_bytes > _MAX_SOURCE_ARTIFACT_BYTES:
            return None
        try:
            artifact_bytes = self._artifacts.read(artifact)
            if (
                not artifact_bytes
                or len(artifact_bytes) > _MAX_SOURCE_ARTIFACT_BYTES
                or len(artifact_bytes) != artifact.size_bytes
                or sha256_hex(artifact_bytes) != artifact.sha256
            ):
                return None
            snapshot = ProcedureSourceSnapshot.model_validate_json(artifact_bytes, strict=True)
            if (
                canonical_json_bytes(snapshot.model_dump(mode="json", warnings=False))
                != artifact_bytes
            ):
                return None
            if artifact.sha256 != _SHA256_ADAPTER.validate_python(artifact.sha256):
                return None
            return snapshot
        except (MemoryError, OSError, OverflowError, RecursionError, TypeError, ValueError):
            return None
