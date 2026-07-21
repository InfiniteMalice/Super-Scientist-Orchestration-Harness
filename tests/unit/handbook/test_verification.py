from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from conftest import behavior_entry, manifest, source_binding

from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.handbook import (
    build_handbook,
    create_verification_record,
    verify_handbook,
)
from super_scientist.providers.storage.domain_records import HandbookVerificationRecord


def test_missing_symbol_fails_verification(repository_root: Path) -> None:
    declared = manifest(
        repository_root,
        behavior_entry(
            repository_root,
            bindings=(source_binding(repository_root, symbol="missing_symbol"),),
        ),
    )
    result = verify_handbook(repository_root, declared)
    assert result.valid is False
    assert "SYMBOL_NOT_FOUND" in result.finding_codes
    assert result.missing_symbols == ("src/sample.py:missing_symbol",)
    assert result.affected_behavior_ids == ("behavior-alpha",)


def test_source_change_marks_behavior_stale_and_identifies_rules(repository_root: Path) -> None:
    declared = manifest(repository_root)
    built = build_handbook(repository_root, declared)
    source = repository_root / "src" / "sample.py"
    source.write_text(source.read_text(encoding="utf-8") + "\nCHANGED = True\n", encoding="utf-8")

    result = verify_handbook(repository_root, declared)
    assert result.valid is False
    assert built.source_tree_hash != result.actual_source_tree_hash
    assert result.stale_locations == ("src/sample.py:public_function",)
    assert result.affected_behavior_ids == ("behavior-alpha",)
    assert result.affected_rule_version_ids == ("rule-version-alpha",)
    assert "SOURCE_HASH_MISMATCH" in result.finding_codes


def test_repository_commit_mismatch_is_a_stable_finding(repository_root: Path) -> None:
    result = verify_handbook(
        repository_root,
        manifest(repository_root),
        repository_commit="2" * 40,
    )
    assert result.valid is False
    assert result.finding_codes == ("REPOSITORY_COMMIT_MISMATCH",)


def test_modified_generated_artifacts_fail_exact_verification(repository_root: Path) -> None:
    declared = manifest(repository_root)
    built = build_handbook(repository_root, declared)

    changed_json = built.json_bytes.replace(b"Human-authored", b"Machine-inferred")
    result = verify_handbook(
        repository_root,
        declared,
        expected_json_bytes=changed_json,
        expected_markdown_bytes=built.markdown_bytes,
    )
    assert result.valid is False
    assert "GENERATED_ARTIFACT_MISMATCH" in result.finding_codes


def test_unmodified_generated_artifacts_verify_exactly(repository_root: Path) -> None:
    declared = manifest(repository_root)
    built = build_handbook(repository_root, declared)
    result = verify_handbook(
        repository_root,
        declared,
        expected_json_bytes=built.json_bytes,
        expected_markdown_bytes=built.markdown_bytes,
    )
    assert result.valid is True
    assert result.finding_codes == ()
    assert result.generated_artifact_hash == built.generated_artifact_hash
    assert result.actual_source_tree_hash == built.source_tree_hash


def test_verification_result_converts_to_exact_task_13_storage_contract(
    repository_root: Path,
) -> None:
    declared = manifest(repository_root)
    result = verify_handbook(repository_root, declared)
    record = create_verification_record(
        result,
        verification_id="handbook-verification-fixture",
        verified_at=datetime(2026, 7, 20, tzinfo=UTC),
        governing_policy_hash="f" * 64,
    )

    assert type(record) is HandbookVerificationRecord
    assert record.repository_commit == declared.repository_commit
    assert record.source_hashes == result.source_hashes
    assert record.outcome is AssessmentOutcome.PASSED
    assert record.manifest_hash == result.manifest_hash
    assert record.generated_artifact_hash == result.generated_artifact_hash


def test_failed_verification_converts_to_failed_append_only_record(repository_root: Path) -> None:
    declared = manifest(
        repository_root,
        behavior_entry(
            repository_root,
            bindings=(source_binding(repository_root, symbol="absent"),),
        ),
    )
    result = verify_handbook(repository_root, declared)
    record = create_verification_record(
        result,
        verification_id="handbook-verification-failed",
        verified_at=datetime(2026, 7, 20, tzinfo=UTC),
        governing_policy_hash="f" * 64,
    )
    assert record.outcome is AssessmentOutcome.FAILED
    assert record.missing_symbols == ("src/sample.py:absent",)


def test_missing_source_and_test_are_reported_without_becoming_behavioral_truth(
    repository_root: Path,
) -> None:
    missing_source = source_binding(
        repository_root,
        path="src/missing.py",
        source_hash="e" * 64,
    )
    declared = manifest(
        repository_root,
        behavior_entry(
            repository_root,
            bindings=(missing_source,),
            tests=("tests/missing_test.py",),
        ),
    )
    result = verify_handbook(repository_root, declared)
    assert result.valid is False
    assert result.finding_codes == ("SOURCE_NOT_FOUND", "TEST_NOT_FOUND")
    assert result.affected_behavior_ids == ("behavior-alpha",)


def test_invalid_python_is_a_fixed_syntax_finding(repository_root: Path) -> None:
    source = repository_root / "src" / "sample.py"
    source.write_text("def broken(:\n", encoding="utf-8")
    declared = manifest(
        repository_root,
        behavior_entry(
            repository_root,
            bindings=(
                source_binding(
                    repository_root,
                    source_hash="0" * 64,
                ),
            ),
        ),
    )
    result = verify_handbook(repository_root, declared)
    assert result.valid is False
    assert "SOURCE_SYNTAX_ERROR" in result.finding_codes
    assert all("broken" not in finding.message for finding in result.findings)
