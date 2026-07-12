from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import ValidationError

from super_scientist.kernel.audit.chain import append_event, verify_chain
from super_scientist.kernel.audit.models import AuditEvent

TIMESTAMP = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_audit_chain_verifies_in_order() -> None:
    first = append_event(None, "event-1", "transaction", {"decision": "accepted"}, TIMESTAMP)
    second = append_event(
        first,
        "event-2",
        "transaction",
        {"decision": "rejected"},
        TIMESTAMP + timedelta(seconds=1),
    )

    result = verify_chain([first, second])

    assert result.valid
    assert result.checked_events == 2
    assert result.first_invalid_sequence is None


def test_audit_chain_detects_payload_tampering() -> None:
    event = append_event(None, "event-1", "transaction", {"decision": "accepted"}, TIMESTAMP)
    tampered = event.model_copy(update={"payload": {"decision": "rejected"}})

    result = verify_chain([tampered])

    assert not result.valid
    assert result.checked_events == 1
    assert result.first_invalid_sequence == 1


def test_audit_payload_is_deeply_immutable_and_detached_from_input() -> None:
    payload: dict[str, Any] = {
        "nested": {"items": ["first"], "pair": ("alpha", "beta")},
    }
    event = append_event(None, "event-1", "property", payload, TIMESTAMP)

    payload["nested"]["items"][0] = "changed"
    nested = cast(dict[str, Any], event.payload["nested"])

    assert nested["items"] == ("first",)
    assert nested["pair"] == ("alpha", "beta")
    with pytest.raises(TypeError):
        cast(dict[str, Any], nested)["items"] = ("changed",)
    with pytest.raises(TypeError):
        cast(tuple[str, ...], nested["items"])[0] = "changed"


def test_audit_payload_uses_one_array_semantic_for_lists_and_tuples() -> None:
    list_event = append_event(None, "event-1", "property", {"values": [1, 2]}, TIMESTAMP)
    tuple_event = append_event(None, "event-1", "property", {"values": (1, 2)}, TIMESTAMP)

    assert list_event.payload == tuple_event.payload
    assert list_event.payload_hash == tuple_event.payload_hash
    assert list_event.event_hash == tuple_event.event_hash


@pytest.mark.parametrize("unsupported", [{"alpha", "beta"}, frozenset({"alpha", "beta"})])
def test_audit_payload_rejects_sets_and_frozensets(unsupported: object) -> None:
    with pytest.raises(ValueError, match="audit payload collections"):
        append_event(None, "event-1", "transaction", {"values": unsupported}, TIMESTAMP)


def test_audit_chain_detects_list_to_set_payload_tampering() -> None:
    event = append_event(None, "event-1", "transaction", {"values": ["a", "b"]}, TIMESTAMP)
    tampered = event.model_copy(update={"payload": {"values": {"a", "b"}}})

    result = verify_chain([tampered])

    assert not result.valid
    assert result.checked_events == 1
    assert result.first_invalid_sequence == 1


def test_audit_event_fields_are_frozen() -> None:
    event = append_event(None, "event-1", "transaction", {"ok": True}, TIMESTAMP)

    with pytest.raises(ValidationError):
        event.event_id = "event-2"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("previous_hash", "f" * 64),
        ("sequence", 2),
        ("event_id", "changed-id"),
        ("event_type", "changed-type"),
        ("schema_version", 2),
        ("occurred_at", TIMESTAMP + timedelta(seconds=1)),
        ("payload_hash", "f" * 64),
        ("event_hash", "f" * 64),
    ],
)
def test_audit_chain_detects_event_field_tampering(field: str, value: object) -> None:
    event = append_event(None, "event-1", "transaction", {"ok": True}, TIMESTAMP)
    tampered = event.model_copy(update={field: value})

    result = verify_chain([tampered])

    assert not result.valid
    expected_sequence = value if field == "sequence" else 1
    assert result.first_invalid_sequence == expected_sequence


