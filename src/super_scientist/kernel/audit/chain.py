from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from super_scientist.domain.primitives import (
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.kernel.audit.models import (
    AUDIT_SCHEMA_VERSION,
    GENESIS_HASH,
    AuditEvent,
    AuditVerification,
    FrozenJsonMapping,
    freeze_json_mapping,
    json_compatible_payload,
)


def _payload_hash(payload: FrozenJsonMapping) -> str:
    return sha256_hex(canonical_json_bytes(json_compatible_payload(payload)))


def append_event(
    previous: AuditEvent | None,
    event_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    occurred_at: UtcTimestamp,
) -> AuditEvent:
    frozen_payload = freeze_json_mapping(payload)
    sequence = 1 if previous is None else previous.sequence + 1
    previous_hash = GENESIS_HASH if previous is None else previous.event_hash
    payload_hash = _payload_hash(frozen_payload)
    envelope = {
        "sequence": sequence,
        "event_id": event_id,
        "event_type": event_type,
        "schema_version": AUDIT_SCHEMA_VERSION,
        "occurred_at": occurred_at.isoformat(),
        "payload_hash": payload_hash,
        "previous_hash": previous_hash,
    }
    return AuditEvent(
        sequence=sequence,
        event_id=event_id,
        event_type=event_type,
        schema_version=AUDIT_SCHEMA_VERSION,
        occurred_at=occurred_at,
        payload=frozen_payload,
        payload_hash=payload_hash,
        previous_hash=previous_hash,
        event_hash=sha256_hex(canonical_json_bytes(envelope)),
    )


def _invalid_verification(event: object, checked: int) -> AuditVerification:
    candidate = event.sequence if isinstance(event, AuditEvent) else None
    sequence = candidate if type(candidate) is int and candidate >= 1 else checked
    return AuditVerification(
        valid=False,
        checked_events=checked,
        first_invalid_sequence=sequence,
        reason="audit event hash or linkage mismatch",
    )


def _has_valid_replay_metadata(event: object) -> bool:
    if not isinstance(event, AuditEvent):
        return False
    try:
        return (
            type(event.sequence) is int
            and event.sequence >= 1
            and type(event.schema_version) is int
            and event.schema_version == AUDIT_SCHEMA_VERSION
        )
    except Exception:
        return False


def verify_chain(events: Iterable[AuditEvent]) -> AuditVerification:
    previous: AuditEvent | None = None
    checked = 0
    for event in events:
        checked += 1
        if not _has_valid_replay_metadata(event):
            return _invalid_verification(event, checked)
        try:
            expected = append_event(
                previous,
                event.event_id,
                event.event_type,
                event.payload,
                event.occurred_at,
            )
        except Exception:
            return _invalid_verification(event, checked)
        if expected != event:
            return _invalid_verification(event, checked)
        previous = event
    return AuditVerification(valid=True, checked_events=checked)
