from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = PROJECT_ROOT / "quality" / "imported-pattern-firewall-policy.json"
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _firewall() -> ModuleType:
    return importlib.import_module("super_scientist.quality.imported_pattern_firewall")


def _minimal_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    policy = root / "quality" / POLICY_PATH.name
    policy.parent.mkdir(parents=True)
    shutil.copyfile(POLICY_PATH, policy)
    (root / "src").mkdir()
    return root


def test_policy_is_digest_pinned_strict_sorted_and_exactly_allowlisted() -> None:
    firewall = _firewall()
    raw = POLICY_PATH.read_bytes()
    policy = firewall.load_imported_pattern_policy(PROJECT_ROOT)

    assert hashlib.sha256(raw).hexdigest() == firewall.PINNED_POLICY_SHA256
    assert policy.policy_version == 1
    assert policy.denied_terms == tuple(sorted(set(policy.denied_terms)))
    assert policy.allowed_attribution_paths == firewall.ALLOWED_ATTRIBUTION_PATHS
    assert policy.denied_terms

    payload = json.loads(raw)
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        firewall.ImportedPatternPolicy.model_validate(payload)
    payload.pop("unexpected")
    payload["allowed_attribution_paths"] = ["docs"]
    with pytest.raises(ValidationError):
        firewall.ImportedPatternPolicy.model_validate(payload)


def test_firewall_fails_closed_for_missing_malformed_and_allowlist_drift(
    tmp_path: Path,
) -> None:
    firewall = _firewall()
    root = tmp_path / "repository"
    root.mkdir()

    missing = firewall._evaluate_imported_pattern_firewall(
        root,
        expected_policy_sha256="invalid",
        expected_allowed_paths=firewall.ALLOWED_ATTRIBUTION_PATHS,
    )
    assert missing.passed is False
    assert missing.policy_sha256 == "0" * 64
    assert missing.findings[0].code == "SCAN_FAILED"

    policy_path = root / "quality" / POLICY_PATH.name
    policy_path.parent.mkdir()
    malformed = b"{"
    policy_path.write_bytes(malformed)
    malformed_result = firewall._evaluate_imported_pattern_firewall(
        root,
        expected_policy_sha256=hashlib.sha256(malformed).hexdigest(),
        expected_allowed_paths=firewall.ALLOWED_ATTRIBUTION_PATHS,
    )
    assert malformed_result.findings[0].code == "POLICY_SCHEMA_INVALID"

    shutil.copyfile(POLICY_PATH, policy_path)
    drift = firewall._evaluate_imported_pattern_firewall(
        root,
        expected_policy_sha256=firewall.PINNED_POLICY_SHA256,
        expected_allowed_paths=("docs/other.md",),
    )
    assert drift.findings[0].code == "POLICY_ALLOWLIST_MISMATCH"


@pytest.mark.parametrize(
    ("relative_path", "body"),
    [
        ("src/leak.py", "{term}"),
        ("tests/fixtures/leak.json", '{"value":"{term}"}'),
        ("examples/leak.py", "# {term}"),
        (".github/workflows/leak.yml", "name: {term}"),
        ("pyproject.toml", '[project]\nname = "{term}"\n'),
    ],
)
def test_firewall_scans_source_fixture_example_command_and_dependency_surfaces(
    tmp_path: Path,
    relative_path: str,
    body: str,
) -> None:
    firewall = _firewall()
    root = _minimal_repository(tmp_path)
    term = firewall.load_imported_pattern_policy(root).denied_terms[0]
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.replace("{term}", term), encoding="utf-8")

    result = firewall.run_imported_pattern_firewall(root)

    assert result.passed is False
    assert any(finding.path == relative_path for finding in result.findings)
    assert all(term not in finding.message for finding in result.findings)
    assert any(
        finding.term_sha256 == hashlib.sha256(term.encode()).hexdigest()
        for finding in result.findings
    )


@pytest.mark.parametrize("outer_name", ["build", "dist"])
def test_ignored_names_outside_checkout_do_not_disable_scanning(
    tmp_path: Path,
    outer_name: str,
) -> None:
    firewall = _firewall()
    root = tmp_path / outer_name / "checkout"
    policy = root / "quality" / POLICY_PATH.name
    policy.parent.mkdir(parents=True)
    shutil.copyfile(POLICY_PATH, policy)
    term = firewall.load_imported_pattern_policy(root).denied_terms[0]
    leaked = root / "src" / "leak.py"
    leaked.parent.mkdir()
    leaked.write_text(term, encoding="utf-8")

    result = firewall.run_imported_pattern_firewall(root)

    assert result.passed is False
    assert any(finding.path == "src/leak.py" for finding in result.findings)


