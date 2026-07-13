from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from super_scientist.kernel.audit.chain import append_event, verify_chain


@given(st.dictionaries(st.text(min_size=1), st.integers(), min_size=1, max_size=8))
def test_payload_mutation_breaks_chain(payload: dict[str, int]) -> None:
    event = append_event(None, "property", payload, datetime.now(UTC))
    changed = dict(payload)
    first_key = next(iter(changed))
    changed[first_key] += 1
    tampered = event.model_copy(update={"payload": changed})

    assert not verify_chain([tampered]).valid


@given(st.integers(min_value=1, max_value=1000))
def test_sequence_tampering_breaks_chain(sequence: int) -> None:
    event = append_event(None, "property", {"value": 1}, datetime.now(UTC))
    tampered = event.model_copy(update={"sequence": sequence + 1})

    assert not verify_chain([tampered]).valid
