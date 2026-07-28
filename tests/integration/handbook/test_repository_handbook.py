from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from super_scientist.handbook import (
    BehaviorEntry,
    BehaviorManifest,
    SourceBinding,
    build_handbook,
    create_verification_record,
    manifest_schema_bytes,
    verify_handbook,
)
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.domain_records import HandbookVerificationRepository

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HANDBOOK_ROOT = REPOSITORY_ROOT / "docs" / "handbook"


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


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
        (
            "git",
            "-c",
            f"safe.directory={REPOSITORY_ROOT}",
            "cat-file",
            "-e",
            f"{declared.repository_commit}^{{commit}}",
        ),
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
                    "-c",
                    f"safe.directory={REPOSITORY_ROOT}",
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
                (
                    "git",
                    "-c",
                    f"safe.directory={REPOSITORY_ROOT}",
                    "show",
                    f"{declared.repository_commit}:{binding.relative_path}",
                ),
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


def test_fresh_autocrlf_clone_regenerates_byte_identical_handbook_artifacts(
    tmp_path: Path,
) -> None:
    attributes_path = REPOSITORY_ROOT / ".gitattributes"
    attributes = attributes_path.read_text(encoding="utf-8")
    for declaration in (
        "docs/handbook/*.json text eol=lf",
        "docs/handbook/*.md text eol=lf",
        "docs/behavior-handbook.md text eol=lf",
    ):
        assert declaration in attributes

    seed = tmp_path / "seed"
    (seed / "src").mkdir(parents=True)
    (seed / "tests").mkdir()
    (seed / "docs" / "handbook").mkdir(parents=True)
    source_bytes = b"def declared(value: int) -> int:\n    return value + 1\n"
    (seed / "src" / "sample.py").write_bytes(source_bytes)
    (seed / "tests" / "test_sample.py").write_bytes(
        b"def test_sample() -> None:\n    assert True\n"
    )
    (seed / ".gitattributes").write_bytes(attributes_path.read_bytes())
    _git(seed, "init", "--quiet")
    _git(seed, "config", "user.name", "Handbook Fixture")
    _git(seed, "config", "user.email", "handbook@example.invalid")
    _git(seed, "add", ".gitattributes", "src/sample.py", "tests/test_sample.py")
    _git(seed, "commit", "--quiet", "-m", "source snapshot")
    commit = _git(seed, "rev-parse", "HEAD").decode("ascii").strip()
    declared = BehaviorManifest(
        repository="fresh-clone-fixture",
        repository_commit=commit,
        behaviors=(
            BehaviorEntry(
                behavior_id="fresh-clone-behavior",
                summary="Artifacts remain byte-identical across checkout policy.",
                contracts=("Generated files always use LF bytes.",),
                inputs=(),
                outputs=(),
                preconditions=(),
                postconditions=(),
                failure_modes=(),
                state_read=(),
                state_written=(),
                tools=("Git.",),
                permissions=("Repository read only.",),
                dependencies=(),
                governing_rule_version_ids=("rule-portable-rendering-v1",),
                source_bindings=(
                    SourceBinding(
                        repository_commit=commit,
                        relative_path="src/sample.py",
                        symbol="declared",
                        source_hash=hashlib.sha256(source_bytes).hexdigest(),
                    ),
                ),
                test_paths=("tests/test_sample.py",),
                related_behaviors=(),
            ),
        ),
    )
    built = build_handbook(seed, declared)
    generated = {
        "docs/handbook/behaviors.json": (
            declared.model_dump_json(indent=2).encode("utf-8") + b"\n"
        ),
        "docs/handbook/manifest.schema.json": manifest_schema_bytes(),
        "docs/handbook/handbook.json": built.json_bytes,
        "docs/handbook/handbook.md": built.markdown_bytes,
        "docs/behavior-handbook.md": b"# Behavior handbook\n\nGenerated artifacts use LF.\n",
    }
    for relative_path, contents in generated.items():
        (seed / relative_path).write_bytes(contents)
    _git(seed, "add", "docs")
    _git(seed, "commit", "--quiet", "-m", "generated handbook")

    clone = tmp_path / "autocrlf-clone"
    _git(
        tmp_path,
        "-c",
        "core.autocrlf=true",
        "clone",
        "--quiet",
        "--no-hardlinks",
        str(seed),
        str(clone),
    )
    cloned_manifest = BehaviorManifest.model_validate_json(
        (clone / "docs" / "handbook" / "behaviors.json").read_bytes()
    )
    cloned_build = build_handbook(clone, cloned_manifest)

    assert cloned_build.json_bytes == (clone / "docs" / "handbook" / "handbook.json").read_bytes()
    assert cloned_build.markdown_bytes == (clone / "docs" / "handbook" / "handbook.md").read_bytes()
    assert (
        manifest_schema_bytes()
        == (clone / "docs" / "handbook" / "manifest.schema.json").read_bytes()
    )
    for relative_path, contents in generated.items():
        assert (clone / relative_path).read_bytes() == contents
