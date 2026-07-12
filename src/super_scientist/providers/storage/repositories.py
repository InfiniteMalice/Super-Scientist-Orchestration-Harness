from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import Connection, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes, sha256_hex
from super_scientist.kernel.audit.models import AuditEvent
from super_scientist.kernel.transactions.models import Proposal, TransactionDecision
from super_scientist.providers.storage.schema import (
    audit_events,
    claim_heads,
    claim_versions,
    evidence_records,
    governance_policies,
    governance_state,
    transactions,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
TIMESTAMP_ADAPTER: TypeAdapter[UtcTimestamp] = TypeAdapter(UtcTimestamp)
_STORAGE_TYPE_KEY = "__super_scientist_storage_type__"
_STORAGE_ITEMS_KEY = "items"
_STORAGE_ENUMS: dict[str, type[Enum]] = {
    enum_type.__name__: enum_type
    for enum_type in (ActorKind, ClaimStatus, VerificationState)
}


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, frozenset):
        compatible = [_json_compatible(item) for item in value]
        return sorted(compatible, key=canonical_json_bytes)
    return value


def _serialization_fallback(value: object) -> object:
    compatible = _json_compatible(value)
    if compatible is value:
        raise TypeError(f"unsupported JSON value: {type(value).__name__}")
    return compatible