def test_firewall_scans_nested_generated_names_and_future_top_level_inventories(
    tmp_path: Path,
) -> None:
    firewall = _firewall()
    root = _minimal_repository(tmp_path)
    term = firewall.load_imported_pattern_policy(root).denied_terms[0]
    nested = root / "src" / "package" / "build" / "payload.py"
    nested.parent.mkdir(parents=True)
    nested.write_text(term, encoding="utf-8")
    inventory = root / "future-config" / "dependencies.lock"
    inventory.parent.mkdir()
    inventory.write_text(term, encoding="utf-8")
    command = root / "commands" / "release"
    command.parent.mkdir()
    command.write_text(term, encoding="utf-8")

    result = firewall.run_imported_pattern_firewall(root)

    assert result.passed is False
    assert {finding.path for finding in result.findings} >= {
        "src/package/build/payload.py",
        "future-config/dependencies.lock",
        "commands/release",
    }


def test_git_inventory_scans_tracked_and_untracked_files_but_not_ignored_scratch(
    tmp_path: Path,
) -> None:
    firewall = _firewall()
    root = _minimal_repository(tmp_path)
    subprocess.run(
        ("git", "-C", str(root), "init", "--quiet"),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    (root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    term = firewall.load_imported_pattern_policy(root).denied_terms[0]
    targets = {
        "src/tracked.py",
        "src/untracked.py",
        "src/package/build/payload.py",
        "future-config/dependencies.lock",
        "commands/release",
    }
    for relative in targets:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(term, encoding="utf-8")
    subprocess.run(
        ("git", "-C", str(root), "add", "src/tracked.py"),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    ignored = root / "scratch" / "unreadable"
    ignored.parent.mkdir()
    ignored.write_bytes(b"\xff")

    result = firewall.run_imported_pattern_firewall(root)

    assert result.passed is False
    assert {finding.path for finding in result.findings} == targets
    assert all(finding.code == "DENIED_TERM_FOUND" for finding in result.findings)


def test_git_inventory_uses_fixed_bounded_command_and_rejects_escaping_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firewall = _firewall()
    root = _minimal_repository(tmp_path)
    (root / ".git").mkdir()
    observed: dict[str, object] = {}

    def run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["argv"] = argv
        observed.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=b"../escape.py\0", stderr=b"")

    monkeypatch.setattr(firewall.subprocess, "run", run)

    result = firewall.run_imported_pattern_firewall(root)

    assert result.passed is False
    assert result.findings[0].code == "SCAN_FAILED"
    assert observed == {
        "argv": (
            "git",
            "-c",
            f"safe.directory={root.resolve()}",
            "-C",
            str(root.resolve()),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ),
        "capture_output": True,
        "check": False,
        "shell": False,
        "timeout": 30.0,
    }


def test_repository_firewall_passes_and_plaintext_terms_are_confined() -> None:
    firewall = _firewall()
    policy = firewall.load_imported_pattern_policy(PROJECT_ROOT)

    result = firewall.run_imported_pattern_firewall(PROJECT_ROOT)

    assert result.passed is True
    assert result.findings == ()
    skipped = {POLICY_PATH.relative_to(PROJECT_ROOT).as_posix(), *policy.allowed_attribution_paths}
    for base in ("src", "tests", "examples", "alembic", ".github", "docs"):
        for path in (PROJECT_ROOT / base).rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            if relative in skipped:
                continue
            text = path.read_text(encoding="utf-8")
            assert all(term not in text for term in policy.denied_terms), relative


def test_firewall_rejects_non_utf8_and_symlinked_scan_inputs(tmp_path: Path) -> None:
    firewall = _firewall()
    root = _minimal_repository(tmp_path)
    (root / "src" / "opaque.py").write_bytes(b"\xff")

    undecodable = firewall.run_imported_pattern_firewall(root)

    assert undecodable.passed is False
    assert any(finding.code == "TEXT_DECODE_FAILED" for finding in undecodable.findings)

    (root / "src" / "opaque.py").unlink()
    outside = tmp_path / "outside.py"
    outside.write_text("safe", encoding="utf-8")
    link = root / "src" / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    linked = firewall.run_imported_pattern_firewall(root)

    assert linked.passed is False
    assert any(finding.code == "UNSAFE_SCAN_PATH" for finding in linked.findings)


def test_firewall_validates_allowed_path_before_attribution_skip(tmp_path: Path) -> None:
    firewall = _firewall()
    root = _minimal_repository(tmp_path)
    allowed = root / firewall.ALLOWED_ATTRIBUTION_PATHS[0]
    allowed.parent.mkdir(parents=True)
    outside = tmp_path / "outside-attribution.txt"
    outside.write_text("attribution", encoding="utf-8")
    try:
        allowed.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = firewall.run_imported_pattern_firewall(root)

    assert result.passed is False
    assert any(
        finding.code == "UNSAFE_SCAN_PATH" and finding.path == firewall.ALLOWED_ATTRIBUTION_PATHS[0]
        for finding in result.findings
    )
