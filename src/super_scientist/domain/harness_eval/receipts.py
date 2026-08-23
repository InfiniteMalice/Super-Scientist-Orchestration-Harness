from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    canonical_json_bytes,
    sha256_hex,
)

MAX_RECEIPT_IDENTIFIER_LENGTH = 200
MAX_RECEIPT_SCHEMA_VERSION = 2_147_483_647

BoundedReceiptIdentifier = Annotated[
    StableIdentifier,
    Field(
        max_length=MAX_RECEIPT_IDENTIFIER_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]


class EvidenceReceipt(BaseModel):
    """Repository-independent identity for evidence resolved by a later handler."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )

    record_id: BoundedReceiptIdentifier
    schema_version: int = Field(strict=True, ge=1, le=MAX_RECEIPT_SCHEMA_VERSION)
    content_hash: Sha256Hex


class ResolvedEvidenceKind(StrEnum):
    EXPECTATION_SOURCE = "EXPECTATION_SOURCE"
    RESOLVER = "RESOLVER"
    PROVENANCE = "PROVENANCE"
    VERIFICATION_RESULT_SOURCE = "VERIFICATION_RESULT_SOURCE"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    DIAGNOSTIC_SOURCE = "DIAGNOSTIC_SOURCE"
    OBSERVABLE_EVIDENCE = "OBSERVABLE_EVIDENCE"


class _ResolvedEvidenceRecordPayload(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )

    schema_version: int = Field(strict=True, ge=1, le=MAX_RECEIPT_SCHEMA_VERSION)
    receipt: EvidenceReceipt
    kind: ResolvedEvidenceKind
    snapshot_hash: Sha256Hex

    @model_validator(mode="after")
    def require_receipt_to_address_snapshot(self) -> _ResolvedEvidenceRecordPayload:
        if self.receipt.content_hash != self.snapshot_hash:
            raise ValueError("resolved evidence receipt must address the exact snapshot")
        return self


class ResolvedEvidenceRecord(_ResolvedEvidenceRecordPayload):
    """One committed receipt/snapshot record resolved by a later handler."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> ResolvedEvidenceRecord:
        payload = _ResolvedEvidenceRecordPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=resolved_evidence_record_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> ResolvedEvidenceRecord:
        if self.content_hash != resolved_evidence_record_hash(self):
            raise ValueError("content_hash must canonically address resolved evidence")
        return self


def resolved_evidence_record_hash(record: BaseModel | Mapping[str, object]) -> str:
    payload = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
    payload.pop("content_hash", None)
    return sha256_hex(canonical_json_bytes(payload))


class _ResolvedEvidenceInventoryPayload(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )

    schema_version: int = Field(strict=True, ge=1, le=MAX_RECEIPT_SCHEMA_VERSION)
    inventory_id: BoundedReceiptIdentifier
    resolved_by: EvidenceReceipt
    records: tuple[ResolvedEvidenceRecord, ...] = Field(min_length=1, max_length=4096)

    @field_validator("records")
    @classmethod
    def require_canonical_unique_records(
        cls,
        values: tuple[ResolvedEvidenceRecord, ...],
    ) -> tuple[ResolvedEvidenceRecord, ...]:
        keys = tuple(
            (
                item.receipt.record_id,
                item.receipt.schema_version,
                item.receipt.content_hash,
                item.kind.value,
            )
            for item in values
        )
        record_ids = tuple(item.receipt.record_id for item in values)
        if (
            len(keys) != len(set(keys))
            or len(record_ids) != len(set(record_ids))
            or keys != tuple(sorted(keys))
        ):
            raise ValueError("resolved inventory records must be unique and canonical")
        return values


class ResolvedEvidenceInventory(_ResolvedEvidenceInventoryPayload):
    """Capability supplied from handler-resolved committed evidence records."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> ResolvedEvidenceInventory:
        payload = _ResolvedEvidenceInventoryPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=resolved_evidence_inventory_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> ResolvedEvidenceInventory:
        if self.content_hash != resolved_evidence_inventory_hash(self):
            raise ValueError("content_hash must canonically address resolved evidence inventory")
        return self


def resolved_evidence_inventory_hash(record: BaseModel | Mapping[str, object]) -> str:
    payload = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
    payload.pop("content_hash", None)
    return sha256_hex(canonical_json_bytes(payload))


def require_resolved_evidence(
    inventory: ResolvedEvidenceInventory,
    receipt: EvidenceReceipt,
    kind: ResolvedEvidenceKind,
) -> ResolvedEvidenceRecord:
    validated_inventory = (
        inventory
        if type(inventory) is ResolvedEvidenceInventory
        else ResolvedEvidenceInventory.model_validate(inventory)
    )
    validated_receipt = (
        receipt if type(receipt) is EvidenceReceipt else EvidenceReceipt.model_validate(receipt)
    )
    match = next(
        (
            item
            for item in validated_inventory.records
            if item.receipt == validated_receipt and item.kind is kind
        ),
        None,
    )
    if match is None:
        raise ValueError(
            f"resolved evidence inventory does not accept {kind.value} receipt "
            f"{validated_receipt.record_id}"
        )
    return match


__all__ = [
    "MAX_RECEIPT_IDENTIFIER_LENGTH",
    "MAX_RECEIPT_SCHEMA_VERSION",
    "BoundedReceiptIdentifier",
    "EvidenceReceipt",
    "ResolvedEvidenceInventory",
    "ResolvedEvidenceKind",
    "ResolvedEvidenceRecord",
    "require_resolved_evidence",
    "resolved_evidence_inventory_hash",
    "resolved_evidence_record_hash",
]
