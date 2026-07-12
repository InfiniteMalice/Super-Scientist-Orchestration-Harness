from hypothesis import given
from hypothesis import strategies as st

from super_scientist.domain.claims.models import ClaimStatus
from super_scientist.domain.claims.transitions import ALLOWED, validate_transition


@given(st.sampled_from(list(ClaimStatus)), st.sampled_from(list(ClaimStatus)))
def test_transition_policy_matches_declared_graph(
    current: ClaimStatus,
    target: ClaimStatus,
) -> None:
    assert validate_transition(current, target).allowed is (target in ALLOWED[current])
