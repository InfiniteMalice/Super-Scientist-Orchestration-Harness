from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess  # nosec B404
import sys
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    canonical_json_bytes,
    sha256_hex,
)

POLICY_RELATIVE_PATH = PurePosixPath("quality/imported-pattern-firewall-policy.json")
PINNED_POLICY_SHA256 = "29e5dad156e9fba2196b6a4989187ca1051295779cf1a3e11cce7be41410db38"
ALLOWED_ATTRIBUTION_PATHS = (
    "docs/sources/source-register.yaml",
    "docs/superpowers/plans/2026-07-11-epistemic-kernel-vertical-slice.md",
    "docs/superpowers/plans/2026-07-18-governed-adaptation-and-harness-evolution.md",
    "docs/superpowers/specs/2026-07-11-super-scientist-foundation-design.md",
    "docs/superpowers/specs/2026-07-18-governed-adaptation-and-harness-evolution-design.md",
)
ACTIVE_APPROVED_REGISTRY_HASH = "a57908a27492d82ae73d011b3c243d26b4b2efe352c07052261012ffab91cc72"
ACTIVE_APPROVED_FIREWALL_POLICY_SHA256 = (
    "29e5dad156e9fba2196b6a4989187ca1051295779cf1a3e11cce7be41410db38"
)
ACTIVE_APPROVED_ALLOWED_ATTRIBUTION_PATHS = (
    "docs/sources/source-register.yaml",
    "docs/superpowers/plans/2026-07-11-epistemic-kernel-vertical-slice.md",
    "docs/superpowers/plans/2026-07-18-governed-adaptation-and-harness-evolution.md",
    "docs/superpowers/specs/2026-07-11-super-scientist-foundation-design.md",
    "docs/superpowers/specs/2026-07-18-governed-adaptation-and-harness-evolution-design.md",
)
ACTIVE_APPROVED_QUALITY_POLICY_HASH = (
    "a5dfc9857c5af5db9eec17ce1f57cd2d367e47f3a4b759aa8fda55a4f247e494"
)

_TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".ini",
        ".json",
        ".lock",
        ".md",
        ".ps1",
        ".py",
        ".sh",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_IGNORED_TOP_LEVEL_NAMES = frozenset(
    {
        ".coverage",
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".superpowers",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
    }
)
_GIT_INVENTORY_TIMEOUT_SECONDS = 30.0
_SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


def _is_exact_relative_file_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and "\\" not in value
        and not path.is_absolute()
        and len(path.parts) >= 2
        and all(part not in {"", ".", ".."} for part in path.parts)
        and not any(character in value for character in "*?[]")
        and ":" not in value
        and bool(path.suffix)
    )


