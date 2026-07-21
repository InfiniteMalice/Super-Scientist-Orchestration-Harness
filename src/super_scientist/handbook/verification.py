from __future__ import annotations

from datetime import datetime
from pathlib import Path

from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.handbook.builder import (
    _artifact_hash,
    _inspect_manifest,
    _manifest_hash,
    _render_build,
)
from super_scientist.handbook.models import (
    BehaviorManifest,
    HandbookFinding,
    HandbookFindingCode,
    HandbookVerificationResult,
)
from super_scientist.providers.storage.domain_records import HandbookVerificationRecord


def verify_handbook(
    repository_root: Path,
    manifest: BehaviorManifest,
    *,
    repository_commit: str | None = None,
    expected_json_bytes: bytes | None = None,
    expected_markdown_bytes: bytes | None = None,
) -> HandbookVerificationResult:
    """Verify declared source facts and, when supplied, exact generated projections."""

    effective_commit = repository_commit or manifest.repository_commit
    inspection = _inspect_manifest(repository_root, manifest, effective_commit)
    findings = list(inspection.findings)
    total_bindings = sum(len(behavior.source_bindings) for behavior in manifest.behaviors)
    can_render = len(inspection.source_locations) == total_bindings
    if can_render:
        built = _render_build(manifest, inspection)
        artifact_hash = built.generated_artifact_hash
        supplied_artifacts = expected_json_bytes is not None or expected_markdown_bytes is not None
        artifacts_match = (
            expected_json_bytes == built.json_bytes
            and expected_markdown_bytes == built.markdown_bytes
        )
        if supplied_artifacts and not artifacts_match:
            findings.append(
                HandbookFinding(
                    code=HandbookFindingCode.GENERATED_ARTIFACT_MISMATCH,
                    message="generated handbook artifacts are not byte-identical",
                    behavior_id=None,
                    location=None,
                )
            )
    else:
        artifact_hash = _artifact_hash(
            expected_json_bytes or b"",
            expected_markdown_bytes or b"",
        )

    retained_findings = _deduplicate(findings)
    affected_behavior_ids = _affected_behaviors(manifest, retained_findings)
    affected_rule_version_ids = tuple(
        sorted(
            {
                rule_version_id
                for behavior in manifest.behaviors
                if behavior.behavior_id in affected_behavior_ids
                for rule_version_id in behavior.governing_rule_version_ids
            }
        )
    )
    stale_locations = tuple(
        sorted(
            {
                finding.location
                for finding in retained_findings
                if finding.code is HandbookFindingCode.SOURCE_HASH_MISMATCH
                and finding.location is not None
            }
        )
    )
    missing_symbols = tuple(
        sorted(
            {
                finding.location
                for finding in retained_findings
                if finding.code is HandbookFindingCode.SYMBOL_NOT_FOUND
                and finding.location is not None
            }
        )
    )
    return HandbookVerificationResult(
        valid=not retained_findings,
        repository_commit=manifest.repository_commit,
        manifest_hash=_manifest_hash(manifest),
        expected_source_tree_hash=inspection.expected_source_tree_hash,
        actual_source_tree_hash=inspection.actual_source_tree_hash,
        source_hashes=inspection.source_hashes,
        generated_artifact_hash=artifact_hash,
        findings=retained_findings,
        finding_codes=tuple(finding.code for finding in retained_findings),
        stale_locations=stale_locations,
        missing_symbols=missing_symbols,
        affected_behavior_ids=affected_behavior_ids,
        affected_rule_version_ids=affected_rule_version_ids,
    )


def create_verification_record(
    result: HandbookVerificationResult,
    *,
    verification_id: str,
    verified_at: datetime,
    governing_policy_hash: str,
) -> HandbookVerificationRecord:
    """Project a verification result into Task 13's canonical append-only record."""

    return HandbookVerificationRecord(
        verification_id=verification_id,
        manifest_hash=result.manifest_hash,
        repository_commit=result.repository_commit,
        source_hashes=result.source_hashes,
        generated_artifact_hash=result.generated_artifact_hash,
        stale_locations=result.stale_locations,
        missing_symbols=result.missing_symbols,
        outcome=AssessmentOutcome.PASSED if result.valid else AssessmentOutcome.FAILED,
        verified_at=verified_at,
        governing_policy_hash=governing_policy_hash,
    )


def _deduplicate(findings: list[HandbookFinding]) -> tuple[HandbookFinding, ...]:
    retained: list[HandbookFinding] = []
    seen: set[tuple[HandbookFindingCode, str | None, str | None]] = set()
    for finding in findings:
        identity = (finding.code, finding.behavior_id, finding.location)
        if identity not in seen:
            seen.add(identity)
            retained.append(finding)
    return tuple(retained)


def _affected_behaviors(
    manifest: BehaviorManifest,
    findings: tuple[HandbookFinding, ...],
) -> tuple[str, ...]:
    explicit = {finding.behavior_id for finding in findings if finding.behavior_id is not None}
    if any(
        finding.code
        in {
            HandbookFindingCode.REPOSITORY_COMMIT_MISMATCH,
            HandbookFindingCode.GENERATED_ARTIFACT_MISMATCH,
        }
        for finding in findings
    ):
        explicit.update(behavior.behavior_id for behavior in manifest.behaviors)
    return tuple(sorted(explicit))


__all__ = ["create_verification_record", "verify_handbook"]
