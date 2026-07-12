from collections.abc import Mapping

from super_scientist.domain.claims.models import AtomicClaim, EvidenceLink
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.evaluation.claim_drift.models import CheckOutcome, CheckResult


def check_evidence_link(
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
    if evidence.extracted_span is None or link.supporting_span not in evidence.extracted_span.text:
        return CheckResult(
            code="evidence_span_exists",
            outcome=CheckOutcome.FAIL_DETERMINISTIC,
            reason="supporting span is unavailable in linked evidence",
        )
    return CheckResult(
        code="evidence_link",
        outcome=CheckOutcome.PASS_DETERMINISTIC,
        reason="linked evidence and exact span exist",
    )


def run_deterministic_checks(
    claim: AtomicClaim,
    evidence_by_id: Mapping[str, EvidenceRecord],
) -> tuple[CheckResult, ...]:
    checks = tuple(check_evidence_link(link, evidence_by_id) for link in claim.evidence_links)
    if not checks:
        return (
            CheckResult(
                code="source_exists",
                outcome=CheckOutcome.FAIL_DETERMINISTIC,
                reason="claim has no evidence links",
            ),
        )
    return checks