class ImportedPatternPolicy(_StrictFrozenModel):
    policy_version: Literal[1]
    denied_terms: tuple[NonBlankText, ...] = Field(min_length=1)
    allowed_attribution_paths: tuple[NonBlankText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_canonical_sequences(self) -> Self:
        if self.denied_terms != tuple(sorted(set(self.denied_terms))):
            raise ValueError("denied terms must be sorted and unique")
        if self.allowed_attribution_paths != tuple(sorted(set(self.allowed_attribution_paths))):
            raise ValueError("allowed attribution paths must be sorted and unique")
        if not all(_is_exact_relative_file_path(path) for path in self.allowed_attribution_paths):
            raise ValueError("allowed attribution paths must name exact relative files")
        return self


type FindingCode = Literal[
    "POLICY_DIGEST_MISMATCH",
    "POLICY_SCHEMA_INVALID",
    "POLICY_ALLOWLIST_MISMATCH",
    "DENIED_TERM_FOUND",
    "TEXT_DECODE_FAILED",
    "UNSAFE_SCAN_PATH",
    "SCAN_FAILED",
]


class FirewallFinding(_StrictFrozenModel):
    code: FindingCode
    path: NonBlankText
    message: NonBlankText
    term_sha256: Sha256Hex | None = None


class ImportedPatternFirewallResult(_StrictFrozenModel):
    policy_sha256: Sha256Hex
    passed: bool
    findings: tuple[FirewallFinding, ...]


class QualityPolicyBinding(_StrictFrozenModel):
    registry_hash: Sha256Hex
    firewall_policy_sha256: Sha256Hex
    allowed_attribution_paths: tuple[NonBlankText, ...] = Field(min_length=1)
    quality_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_composite_hash(self) -> Self:
        expected_hash = quality_policy_hash(
            registry_hash=self.registry_hash,
            firewall_policy_sha256=self.firewall_policy_sha256,
            allowed_attribution_paths=self.allowed_attribution_paths,
        )
        if self.quality_policy_hash != expected_hash:
            raise ValueError("quality policy binding does not match its declared hash")
        return self


def quality_registry_hash(
    checks: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    python_executable: str,
) -> str:
    if not checks:
        raise ValueError("quality registry must contain at least one check")
    names = tuple(name for name, _ in checks)
    if len(set(names)) != len(names) or any(not name or name != name.strip() for name in names):
        raise ValueError("quality check names must be non-blank and unique")
    normalized_checks: list[dict[str, object]] = []
    for name, argv in checks:
        if not argv or argv[0] != python_executable:
            raise ValueError("quality checks must bind the reviewed Python executable")
        normalized_checks.append(
            {
                "argv": ("{python}", *argv[1:]),
                "name": name,
            }
        )
    return sha256_hex(
        canonical_json_bytes(
            {
                "checks": normalized_checks,
                "quality_registry_version": 1,
            }
        )
    )


def quality_policy_hash(
    *,
    registry_hash: Sha256Hex,
    firewall_policy_sha256: Sha256Hex,
    allowed_attribution_paths: tuple[str, ...],
) -> str:
    validated_registry_hash = _SHA256_ADAPTER.validate_python(registry_hash)
    validated_firewall_hash = _SHA256_ADAPTER.validate_python(firewall_policy_sha256)
    if allowed_attribution_paths != tuple(sorted(set(allowed_attribution_paths))):
        raise ValueError("allowed attribution paths must be sorted and unique")
    if not all(_is_exact_relative_file_path(path) for path in allowed_attribution_paths):
        raise ValueError("allowed attribution paths must name exact relative files")
    payload = {
        "allowed_attribution_paths": allowed_attribution_paths,
        "firewall_policy_sha256": validated_firewall_hash,
        "quality_policy_version": 1,
        "registry_hash": validated_registry_hash,
    }
    return sha256_hex(canonical_json_bytes(payload))


def current_quality_policy_binding() -> QualityPolicyBinding:
    from super_scientist.quality.runner import CHECKS

    registry_hash = quality_registry_hash(
        tuple((check.name, check.argv) for check in CHECKS),
        python_executable=sys.executable,
    )
    composite_hash = quality_policy_hash(
        registry_hash=registry_hash,
        firewall_policy_sha256=PINNED_POLICY_SHA256,
        allowed_attribution_paths=ALLOWED_ATTRIBUTION_PATHS,
    )
    return QualityPolicyBinding(
        registry_hash=registry_hash,
        firewall_policy_sha256=PINNED_POLICY_SHA256,
        allowed_attribution_paths=ALLOWED_ATTRIBUTION_PATHS,
        quality_policy_hash=composite_hash,
    )


def approved_quality_policy_binding() -> QualityPolicyBinding:
    return QualityPolicyBinding(
        registry_hash=ACTIVE_APPROVED_REGISTRY_HASH,
        firewall_policy_sha256=ACTIVE_APPROVED_FIREWALL_POLICY_SHA256,
        allowed_attribution_paths=ACTIVE_APPROVED_ALLOWED_ATTRIBUTION_PATHS,
        quality_policy_hash=ACTIVE_APPROVED_QUALITY_POLICY_HASH,
    )


def load_imported_pattern_policy(project_root: Path) -> ImportedPatternPolicy:
    policy_path = project_root / Path(POLICY_RELATIVE_PATH.as_posix())
    unsafe_reason = _unsafe_repository_path_reason(project_root, policy_path)
    if unsafe_reason is not None:
        raise ValueError(unsafe_reason)
    raw = policy_path.read_bytes()
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != PINNED_POLICY_SHA256:
        raise ValueError("imported-pattern policy digest does not match the reviewed pin")
    policy = ImportedPatternPolicy.model_validate_json(raw)
    if policy.allowed_attribution_paths != ALLOWED_ATTRIBUTION_PATHS:
        raise ValueError("imported-pattern attribution allowlist does not match the executable pin")
    return policy


def run_imported_pattern_firewall(project_root: Path) -> ImportedPatternFirewallResult:
    return _evaluate_imported_pattern_firewall(
        project_root,
        expected_policy_sha256=PINNED_POLICY_SHA256,
        expected_allowed_paths=ALLOWED_ATTRIBUTION_PATHS,
    )


def _evaluate_imported_pattern_firewall(
    project_root: Path,
    *,
    expected_policy_sha256: str,
    expected_allowed_paths: tuple[str, ...],
) -> ImportedPatternFirewallResult:
    policy_path = project_root / Path(POLICY_RELATIVE_PATH.as_posix())
    try:
        unsafe_reason = _unsafe_repository_path_reason(project_root, policy_path)
        if unsafe_reason is not None:
            return _failed_result(
                expected_policy_sha256,
                "UNSAFE_SCAN_PATH",
                POLICY_RELATIVE_PATH.as_posix(),
                unsafe_reason,
            )
        raw = policy_path.read_bytes()
    except OSError:
        return _failed_result(
            expected_policy_sha256,
            "SCAN_FAILED",
            POLICY_RELATIVE_PATH.as_posix(),
            "the policy file could not be read",
        )
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != expected_policy_sha256:
        return _failed_result(
            actual_digest,
            "POLICY_DIGEST_MISMATCH",
            POLICY_RELATIVE_PATH.as_posix(),
            "the policy file does not match the reviewed digest",
        )
    try:
        policy = ImportedPatternPolicy.model_validate_json(raw)
    except (ValueError, json.JSONDecodeError):
        return _failed_result(
            actual_digest,
            "POLICY_SCHEMA_INVALID",
            POLICY_RELATIVE_PATH.as_posix(),
            "the policy file does not satisfy its strict schema",
        )
    if (
        policy.allowed_attribution_paths != expected_allowed_paths
        or expected_allowed_paths != ALLOWED_ATTRIBUTION_PATHS
    ):
        return _failed_result(
            actual_digest,
            "POLICY_ALLOWLIST_MISMATCH",
            POLICY_RELATIVE_PATH.as_posix(),
            "the attribution allowlist does not match the executable pin",
        )

    findings = _scan_repository(project_root, policy)
    return ImportedPatternFirewallResult(
        policy_sha256=actual_digest,
        passed=not findings,
        findings=findings,
    )


def _failed_result(
    digest: str,
    code: FindingCode,
    path: str,
    message: str,
) -> ImportedPatternFirewallResult:
    safe_digest = digest if len(digest) == 64 else "0" * 64
    return ImportedPatternFirewallResult(
        policy_sha256=safe_digest,
        passed=False,
        findings=(FirewallFinding(code=code, path=path, message=message),),
    )


def _scan_repository(
    project_root: Path,
    policy: ImportedPatternPolicy,
) -> tuple[FirewallFinding, ...]:
    findings: list[FirewallFinding] = []
    allowed = frozenset(policy.allowed_attribution_paths)
    policy_relative = POLICY_RELATIVE_PATH.as_posix()
    try:
        root = project_root.resolve(strict=True)
    except OSError:
        return (
            FirewallFinding(
                code="SCAN_FAILED",
                path=".",
                message="the repository root could not be resolved",
            ),
        )

    try:
        paths = _scan_paths(project_root)
    except OSError as error:
        return (
            FirewallFinding(
                code="SCAN_FAILED",
                path=".",
                message=(
                    f"the repository tree could not be enumerated ({type(error).__name__}: {error})"
                ),
            ),
        )

    for path in paths:
        try:
            relative = path.relative_to(project_root).as_posix()
        except ValueError:
            continue
        try:
            unsafe_reason = _unsafe_repository_path_reason(project_root, path)
            if unsafe_reason is not None:
                findings.append(
                    FirewallFinding(
                        code="UNSAFE_SCAN_PATH",
                        path=relative,
                        message=unsafe_reason,
                    )
                )
                continue
            if relative in allowed or relative == policy_relative:
                continue
            if not path.is_file() or not _is_text_candidate(path):
                continue
            resolved = path.resolve(strict=True)
            if root not in resolved.parents:
                findings.append(
                    FirewallFinding(
                        code="UNSAFE_SCAN_PATH",
                        path=relative,
                        message="a scan input resolves outside the repository",
                    )
                )
                continue
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(
                FirewallFinding(
                    code="TEXT_DECODE_FAILED",
                    path=relative,
                    message="a scanned text file is not valid UTF-8",
                )
            )
            continue
        except OSError:
            findings.append(
                FirewallFinding(
                    code="SCAN_FAILED",
                    path=relative,
                    message="a scan input could not be verified",
                )
            )
            continue
        casefolded = text.casefold()
        for term in policy.denied_terms:
            if term.casefold() in casefolded:
                findings.append(
                    FirewallFinding(
                        code="DENIED_TERM_FOUND",
                        path=relative,
                        message="a denied imported-pattern term was found",
                        term_sha256=hashlib.sha256(term.encode("utf-8")).hexdigest(),
                    )
                )
    return tuple(findings)


def _scan_paths(project_root: Path) -> tuple[Path, ...]:
    if _is_git_worktree(project_root):
        return _git_scan_paths(project_root)
    return _recursive_scan_paths(project_root)


def _git_scan_paths(project_root: Path) -> tuple[Path, ...]:
    trusted_root = project_root.resolve(strict=True)
    argv = (
        "git",
        "-c",
        f"safe.directory={trusted_root}",
        "-C",
        str(trusted_root),
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    )
    try:
        completed = subprocess.run(  # nosec B603
            argv,
            check=False,
            capture_output=True,
            timeout=_GIT_INVENTORY_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise OSError("Git inventory enumeration timed out") from error
    if completed.returncode != 0 or not isinstance(completed.stdout, bytes):
        raise OSError(f"Git inventory enumeration failed with status {completed.returncode}")
    raw_inventory = completed.stdout
    if raw_inventory and not raw_inventory.endswith(b"\0"):
        raise OSError("Git inventory is not NUL terminated")

    paths: list[Path] = []
    seen: set[str] = set()
    for raw_relative in raw_inventory.split(b"\0")[:-1]:
        try:
            relative = raw_relative.decode("utf-8")
        except UnicodeDecodeError as error:
            raise OSError("Git inventory path is not valid UTF-8") from error
        posix_path = _strict_inventory_path(relative)
        canonical_relative = posix_path.as_posix()
        if canonical_relative in seen:
            raise OSError("Git inventory contains duplicate paths")
        seen.add(canonical_relative)
        if posix_path.parts[0] in _IGNORED_TOP_LEVEL_NAMES:
            continue
        paths.append(project_root.joinpath(*posix_path.parts))
    return tuple(paths)


def _strict_inventory_path(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != relative
    ):
        raise OSError("Git inventory contains an unsafe relative path")
    return path


def _is_git_worktree(project_root: Path) -> bool:
    marker = project_root / ".git"
    try:
        metadata = marker.lstat()
    except FileNotFoundError:
        return False
    return not _is_link_or_reparse(metadata) and (
        stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
    )


def _recursive_scan_paths(project_root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []

    def collect(directory: Path, *, top_level: bool) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if top_level and entry.name in _IGNORED_TOP_LEVEL_NAMES:
                    continue
                path = Path(entry.path)
                paths.append(path)
                metadata = entry.stat(follow_symlinks=False)
                if _is_link_or_reparse(metadata):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    collect(path, top_level=False)

    collect(project_root, top_level=True)
    return tuple(paths)


def _is_text_candidate(path: Path) -> bool:
    return not path.suffix or path.suffix.lower() in _TEXT_SUFFIXES


def _unsafe_repository_path_reason(project_root: Path, path: Path) -> str | None:
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        return "a scan input is outside the repository"

    current = project_root
    candidates = [current]
    for part in relative.parts:
        current /= part
        candidates.append(current)
    for candidate in candidates:
        metadata = candidate.lstat()
        if _is_link_or_reparse(metadata):
            return "a symlink or reparse point is not an admissible scan input"

    root = project_root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        return "a scan input resolves outside the repository"
    return None


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


from super_scientist.quality.policy_records import (  # noqa: E402
    QualityPolicyProposal,
    canonical_record_bytes,
)


def quality_policy_proposal_record(proposal: QualityPolicyProposal) -> bytes:
    """Return canonical pending-record bytes without granting source-edit authority."""

    return canonical_record_bytes(proposal)
