from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.claims.transitions import ALLOWED, validate_transition


def _claim(**updates: object) -> AtomicClaim:
    values: dict[str, object] = {
        "claim_id": "claim-1",
        "version": 1,
        "proposition": "The intervention changes the outcome.",
        "scope": "Controlled laboratory setting",
        "population_or_system": "Test system",
        "epistemic_modality": "supports",
        "status": ClaimStatus.PROPOSED,
        "created_at": datetime.now(UTC),
        "created_by": "actor-1",
    }
    values.update(updates)
    return AtomicClaim(**values)


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
    claim = _claim(
        status=ClaimStatus.EVIDENCE_LINKED,
        evidence_links=[EvidenceLink(evidence_id="evidence-1", supporting_span="result")],
        assumptions=["Measurement is calibrated."],
    )

    assert isinstance(claim.evidence_links, tuple)
    assert isinstance(claim.assumptions, tuple)
    with pytest.raises(ValidationError):
        claim.status = ClaimStatus.TESTABLE
    with pytest.raises(ValidationError):
        claim.evidence_links[0].evidence_id = "evidence-2"


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_claim_version_rejects_coerced_values(version: object) -> None:
    with pytest.raises(ValidationError, match="version"):
        _claim(version=version)


def test_version_one_rejects_unexpected_parent_version_id() -> None:
    with pytest.raises(ValidationError, match="version 1"):
        _claim(parent_version_id="claim-1:0")


def test_later_version_requires_parent_version_id() -> None:
    with pytest.raises(ValidationError, match="parent_version_id"):
        _claim(version=2)


def test_later_version_rejects_wrong_parent_version_id() -> None:
    with pytest.raises(ValidationError, match="parent_version_id"):
        _claim(version=2, parent_version_id="claim-1:2")


def test_later_version_accepts_immediately_preceding_parent_version_id() -> None:
    claim = _claim(version=2, parent_version_id="claim-1:1")

    assert claim.parent_version_id == "claim-1:1"


@pytest.mark.parametrize(
    "status",
    [
        status
        for status in ClaimStatus
        if status not in {ClaimStatus.PROPOSED, ClaimStatus.WITHDRAWN}
    ],
)
def test_non_draft_and_non_withdrawn_claims_require_evidence_links(status: ClaimStatus) -> None:
    with pytest.raises(ValidationError, match="evidence link"):
        _claim(status=status)


@pytest.mark.parametrize("status", [ClaimStatus.PROPOSED, ClaimStatus.WITHDRAWN])
def test_proposed_and_withdrawn_claims_allow_no_evidence_links(status: ClaimStatus) -> None:
    claim = _claim(status=status)

    assert claim.evidence_links == ()


def test_claim_rejects_duplicate_exact_evidence_links() -> None:
    link = EvidenceLink(evidence_id="evidence-1", supporting_span="result")

    with pytest.raises(ValidationError, match="duplicate evidence links"):
        _claim(evidence_links=(link, link))


def test_claim_allows_distinct_evidence_link_pairs() -> None:
    claim = _claim(
        evidence_links=(
            EvidenceLink(evidence_id="evidence-1", supporting_span="first result"),
            EvidenceLink(evidence_id="evidence-1", supporting_span="second result"),
        )
    )

    assert len(claim.evidence_links) == 2
