from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from super_scientist.handbook import (
    BehaviorManifest,
    build_handbook,
    create_verification_record,
    manifest_schema_bytes,
    verify_handbook,
)
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.domain_records import HandbookVerificationRepository

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HANDBOOK_ROOT = REPOSITORY_ROOT / "docs" / "handbook"


def _repository_manifest() -> BehaviorManifest:
    return BehaviorManifest.model_validate_json((HANDBOOK_ROOT / "behaviors.json").read_bytes())


def test_repository_handbook_build_is_reproducible_and_source_controlled() -> None:
    declared = _repository_manifest()
    first = build_handbook(REPOSITORY_ROOT, declared)
    second = build_handbook(REPOSITORY_ROOT, declared)

    assert first.json_bytes == second.json_bytes == (HANDBOOK_ROOT / "handbook.json").read_bytes()
    assert (
        first.markdown_bytes
        == second.markdown_bytes
        == (HANDBOOK_ROOT / "handbook.md").read_bytes()
    )
    assert manifest_schema_bytes() == (HANDBOOK_ROOT / "manifest.schema.json").read_bytes()


def test_repository_handbook_verifies_exact_source_snapshot_and_artifacts() -> None:
    declared = _repository_manifest()
    result = verify_handbook(
        REPOSITORY_ROOT,
        declared,
        repository_commit=declared.repository_commit,
        expected_json_bytes=(HANDBOOK_ROOT / "handbook.json").read_bytes(),
        expected_markdown_bytes=(HANDBOOK_ROOT / "handbook.md").read_bytes(),
    )
    assert result.valid is True
    assert result.findings == ()
    assert result.stale_locations == ()
    assert result.missing_symbols == ()


def test_manifest_commit_is_a_real_git_commit_and_bound_sources_match_worktree() -> None:
    declared = _repository_manifest()
    subprocess.run(
        ("git", "cat-file", "-e", f"{declared.repository_commit}^{{commit}}"),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    for behavior in declared.behaviors:
        for binding in behavior.source_bindings:
            assert binding.repository_commit == declared.repository_commit
            completed = subprocess.run(
                (
                    "git",
                    "diff",
                    "--quiet",
                    declared.repository_commit,
                    "--",
                    binding.relative_path,
                ),
                cwd=REPOSITORY_ROOT,
                capture_output=True,
            )
            assert completed.returncode == 0
            committed = subprocess.run(
                ("git", "show", f"{declared.repository_commit}:{binding.relative_path}"),
                cwd=REPOSITORY_ROOT,
                check=True,
                capture_output=True,
            )
            worktree_bytes = (REPOSITORY_ROOT / binding.relative_path).read_bytes()
            assert committed.stdout == worktree_bytes.replace(b"\r\n", b"\n")


def test_repository_handbook_exposes_reverse_behavior_and_rule_navigation() -> None:
    document = json.loads((HANDBOOK_ROOT / "handbook.json").read_bytes())
    declared = _repository_manifest()
    declared_ids = {behavior.behavior_id for behavior in declared.behaviors}

    linked_ids = {
        behavior_id
        for source_link in document["source_to_behaviors"]
        for behavior_id in source_link["behavior_ids"]
    }
    rule_linked_ids = {
        behavior_id
        for rule_link in document["rule_to_behaviors"]
        for behavior_id in rule_link["behavior_ids"]
    }
    assert linked_ids == declared_ids
    assert rule_linked_ids == declared_ids


def test_repository_handbook_verification_record_round_trips_through_0006(
    tmp_path: Path,
) -> None:
    declared = _repository_manifest()
    result = verify_handbook(
        REPOSITORY_ROOT,
        declared,
        repository_commit=declared.repository_commit,
    )
    record = create_verification_record(
        result,
        verification_id="repository-handbook-verification",
        verified_at=datetime(2026, 7, 20, tzinfo=UTC),
        governing_policy_hash="f" * 64,
    )
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'handbook.sqlite3').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    try:
        with engine.begin() as connection:
            repository = HandbookVerificationRepository(connection)
            repository.add(record.verification_id, record, record.verified_at)
            assert repository.get(record.verification_id) == record
    finally:
        engine.dispose()


def test_human_handbook_document_states_derived_authority_and_failure_modes() -> None:
    documentation = (REPOSITORY_ROOT / "docs" / "behavior-handbook.md").read_text(encoding="utf-8")
    for statement in (
        "derived index",
        "human-authored",
        "does not infer behavioral truth",
        "source, tests, governance policy, and active rules remain authoritative",
        "stale",
        "symlink",
    ):
        assert statement in documentation
