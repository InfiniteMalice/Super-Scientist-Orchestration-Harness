from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from super_scientist.domain.primitives import Sha256Hex, StableIdentifier

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


__all__ = [
    "MAX_RECEIPT_IDENTIFIER_LENGTH",
    "MAX_RECEIPT_SCHEMA_VERSION",
    "BoundedReceiptIdentifier",
    "EvidenceReceipt",
]
