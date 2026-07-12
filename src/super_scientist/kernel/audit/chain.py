from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from super_scientist.domain.primitives import (
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.kernel.audit.models import (
    GENESIS_HASH,
    AuditEvent,
    AuditVerification,
    freeze_json_mapping,
)


def _canonical_json_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, frozenset):
        values = [_canonical_json_value(item) for item in value]
        return sorted(values, key=canonical_json_bytes)
    return value


def _payload_hash(payload: Mapping[str, object]) -> str:
    canonical_payload = _canonical_json_value(payload)
    return sha256_hex(canonical_json_bytes(canonical_payload))


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
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "payload_hash": payload_hash,
        "previous_hash": previous_hash,
    }
    return AuditEvent(
        sequence=sequence,
        event_id=event_id,
        event_type=event_type,
        schema_version=1,
        occurred_at=occurred_at,
        payload=frozen_payload,
        payload_hash=payload_hash,
        previous_hash=previous_hash,
        event_hash=sha256_hex(canonical_json_bytes(envelope)),
    )


def _invalid_verification(event: AuditEvent, checked: int) -> AuditVerification:
    sequence = (
        event.sequence
        if isinstance(event.sequence, int) and not isinstance(event.sequence, bool)
        else checked
    )
    return AuditVerification(
        valid=False,
        checked_events=checked,
        first_invalid_sequence=sequence,
        reason="audit event hash or linkage mismatch",
    )


def verify_chain(events: Iterable[AuditEvent]) -> AuditVerification:
    previous: AuditEvent | None = None
    checked = 0
    for event in events:
        checked += 1
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