def _encode_storage_value(value: object) -> object:
    if isinstance(value, Mapping):
        encoded = {key: _encode_storage_value(item) for key, item in value.items()}
        if _STORAGE_TYPE_KEY in value:
            return {
                _STORAGE_TYPE_KEY: "mapping",
                _STORAGE_ITEMS_KEY: [[key, encoded[key]] for key in sorted(encoded)],
            }
        return encoded
    if isinstance(value, (list, tuple)):
        return [_encode_storage_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        encoded_items = [_encode_storage_value(item) for item in value]
        return {
            _STORAGE_TYPE_KEY: "frozenset",
            _STORAGE_ITEMS_KEY: sorted(encoded_items, key=canonical_json_bytes),
        }
    if isinstance(value, datetime):
        return {
            _STORAGE_TYPE_KEY: "datetime",
            _STORAGE_ITEMS_KEY: value.isoformat(),
        }
    if isinstance(value, Enum):
        return {
            _STORAGE_TYPE_KEY: "enum",
            _STORAGE_ITEMS_KEY: [value.__class__.__name__, value.value],
        }
    return value


def _decode_storage_value(value: object) -> object:
    if isinstance(value, list):
        return [_decode_storage_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {_STORAGE_TYPE_KEY, _STORAGE_ITEMS_KEY}:
        storage_type = value[_STORAGE_TYPE_KEY]
        items = value[_STORAGE_ITEMS_KEY]
        if storage_type == "frozenset":
            if not isinstance(items, list):
                raise ValueError("invalid frozenset storage envelope")
            return frozenset(_decode_storage_value(item) for item in items)
        if storage_type == "mapping":
            if not isinstance(items, list):
                raise ValueError("invalid mapping storage envelope")
            decoded: dict[str, object] = {}
            for item in items:
                if (
                    not isinstance(item, list)
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or item[0] in decoded
                ):
                    raise ValueError("invalid mapping storage item")
                decoded[item[0]] = _decode_storage_value(item[1])
            return decoded
        if storage_type == "datetime":
            if not isinstance(items, str):
                raise ValueError("invalid datetime storage envelope")
            return datetime.fromisoformat(items)
        if storage_type == "enum":
            if (
                not isinstance(items, list)
                or len(items) != 2
                or not isinstance(items[0], str)
            ):
                raise ValueError("invalid enum storage envelope")
            enum_type = _STORAGE_ENUMS.get(items[0])
            if enum_type is None:
                raise ValueError(f"unsupported stored enum: {items[0]}")
            return enum_type(items[1])
    return {key: _decode_storage_value(item) for key, item in value.items()}


def _storage_json(value: object) -> str:
    return canonical_json_bytes(_encode_storage_value(value)).decode("utf-8")


def _load_storage_json(value: str) -> object:
    return _decode_storage_value(json.loads(value))


def _validated_model_json[ModelT: BaseModel](model_type: type[ModelT], value: ModelT) -> str:
    dumped = value.model_dump(mode="python", warnings="none")
    validated = model_type.model_validate(dumped)
    return validated.model_dump_json(warnings="none", fallback=_serialization_fallback)


def _validated_timestamp(value: UtcTimestamp) -> UtcTimestamp:
    return TIMESTAMP_ADAPTER.validate_python(value)


class StoredTransaction(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal: Proposal
    proposal_hash: str
    decision: TransactionDecision


class EvidenceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        row = self._connection.execute(
            select(evidence_records.c.record_json).where(
                evidence_records.c.evidence_id == evidence_id
            )
        ).scalar_one_or_none()
        return None if row is None else EvidenceRecord.model_validate(_load_storage_json(row))

    def list_all(self) -> tuple[EvidenceRecord, ...]:
        rows = self._connection.execute(
            select(evidence_records.c.record_json).order_by(evidence_records.c.evidence_id)
        ).scalars()
        return tuple(EvidenceRecord.model_validate(_load_storage_json(row)) for row in rows)

    def add(self, record: EvidenceRecord) -> None:
        dumped = record.model_dump(mode="python", warnings="none")
        validated = EvidenceRecord.model_validate(dumped)
        record_json = _storage_json(validated.model_dump(mode="python", warnings="none"))
        self._connection.execute(
            insert(evidence_records).values(
                evidence_id=validated.evidence_id,
                content_hash=validated.content_hash,
                record_json=record_json,
                created_at=validated.retrieved_at.isoformat(),
            )
        )


class ClaimRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_head(self, claim_id: str) -> AtomicClaim | None:
        row = self._connection.execute(
            select(claim_versions.c.record_json)
            .join(
                claim_heads,
                claim_heads.c.claim_version_id == claim_versions.c.claim_version_id,
            )
            .where(claim_heads.c.claim_id == claim_id)
        ).scalar_one_or_none()
        return None if row is None else AtomicClaim.model_validate_json(row)

    def get_head_required(self, claim_id: str) -> AtomicClaim:
        claim = self.get_head(claim_id)
        if claim is None:
            raise KeyError(f"claim does not exist: {claim_id}")
        return claim

    def list_heads(self) -> tuple[AtomicClaim, ...]:
        rows = self._connection.execute(
            select(claim_versions.c.record_json)
            .join(
                claim_heads,
                claim_heads.c.claim_version_id == claim_versions.c.claim_version_id,
            )
            .order_by(claim_heads.c.claim_id)
        ).scalars()
        return tuple(AtomicClaim.model_validate_json(row) for row in rows)

    def history(self, claim_id: str) -> tuple[AtomicClaim, ...]:
        rows = self._connection.execute(
            select(claim_versions.c.record_json)
            .where(claim_versions.c.claim_id == claim_id)
            .order_by(claim_versions.c.version)
        ).scalars()
        return tuple(AtomicClaim.model_validate_json(row) for row in rows)

    def add_version(self, claim: AtomicClaim) -> None:
        record_json = _validated_model_json(AtomicClaim, claim)
        validated = AtomicClaim.model_validate_json(record_json)
        version_id = f"{validated.claim_id}:{validated.version}"
        self._connection.execute(
            insert(claim_versions).values(
                claim_version_id=version_id,
                claim_id=validated.claim_id,
                version=validated.version,
                status=validated.status.value,
                record_json=record_json,
                content_hash=sha256_hex(record_json.encode("utf-8")),
                created_at=validated.created_at.isoformat(),
            )
        )
        head_insert = sqlite_insert(claim_heads).values(
            claim_id=validated.claim_id,
            claim_version_id=version_id,
            version=validated.version,
            status=validated.status.value,
        )
        self._connection.execute(
            head_insert.on_conflict_do_update(
                index_elements=[claim_heads.c.claim_id],
                set_={
                    "claim_version_id": version_id,
                    "version": validated.version,
                    "status": validated.status.value,
                },
            )
        )


class TransactionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_by_idempotency_key(self, key: str) -> StoredTransaction | None:
        row = self._connection.execute(
            select(
                transactions.c.proposal_json,
                transactions.c.proposal_hash,
                transactions.c.decision_json,
            ).where(transactions.c.idempotency_key == key)
        ).one_or_none()
        if row is None:
            return None
        proposal_json, proposal_hash, decision_json = row
        return StoredTransaction(
            proposal=PROPOSAL_ADAPTER.validate_python(_load_storage_json(proposal_json)),
            proposal_hash=proposal_hash,
            decision=TransactionDecision.model_validate_json(decision_json),
        )

    def list_all(self) -> tuple[StoredTransaction, ...]:
        rows = self._connection.execute(
            select(
                transactions.c.proposal_json,
                transactions.c.proposal_hash,
                transactions.c.decision_json,
            ).order_by(transactions.c.created_at, transactions.c.proposal_id)
        )
        return tuple(
            StoredTransaction(
                proposal=PROPOSAL_ADAPTER.validate_python(_load_storage_json(proposal_json)),
                proposal_hash=proposal_hash,
                decision=TransactionDecision.model_validate_json(decision_json),
            )
            for proposal_json, proposal_hash, decision_json in rows
        )

    def add(
        self,
        proposal: Proposal,
        decision: TransactionDecision,
        occurred_at: UtcTimestamp,
    ) -> None:
        dumped_proposal = proposal.model_dump(mode="python", warnings="none")
        validated_proposal = PROPOSAL_ADAPTER.validate_python(dumped_proposal)
        proposal_json = _storage_json(
            validated_proposal.model_dump(mode="python", warnings="none")
        )
        decision_json = _validated_model_json(TransactionDecision, decision)
        validated_occurred_at = _validated_timestamp(occurred_at)
        self._connection.execute(
            insert(transactions).values(
                proposal_id=validated_proposal.proposal_id,
                idempotency_key=validated_proposal.idempotency_key,
                proposal_hash=sha256_hex(proposal_json.encode("utf-8")),
                proposal_json=proposal_json,
                decision_json=decision_json,
                created_at=validated_occurred_at.isoformat(),
            )
        )


class AuditRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def last(self) -> AuditEvent | None:
        row = self._connection.execute(
            select(audit_events.c.event_json).order_by(audit_events.c.sequence.desc()).limit(1)
        ).scalar_one_or_none()
        return None if row is None else AuditEvent.model_validate_json(row)

    def list_all(self) -> tuple[AuditEvent, ...]:
        rows = self._connection.execute(
            select(audit_events.c.event_json).order_by(audit_events.c.sequence)
        ).scalars()
        return tuple(AuditEvent.model_validate_json(row) for row in rows)

    def add(self, event: AuditEvent) -> None:
        event_json = _validated_model_json(AuditEvent, event)
        validated = AuditEvent.model_validate_json(event_json)
        self._connection.execute(
            insert(audit_events).values(
                sequence=validated.sequence,
                event_id=validated.event_id,
                previous_hash=validated.previous_hash,
                payload_hash=validated.payload_hash,
                event_hash=validated.event_hash,
                event_json=event_json,
            )
        )


class PolicyRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def add_and_activate(self, snapshot: PolicySnapshot, created_at: UtcTimestamp) -> None:
        snapshot_json = _validated_model_json(PolicySnapshot, snapshot)
        validated = PolicySnapshot.model_validate_json(snapshot_json)
        validated_created_at = _validated_timestamp(created_at)
        policy_insert = sqlite_insert(governance_policies).values(
            policy_hash=validated.policy_hash,
            policy_json=validated.policy.model_dump_json(),
            created_at=validated_created_at.isoformat(),
        )
        self._connection.execute(policy_insert.on_conflict_do_nothing())
        state_insert = sqlite_insert(governance_state).values(
            singleton_id=1,
            active_policy_hash=validated.policy_hash,
        )
        self._connection.execute(
            state_insert.on_conflict_do_update(
                index_elements=[governance_state.c.singleton_id],
                set_={"active_policy_hash": validated.policy_hash},
            )
        )

    def get_active(self) -> PolicySnapshot | None:
        row = self._connection.execute(
            select(
                governance_policies.c.policy_hash,
                governance_policies.c.policy_json,
            )
            .join(
                governance_state,
                governance_state.c.active_policy_hash == governance_policies.c.policy_hash,
            )
            .where(governance_state.c.singleton_id == 1)
        ).one_or_none()
        if row is None:
            return None
        policy_hash, policy_json = row
        return PolicySnapshot(
            policy_hash=policy_hash,
            policy=GovernancePolicy.model_validate_json(policy_json),
        )


class RepositorySet:
    def __init__(self, connection: Connection) -> None:
        self.evidence = EvidenceRepository(connection)
        self.claims = ClaimRepository(connection)
        self.transactions = TransactionRepository(connection)
        self.audit = AuditRepository(connection)
        self.policies = PolicyRepository(connection)
