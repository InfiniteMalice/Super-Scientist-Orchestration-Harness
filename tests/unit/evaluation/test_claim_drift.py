from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, EvidenceSpan
from super_scientist.evaluation.claim_drift.deterministic import (
    check_evidence_link,
    run_deterministic_checks,
)
from super_scientist.evaluation.claim_drift.models import CheckOutcome, CheckResult


def _evidence_record(
    extracted_text: str | None = None,
    evidence_id: str = "evidence-1",
) -> EvidenceRecord:
    span = None
    if extracted_text is not None:
        span = EvidenceSpan(start=0, end=len(extracted_text), text=extracted_text)
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="document",
        source_locator="fixture://evidence-1",
        retrieved_at=datetime.now(UTC),
        artifact=ArtifactRef(
            sha256="a" * 64,
            size_bytes=0,
            media_type="text/plain",
            relative_path=f"sha256/aa/{'a' * 64}",
        ),
        extracted_span=span,
        provenance={"collector": "test"},
        ingestion_actor_id="actor-1",
    )


def _claim(evidence_links: tuple[EvidenceLink, ...] = ()) -> AtomicClaim:
    return AtomicClaim(
        claim_id="claim-1",
        version=1,
        proposition="The intervention changes the outcome.",
        scope="Controlled laboratory setting",
        population_or_system="Test system",
        epistemic_modality="supports",
        status=ClaimStatus.PROPOSED,
        evidence_links=evidence_links,
        created_at=datetime.now(UTC),
        created_by="actor-1",
    )


def test_missing_evidence_fails_deterministically() -> None:
    link = EvidenceLink(evidence_id="missing", supporting_span="span")

    result = check_evidence_link(link, evidence_by_id={})

    assert result.outcome is CheckOutcome.FAIL_DETERMINISTIC
    assert result.code == "source_exists"


def test_missing_supporting_span_fails_deterministically() -> None:
    link = EvidenceLink(evidence_id="evidence-1", supporting_span="result")

    result = check_evidence_link(link, {"evidence-1": _evidence_record("other text")})

    assert result.outcome is CheckOutcome.FAIL_DETERMINISTIC
    assert result.code == "evidence_span_exists"


def test_mismatched_mapping_key_and_evidence_id_fails_source_check() -> None:
    link = EvidenceLink(evidence_id="evidence-1", supporting_span="result")

    result = check_evidence_link(
        link,
        {"evidence-1": _evidence_record("result", evidence_id="different-evidence")},
    )

    assert result.outcome is CheckOutcome.FAIL_DETERMINISTIC
    assert result.code == "source_exists"


def test_exact_evidence_span_passes_deterministically() -> None:
    link = EvidenceLink(evidence_id="evidence-1", supporting_span="observed result")

    result = check_evidence_link(
        link,
        {"evidence-1": _evidence_record("the observed result was stable")},
    )

    assert result.outcome is CheckOutcome.PASS_DETERMINISTIC
    assert result.code == "evidence_link"


def test_claim_without_evidence_links_fails_source_check() -> None:
    results = run_deterministic_checks(_claim(), {})

    assert results == (
        CheckResult(
            code="source_exists",
            outcome=CheckOutcome.FAIL_DETERMINISTIC,
            reason="claim has no evidence links",
        ),
    )


def test_deterministic_checks_do_not_certify_scope_or_modality() -> None:
    claim = _claim((EvidenceLink(evidence_id="evidence-1", supporting_span="result"),))

    results = run_deterministic_checks(claim, {"evidence-1": _evidence_record("result")})

    assert all(result.code not in {"scope", "epistemic_modality"} for result in results)
    assert all(result.outcome is not CheckOutcome.REQUIRES_INDEPENDENT_REVIEW for result in results)


def test_check_results_are_immutable() -> None:
    result = CheckResult(
        code="source_exists",
        outcome=CheckOutcome.PASS_DETERMINISTIC,
        reason="linked evidence exists",
    )

    with pytest.raises(ValidationError):
        result.code = "other"
