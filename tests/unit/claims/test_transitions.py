from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.claims.transitions import ALLOWED, validate_transition


def test_claim_cannot_skip_evidence_linked() -> None:
    result = validate_transition(ClaimStatus.PROPOSED, ClaimStatus.CORROBORATED)

    assert not result.allowed
    assert result.reason == "illegal claim status transition"


def test_claim_can_be_withdrawn_from_proposed() -> None:
    assert validate_transition(ClaimStatus.PROPOSED, ClaimStatus.WITHDRAWN).allowed


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in ClaimStatus
        for target in ClaimStatus
        if target not in ALLOWED[current]
    ],
)
def test_every_undeclared_transition_is_rejected(
    current: ClaimStatus,
    target: ClaimStatus,
) -> None:
    result = validate_transition(current, target)

    assert not result.allowed
    assert result.reason == "illegal claim status transition"


def test_atomic_claim_collections_are_deeply_immutable() -> None:
    claim = AtomicClaim(
        claim_id="claim-1",
        version=1,
        proposition="The intervention changes the outcome.",
        scope="Controlled laboratory setting",
        population_or_system="Test system",
        epistemic_modality="supports",
        status=ClaimStatus.EVIDENCE_LINKED,
        evidence_links=[EvidenceLink(evidence_id="evidence-1", supporting_span="result")],
        assumptions=["Measurement is calibrated."],
        created_at=datetime.now(UTC),
        created_by="actor-1",
    )

    assert isinstance(claim.evidence_links, tuple)
    assert isinstance(claim.assumptions, tuple)
    with pytest.raises(ValidationError):
        claim.status = ClaimStatus.TESTABLE
    with pytest.raises(ValidationError):
        claim.evidence_links[0].evidence_id = "evidence-2"
