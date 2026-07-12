from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, NewType

from pydantic import AfterValidator

EvidenceId = NewType("EvidenceId", str)
ClaimId = NewType("ClaimId", str)
TransactionId = NewType("TransactionId", str)
ActorId = NewType("ActorId", str)


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


UtcTimestamp = Annotated[datetime, AfterValidator(require_utc)]


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