def test_audit_chain_detects_linkage_and_sequence_tampering() -> None:
    first = append_event(None, "event-1", "transaction", {"ok": True}, TIMESTAMP)
    second = append_event(first, "event-2", "transaction", {"ok": True}, TIMESTAMP)
    tampered = second.model_copy(update={"previous_hash": "f" * 64, "sequence": 3})

    result = verify_chain([first, tampered])

    assert not result.valid
    assert result.checked_events == 2
    assert result.first_invalid_sequence == 3


def test_audit_chain_fails_closed_on_malformed_timestamp() -> None:
    event = append_event(None, "event-1", "transaction", {"ok": True}, TIMESTAMP)
    tampered = event.model_copy(update={"occurred_at": datetime(2026, 7, 11, 12, 0)})

    result = verify_chain([tampered])

    assert not result.valid
    assert result.checked_events == 1
    assert result.reason == "audit event hash or linkage mismatch"


@pytest.mark.parametrize("sequence", [0, -1, False, 1.0, "1"])
def test_audit_chain_uses_checked_ordinal_for_malformed_sequence(sequence: object) -> None:
    event = append_event(None, "event-1", "transaction", {"ok": True}, TIMESTAMP)
    tampered = event.model_copy(update={"sequence": sequence})

    result = verify_chain([tampered])

    assert not result.valid
    assert result.checked_events == 1
    assert result.first_invalid_sequence == 1


def test_audit_chain_reports_ordinal_for_missing_sequence() -> None:
    event = append_event(None, "event-1", "transaction", {"ok": True}, TIMESTAMP)
    malformed = AuditEvent.model_construct(
        event_id=event.event_id,
        event_type=event.event_type,
        schema_version=event.schema_version,
        occurred_at=event.occurred_at,
        payload=event.payload,
        payload_hash=event.payload_hash,
        previous_hash=event.previous_hash,
        event_hash=event.event_hash,
    )

    result = verify_chain([malformed])

    assert not result.valid
    assert result.checked_events == 1
    assert result.first_invalid_sequence == 1


@pytest.mark.parametrize("schema_version", [True, 1.0, "1", 0, 2])
def test_audit_chain_rejects_tampered_schema_version(schema_version: object) -> None:
    event = append_event(None, "event-1", "transaction", {"ok": True}, TIMESTAMP)
    tampered = event.model_copy(update={"schema_version": schema_version})

    result = verify_chain([tampered])

    assert not result.valid
    assert result.first_invalid_sequence == 1


def test_audit_event_sequence_and_schema_version_are_strict_in_model_validation() -> None:
    event = append_event(None, "event-1", "transaction", {"ok": True}, TIMESTAMP)
    data = event.model_dump(mode="json")

    with pytest.raises(ValidationError):
        AuditEvent.model_validate({**data, "sequence": True})
    with pytest.raises(ValidationError):
        AuditEvent.model_validate({**data, "schema_version": 1.0})


def test_audit_event_json_round_trip_preserves_chain_verification() -> None:
    event = append_event(
        None,
        "event-1",
        "transaction",
        {"nested": {"values": [1, 2], "label": "accepted"}},
        TIMESTAMP,
    )

    restored = AuditEvent.model_validate_json(event.model_dump_json())

    assert restored == event
    assert restored.payload["nested"] == {"values": (1, 2), "label": "accepted"}
    assert verify_chain([restored]).valid


def test_audit_chain_accepts_no_events() -> None:
    result = verify_chain([])

    assert result.valid
    assert result.checked_events == 0
    assert result.first_invalid_sequence is None
    assert result.reason is None


@pytest.mark.parametrize("unsupported", [object(), float("nan"), bytearray(b"mutable")])
def test_audit_payload_rejects_non_canonical_values(unsupported: object) -> None:
    with pytest.raises(ValueError, match="unsupported audit payload value"):
        append_event(None, "event-1", "transaction", {"value": unsupported}, TIMESTAMP)
