from collections.abc import Mapping

from super_scientist.domain.claims.models import AtomicClaim, EvidenceLink
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.evaluation.claim_drift.models import CheckOutcome, CheckResult


def _check_source_exists(
    link: EvidenceLink,
    evidence_by_id: Mapping[str, EvidenceRecord],
) -> CheckResult:
    evidence = evidence_by_id.get(link.evidence_id)
    if evidence is None or evidence.evidence_id != link.evidence_id:
        return CheckResult(
            code="source_exists",
            outcome=CheckOutcome.FAIL_DETERMINISTIC,
            reason="linked evidence does not exist",
        )
    return CheckResult(
        code="source_exists",
        outcome=CheckOutcome.PASS_DETERMINISTIC,
        reason="linked evidence exists",
    )


def _check_evidence_span_exists(
    link: EvidenceLink,
    evidence_by_id: Mapping[str, EvidenceRecord],
    source_check: CheckResult,
) -> CheckResult:
    if source_check.outcome is not CheckOutcome.PASS_DETERMINISTIC:
        return CheckResult(
            code="evidence_span_exists",
            outcome=CheckOutcome.NOT_APPLICABLE,
            reason="supporting span cannot be checked without linked evidence",
        )
    evidence = evidence_by_id[link.evidence_id]
    if evidence.extracted_span is None or link.supporting_span not in evidence.extracted_span.text:
        return CheckResult(
            code="evidence_span_exists",
            outcome=CheckOutcome.FAIL_DETERMINISTIC,
            reason="supporting span is unavailable in linked evidence",
        )
    return CheckResult(
        code="evidence_span_exists",
        outcome=CheckOutcome.PASS_DETERMINISTIC,
        reason="supporting span exists in linked evidence",
    )


def check_evidence_link(
    link: EvidenceLink,
    evidence_by_id: Mapping[str, EvidenceRecord],
) -> CheckResult:
    source_check = _check_source_exists(link, evidence_by_id)
    if source_check.outcome is not CheckOutcome.PASS_DETERMINISTIC:
        return source_check
    span_check = _check_evidence_span_exists(link, evidence_by_id, source_check)
    if span_check.outcome is not CheckOutcome.PASS_DETERMINISTIC:
        return span_check
    return CheckResult(
        code="evidence_link",
        outcome=CheckOutcome.PASS_DETERMINISTIC,
        reason="linked evidence and exact span exist",
    )


def run_deterministic_checks(
    claim: AtomicClaim,
    evidence_by_id: Mapping[str, EvidenceRecord],
) -> tuple[CheckResult, ...]:
    if not claim.evidence_links:
        return (
            CheckResult(
                code="source_exists",
                outcome=CheckOutcome.FAIL_DETERMINISTIC,
                reason="claim has no evidence links",
            ),
            CheckResult(
                code="evidence_span_exists",
                outcome=CheckOutcome.NOT_APPLICABLE,
                reason="supporting span cannot be checked without evidence links",
            ),
        )

    checks: list[CheckResult] = []
    for link in claim.evidence_links:
        source_check = _check_source_exists(link, evidence_by_id)
        checks.append(source_check)
        checks.append(_check_evidence_span_exists(link, evidence_by_id, source_check))
    return tuple(checks)
