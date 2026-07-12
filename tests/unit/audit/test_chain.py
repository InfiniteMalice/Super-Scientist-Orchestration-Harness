from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import ValidationError

from super_scientist.kernel.audit.chain import append_event, verify_chain

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
        "nested": {"items": ["first"], "tags": {"alpha", "beta"}},
    }
    event = append_event(None, "event-1", "property", payload, TIMESTAMP)

    payload["nested"]["items"][0] = "changed"
    nested = cast(dict[str, Any], event.payload["nested"])

    assert nested["items"] == ("first",)
    assert nested["tags"] == frozenset({"alpha", "beta"})
    with pytest.raises(TypeError):
        cast(dict[str, Any], nested)["items"] = ("changed",)
    with pytest.raises(TypeError):
        cast(tuple[str, ...], nested["items"])[0] = "changed"


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
