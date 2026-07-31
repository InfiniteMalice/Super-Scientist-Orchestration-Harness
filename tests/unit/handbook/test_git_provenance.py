from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from super_scientist.handbook import (
    BehaviorEntry,
    BehaviorManifest,
    SourceBinding,
    create_verification_record,
    verify_handbook,
)


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample.py").write_bytes(
        b"def public_function(value: int) -> int:\n    return value + 1\n"
    )
    (root / "tests" / "test_sample.py").write_bytes(
        b"def test_sample() -> None:\n    assert True\n"
    )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Handbook Fixture")
    _git(root, "config", "user.email", "handbook@example.invalid")
    _git(root, "add", "src/sample.py", "tests/test_sample.py")
    _git(root, "commit", "--quiet", "-m", "fixture snapshot")
    return root, _git(root, "rev-parse", "HEAD").decode("ascii").strip()


def _manifest(
    root: Path, repository_commit: str, source_hash: str | None = None
) -> BehaviorManifest:
    binding = SourceBinding(
        repository_commit=repository_commit,
        relative_path="src/sample.py",
        symbol="public_function",
        source_hash=source_hash
        or hashlib.sha256((root / "src" / "sample.py").read_bytes()).hexdigest(),
    )
    return BehaviorManifest(
        repository="git-provenance-fixture",
        repository_commit=repository_commit,
        behaviors=(
            BehaviorEntry(
                behavior_id="behavior-alpha",
                summary="Verify exact committed source provenance.",
                contracts=("Bind source claims to a real commit blob.",),
                inputs=(),
                outputs=(),
                preconditions=(),
                postconditions=(),
                failure_modes=(),
                state_read=(),
                state_written=(),
                tools=("Git object database.",),
                permissions=("Repository read only.",),
                dependencies=(),
                governing_rule_version_ids=("rule-provenance-v1",),
                source_bindings=(binding,),
                test_paths=("tests/test_sample.py",),
                related_behaviors=(),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("object_kind", "expected_finding"),
    (
        ("missing", "REPOSITORY_COMMIT_NOT_FOUND"),
        ("blob", "REPOSITORY_OBJECT_NOT_COMMIT"),
        ("tree", "REPOSITORY_OBJECT_NOT_COMMIT"),
    ),
)
def test_repository_commit_must_resolve_to_a_real_commit_object(
    tmp_path: Path,
    object_kind: str,
    expected_finding: str,
) -> None:
    root, commit = _repository(tmp_path)
    object_id = {
        "missing": "0" * len(commit),
        "blob": _git(root, "rev-parse", "HEAD:src/sample.py").decode("ascii").strip(),
        "tree": _git(root, "rev-parse", "HEAD^{tree}").decode("ascii").strip(),
    }[object_kind]

    result = verify_handbook(root, _manifest(root, object_id))

    assert result.valid is False
    assert expected_finding in result.finding_codes
    assert result.provenance_verified is False
    with pytest.raises(ValueError, match="verified provenance"):
        create_verification_record(
            result,
            verification_id=f"invalid-{object_kind}-provenance",
            verified_at=datetime(2026, 7, 20, tzinfo=UTC),
            governing_policy_hash="f" * 64,
        )


def test_declared_hash_is_checked_against_the_exact_blob_at_the_commit(tmp_path: Path) -> None:
    root, commit = _repository(tmp_path)
    changed = b"def public_function(value: int) -> int:\n    return value + 99\n"
    (root / "src" / "sample.py").write_bytes(changed)
    declared = _manifest(root, commit, hashlib.sha256(changed).hexdigest())

    result = verify_handbook(root, declared)

    assert result.valid is False
    assert "COMMIT_SOURCE_MISMATCH" in result.finding_codes
    assert "CHECKOUT_SOURCE_STALE" in result.finding_codes
    assert result.expected_source_tree_hash != result.actual_source_tree_hash
    assert result.provenance_verified is False
    with pytest.raises(ValueError, match="verified provenance"):
        create_verification_record(
            result,
            verification_id="manifest-cannot-rebind-old-commit",
            verified_at=datetime(2026, 7, 20, tzinfo=UTC),
            governing_policy_hash="f" * 64,
        )


def test_checkout_staleness_does_not_erase_verified_commit_provenance(tmp_path: Path) -> None:
    root, commit = _repository(tmp_path)
    declared = _manifest(root, commit)
    (root / "src" / "sample.py").write_bytes(
        b"def public_function(value: int) -> int:\n    return value + 2\n"
    )

    result = verify_handbook(root, declared)

    assert result.valid is False
    assert result.provenance_verified is True
    assert result.finding_codes == ("CHECKOUT_SOURCE_STALE",)
    record = create_verification_record(
        result,
        verification_id="verified-commit-with-stale-checkout",
        verified_at=datetime(2026, 7, 20, tzinfo=UTC),
        governing_policy_hash="f" * 64,
    )
    assert record.repository_commit == commit


def test_historical_commit_is_valid_when_every_bound_path_still_matches(tmp_path: Path) -> None:
    root, historical_commit = _repository(tmp_path)
    declared = _manifest(root, historical_commit)
    (root / "unrelated.txt").write_bytes(b"later unrelated state\n")
    _git(root, "add", "unrelated.txt")
    _git(root, "commit", "--quiet", "-m", "unrelated later commit")

    result = verify_handbook(root, declared)

    assert result.valid is True
    assert result.provenance_verified is True
    assert result.repository_commit == historical_commit
