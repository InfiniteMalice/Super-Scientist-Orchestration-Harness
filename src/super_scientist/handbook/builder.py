from __future__ import annotations

import ast
import json
import stat
import string

# Git's object database and clean-filter semantics are the provenance authority.
import subprocess  # nosec B404
import tokenize
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.handbook.models import (
    BehaviorEntry,
    BehaviorManifest,
    HandbookBuildError,
    HandbookBuildResult,
    HandbookFinding,
    HandbookFindingCode,
    PathContainmentError,
    RuleBehaviorLink,
    SourceBehaviorLink,
    SourceLocation,
    SourceSymbolKind,
)

_WINDOWS_RESERVED_PATH_NAMES = frozenset(
    {
        "AUX",
        "CLOCK$",
        "CON",
        "CONIN$",
        "CONOUT$",
        "NUL",
        "PRN",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        *(f"COM{number}" for number in ("¹", "²", "³")),
        *(f"LPT{number}" for number in ("¹", "²", "³")),
    }
)
_WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>"|?*')


@dataclass(frozen=True, slots=True)
class _SymbolFact:
    kind: SourceSymbolKind
    start_line: int
    end_line: int
    source_hash: str


@dataclass(frozen=True, slots=True)
class _Inspection:
    findings: tuple[HandbookFinding, ...]
    source_locations: tuple[SourceLocation, ...]
    expected_source_tree_hash: str
    actual_source_tree_hash: str
    source_hashes: tuple[str, ...]
    provenance_verified: bool


class _SymbolInventory(ast.NodeVisitor):
    def __init__(self, source_text: str) -> None:
        self._source_text = source_text
        self._scope: list[tuple[str, bool]] = []
        self.facts: dict[str, _SymbolFact] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node, SourceSymbolKind.CLASS)
        self._scope.append((node.name, True))
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = (
            SourceSymbolKind.METHOD
            if any(is_class for _, is_class in self._scope)
            else SourceSymbolKind.FUNCTION
        )
        self._record(node, kind)
        self._scope.append((node.name, False))
        self.generic_visit(node)
        self._scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = (
            SourceSymbolKind.ASYNC_METHOD
            if any(is_class for _, is_class in self._scope)
            else SourceSymbolKind.ASYNC_FUNCTION
        )
        self._record(node, kind)
        self._scope.append((node.name, False))
        self.generic_visit(node)
        self._scope.pop()

    def _record(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: SourceSymbolKind,
    ) -> None:
        symbol = ".".join((*((name for name, _ in self._scope)), node.name))
        segment = ast.get_source_segment(self._source_text, node)
        if segment is None:
            segment = "\n".join(
                self._source_text.splitlines()[node.lineno - 1 : (node.end_lineno or node.lineno)]
            )
        self.facts[symbol] = _SymbolFact(
            kind=kind,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            source_hash=sha256_hex(segment.encode("utf-8")),
        )


def build_handbook(
    repository_root: Path,
    manifest: BehaviorManifest,
    *,
    repository_commit: str | None = None,
) -> HandbookBuildResult:
    """Build deterministic projections after verifying every human declaration."""

    effective_commit = repository_commit or manifest.repository_commit
    inspection = _inspect_manifest(repository_root, manifest, effective_commit)
    if inspection.findings:
        raise HandbookBuildError(tuple(finding.code for finding in inspection.findings))
    return _render_build(manifest, inspection)


def manifest_schema_bytes() -> bytes:
    return _pretty_json_bytes(BehaviorManifest.model_json_schema())


def _inspect_manifest(
    repository_root: Path,
    manifest: BehaviorManifest,
    repository_commit: str,
) -> _Inspection:
    root = _repository_root(repository_root)
    findings: list[HandbookFinding] = []
    locations: list[SourceLocation] = []
    actual_by_path: dict[str, str | None] = {}
    expected_by_path: dict[str, str] = {}
    inventory_by_path: dict[str, dict[str, _SymbolFact]] = {}
    parse_failure_by_path: dict[str, HandbookFindingCode] = {}
    provenance_verified = repository_commit == manifest.repository_commit
    commit_verified = False

    if not provenance_verified:
        findings.append(
            HandbookFinding(
                code=HandbookFindingCode.REPOSITORY_COMMIT_MISMATCH,
                message="declared repository commit does not match the verified repository commit",
                behavior_id=None,
                location=None,
            )
        )
    else:
        object_type = _git_object_type(root, repository_commit)
        if object_type is None:
            findings.append(
                HandbookFinding(
                    code=HandbookFindingCode.REPOSITORY_COMMIT_NOT_FOUND,
                    message="declared repository commit is unavailable in the Git object database",
                    behavior_id=None,
                    location=None,
                )
            )
            provenance_verified = False
        elif object_type != "commit":
            findings.append(
                HandbookFinding(
                    code=HandbookFindingCode.REPOSITORY_OBJECT_NOT_COMMIT,
                    message="declared repository object is not a commit",
                    behavior_id=None,
                    location=None,
                )
            )
            provenance_verified = False
        else:
            commit_verified = True

    for behavior in sorted(manifest.behaviors, key=lambda item: item.behavior_id):
        for binding in sorted(
            behavior.source_bindings,
            key=lambda item: (item.relative_path, item.symbol),
        ):
            label = f"{binding.relative_path}:{binding.symbol}"
            expected_by_path[binding.relative_path] = binding.source_hash
            source_path = _contained_path(root, binding.relative_path)
            if binding.relative_path not in actual_by_path:
                committed_bytes = (
                    _committed_source_bytes(root, repository_commit, binding.relative_path)
                    if commit_verified
                    else None
                )
                _inspect_source_bytes(
                    committed_bytes,
                    binding.relative_path,
                    actual_by_path,
                    inventory_by_path,
                    parse_failure_by_path,
                )

            if not source_path.exists():
                findings.append(
                    _finding(
                        HandbookFindingCode.SOURCE_NOT_FOUND,
                        behavior,
                        label,
                        "declared source file is unavailable",
                    )
                )
            elif not _is_regular_file(source_path):
                findings.append(
                    _finding(
                        HandbookFindingCode.SOURCE_NOT_REGULAR_FILE,
                        behavior,
                        label,
                        "declared source path is not a regular file",
                    )
                )
            if source_path.suffix != ".py":
                findings.append(
                    _finding(
                        HandbookFindingCode.SOURCE_NOT_PYTHON,
                        behavior,
                        label,
                        "declared source path is not a Python source file",
                    )
                )

            actual_hash = actual_by_path[binding.relative_path]
            if commit_verified and actual_hash is None:
                findings.append(
                    _finding(
                        HandbookFindingCode.COMMIT_SOURCE_MISMATCH,
                        behavior,
                        label,
                        "declared source is not a regular blob at the repository commit",
                    )
                )
                provenance_verified = False
                continue
            if commit_verified and actual_hash != binding.source_hash:
                findings.append(
                    _finding(
                        HandbookFindingCode.COMMIT_SOURCE_MISMATCH,
                        behavior,
                        label,
                        "declared source hash does not match the exact committed blob bytes",
                    )
                )
                provenance_verified = False
            if commit_verified and not _git_checkout_path_is_clean(
                root,
                repository_commit,
                binding.relative_path,
            ):
                findings.append(
                    _finding(
                        HandbookFindingCode.CHECKOUT_SOURCE_STALE,
                        behavior,
                        label,
                        "current checkout does not match the declared committed source",
                    )
                )
            _assert_no_link_or_reparse(source_path)

            if source_path.suffix != ".py" or actual_hash is None:
                continue
            parse_failure = parse_failure_by_path.get(binding.relative_path)
            if parse_failure is not None:
                message = (
                    "declared Python source has an unsupported encoding"
                    if parse_failure is HandbookFindingCode.SOURCE_ENCODING_ERROR
                    else "declared Python source could not be parsed"
                )
                findings.append(_finding(parse_failure, behavior, label, message))
                continue

            fact = inventory_by_path[binding.relative_path].get(binding.symbol)
            if fact is None:
                findings.append(
                    _finding(
                        HandbookFindingCode.SYMBOL_NOT_FOUND,
                        behavior,
                        label,
                        "declared symbol was not found in the Python AST",
                    )
                )
                continue
            locations.append(
                SourceLocation(
                    behavior_id=behavior.behavior_id,
                    repository_commit=manifest.repository_commit,
                    relative_path=binding.relative_path,
                    module=_module_name(binding.relative_path),
                    symbol=binding.symbol,
                    kind=fact.kind,
                    start_line=fact.start_line,
                    end_line=fact.end_line,
                    source_hash=actual_hash,
                    symbol_source_hash=fact.source_hash,
                )
            )

        for test_path_value in sorted(behavior.test_paths):
            test_path = _contained_path(root, test_path_value)
            if not test_path.exists():
                findings.append(
                    _finding(
                        HandbookFindingCode.TEST_NOT_FOUND,
                        behavior,
                        test_path_value,
                        "declared test file is unavailable",
                    )
                )
            elif not _is_regular_file(test_path):
                findings.append(
                    _finding(
                        HandbookFindingCode.TEST_NOT_REGULAR_FILE,
                        behavior,
                        test_path_value,
                        "declared test path is not a regular file",
                    )
                )

    retained_hashes = tuple(
        sorted(
            {
                actual_by_path.get(path) or expected_hash
                for path, expected_hash in expected_by_path.items()
            }
        )
    )
    return _Inspection(
        findings=_deduplicate_findings(findings),
        source_locations=tuple(
            sorted(
                locations,
                key=lambda item: (item.behavior_id, item.relative_path, item.symbol),
            )
        ),
        expected_source_tree_hash=_source_tree_hash(expected_by_path),
        actual_source_tree_hash=_source_tree_hash(actual_by_path),
        source_hashes=retained_hashes,
        provenance_verified=provenance_verified,
    )


def _inspect_source_bytes(
    source_bytes: bytes | None,
    relative_path: str,
    actual_by_path: dict[str, str | None],
    inventory_by_path: dict[str, dict[str, _SymbolFact]],
    parse_failure_by_path: dict[str, HandbookFindingCode],
) -> None:
    if source_bytes is None:
        actual_by_path[relative_path] = None
        inventory_by_path[relative_path] = {}
        return
    actual_hash = sha256_hex(source_bytes)
    actual_by_path[relative_path] = actual_hash
    inventory_by_path[relative_path] = {}
    if PurePosixPath(relative_path).suffix != ".py":
        return
    try:
        source_text = _decode_python(source_bytes)
    except (SyntaxError, UnicodeDecodeError):
        parse_failure_by_path[relative_path] = HandbookFindingCode.SOURCE_ENCODING_ERROR
        return
    try:
        syntax_tree = ast.parse(source_text, filename=relative_path, type_comments=True)
    except SyntaxError:
        parse_failure_by_path[relative_path] = HandbookFindingCode.SOURCE_SYNTAX_ERROR
        return
    inventory = _SymbolInventory(source_text)
    inventory.visit(syntax_tree)
    line_count = max(1, len(source_text.splitlines()))
    inventory.facts["<module>"] = _SymbolFact(
        kind=SourceSymbolKind.MODULE,
        start_line=1,
        end_line=line_count,
        source_hash=actual_hash,
    )
    inventory_by_path[relative_path] = inventory.facts


def _git_object_type(root: Path, object_id: str) -> str | None:
    completed = _run_git(root, "cat-file", "-t", object_id)
    if completed.returncode != 0:
        return None
    try:
        return completed.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        return None


def _committed_source_bytes(root: Path, commit: str, relative_path: str) -> bytes | None:
    pathspec = f":(literal){relative_path}"
    listed = _run_git(root, "ls-tree", "-z", commit, "--", pathspec)
    if listed.returncode != 0:
        return None
    entries = tuple(entry for entry in listed.stdout.split(b"\x00") if entry)
    if len(entries) != 1 or b"\t" not in entries[0]:
        return None
    header, returned_path = entries[0].split(b"\t", 1)
    header_parts = header.split(b" ")
    if len(header_parts) != 3:
        return None
    mode, object_type, object_id = header_parts
    if (
        mode not in {b"100644", b"100755"}
        or object_type != b"blob"
        or returned_path != relative_path.encode("utf-8")
    ):
        return None
    blob = _run_git(root, "cat-file", "blob", object_id.decode("ascii"))
    return blob.stdout if blob.returncode == 0 else None


def _git_checkout_path_is_clean(root: Path, commit: str, relative_path: str) -> bool:
    completed = _run_git(
        root,
        "diff",
        "--quiet",
        "--no-ext-diff",
        commit,
        "--",
        f":(literal){relative_path}",
    )
    return completed.returncode == 0


def _run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    # Arguments are fixed Git operations plus validated OIDs/literal paths; the shell is disabled.
    trusted_root = root.resolve(strict=True)
    return subprocess.run(  # nosec B603
        ("git", "-c", f"safe.directory={trusted_root}", *arguments),
        cwd=trusted_root,
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        shell=False,
    )


def _render_build(manifest: BehaviorManifest, inspection: _Inspection) -> HandbookBuildResult:
    manifest_hash = _manifest_hash(manifest)
    locations_by_behavior: dict[str, list[SourceLocation]] = defaultdict(list)
    for location in inspection.source_locations:
        locations_by_behavior[location.behavior_id].append(location)
    source_links = _source_links(inspection.source_locations)
    rule_links = _rule_links(manifest)
    behaviors = [
        _render_behavior(behavior, tuple(locations_by_behavior[behavior.behavior_id]))
        for behavior in sorted(manifest.behaviors, key=lambda item: item.behavior_id)
    ]
    document: dict[str, Any] = {
        "schema_version": 1,
        "authority": {
            "authoritative_inputs": [
                "human-authored manifest",
                "source",
                "tests",
                "governance policy",
                "active behavioral rules",
            ],
            "derived_index": True,
            "syntax_infers_behavior": False,
        },
        "repository": manifest.repository,
        "repository_commit": manifest.repository_commit,
        "manifest_hash": manifest_hash,
        "source_tree_hash": inspection.actual_source_tree_hash,
        "disclosure_levels": _disclosure_levels(behaviors),
        "behaviors": behaviors,
        "source_to_behaviors": [item.model_dump(mode="json") for item in source_links],
        "rule_to_behaviors": [item.model_dump(mode="json") for item in rule_links],
    }
    json_bytes = _pretty_json_bytes(document)
    markdown_bytes = _render_markdown(manifest, manifest_hash, inspection, behaviors)
    return HandbookBuildResult(
        repository_commit=manifest.repository_commit,
        manifest_hash=manifest_hash,
        source_tree_hash=inspection.actual_source_tree_hash,
        source_hashes=inspection.source_hashes,
        source_locations=inspection.source_locations,
        source_to_behaviors=source_links,
        rule_to_behaviors=rule_links,
        json_bytes=json_bytes,
        markdown_bytes=markdown_bytes,
        generated_artifact_hash=_artifact_hash(json_bytes, markdown_bytes),
    )


def _render_behavior(
    behavior: BehaviorEntry,
    locations: tuple[SourceLocation, ...],
) -> dict[str, Any]:
    payload = behavior.model_dump(mode="json", exclude={"source_bindings"})
    for field_name in (
        "contracts",
        "inputs",
        "outputs",
        "preconditions",
        "postconditions",
        "failure_modes",
        "state_read",
        "state_written",
        "tools",
        "permissions",
        "dependencies",
        "governing_rule_version_ids",
        "test_paths",
        "related_behaviors",
    ):
        payload[field_name] = sorted(payload[field_name])
    payload["source_locations"] = [
        item.model_dump(mode="json", exclude={"behavior_id"})
        for item in sorted(locations, key=lambda item: (item.relative_path, item.symbol))
    ]
    return payload


def _disclosure_levels(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "level": 1,
            "name": "summary",
            "behaviors": [
                {"behavior_id": item["behavior_id"], "summary": item["summary"]}
                for item in behaviors
            ],
        },
        {
            "level": 2,
            "name": "contracts_dependencies_rules",
            "behaviors": [
                {
                    "behavior_id": item["behavior_id"],
                    "contracts": item["contracts"],
                    "dependencies": item["dependencies"],
                    "governing_rule_version_ids": item["governing_rule_version_ids"],
                }
                for item in behaviors
            ],
        },
        {
            "level": 3,
            "name": "modules_symbols",
            "behaviors": [
                {
                    "behavior_id": item["behavior_id"],
                    "locations": [
                        {
                            "module": location["module"],
                            "symbol": location["symbol"],
                            "kind": location["kind"],
                        }
                        for location in item["source_locations"]
                    ],
                }
                for item in behaviors
            ],
        },
        {
            "level": 4,
            "name": "exact_source",
            "behaviors": [
                {
                    "behavior_id": item["behavior_id"],
                    "source_locations": item["source_locations"],
                    "test_paths": item["test_paths"],
                }
                for item in behaviors
            ],
        },
    ]


def _render_markdown(
    manifest: BehaviorManifest,
    manifest_hash: str,
    inspection: _Inspection,
    behaviors: list[dict[str, Any]],
) -> bytes:
    lines = [
        "# Verified Behavior Handbook",
        "",
        "> This is a deterministic derived index of human-authored behavior declarations.",
        "> Python syntax verifies locations only and does not infer behavioral truth.",
        "",
        f"- Repository: {_code_span(manifest.repository)}",
        f"- Repository commit: {_code_span(manifest.repository_commit)}",
        f"- Manifest SHA-256: {_code_span(manifest_hash)}",
        f"- Source-tree SHA-256: {_code_span(inspection.actual_source_tree_hash)}",
        "",
        "## Level 1: Summary",
        "",
    ]
    for behavior in behaviors:
        lines.extend(
            (
                f"### {_code_span(str(behavior['behavior_id']))}",
                "",
                _escape_markdown_text(str(behavior["summary"])),
                "",
            )
        )
    lines.extend(("## Level 2: Contracts, dependencies, and governing rules", ""))
    for behavior in behaviors:
        lines.extend((f"### {_code_span(str(behavior['behavior_id']))}", "", "Contracts:", ""))
        lines.extend(f"- {_escape_markdown_text(str(item))}" for item in behavior["contracts"])
        lines.extend(("", "Dependencies:", ""))
        lines.extend(_markdown_values(behavior["dependencies"]))
        lines.extend(("", "Governing rule versions:", ""))
        lines.extend(_markdown_values(behavior["governing_rule_version_ids"]))
        lines.append("")
    lines.extend(("## Level 3: Modules and symbols", ""))
    for behavior in behaviors:
        lines.extend((f"### {_code_span(str(behavior['behavior_id']))}", ""))
        for location in behavior["source_locations"]:
            lines.append(
                f"- {_code_span(str(location['module']))} — "
                f"{_code_span(str(location['symbol']))} "
                f"({_escape_markdown_text(str(location['kind']))})"
            )
        lines.append("")
    lines.extend(("## Level 4: Exact commit, path, lines, and hashes", ""))
    for behavior in behaviors:
        lines.extend((f"### {_code_span(str(behavior['behavior_id']))}", ""))
        for location in behavior["source_locations"]:
            source_reference = f"{location['relative_path']}:{location['start_line']}"
            lines.extend(
                (
                    f"- Commit: {_code_span(str(location['repository_commit']))}",
                    f"  - Source: {_code_span(source_reference)} "
                    f"through line {location['end_line']}",
                    f"  - Symbol: {_code_span(str(location['symbol']))}",
                    f"  - File SHA-256: {_code_span(str(location['source_hash']))}",
                    f"  - Symbol SHA-256: {_code_span(str(location['symbol_source_hash']))}",
                )
            )
        lines.extend(("", "Tests:", ""))
        lines.extend(f"- {_code_span(str(item))}" for item in behavior["test_paths"])
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _manifest_hash(manifest: BehaviorManifest) -> str:
    payload = manifest.model_dump(mode="json")
    payload["behaviors"] = sorted(
        (_normalized_behavior(item) for item in payload["behaviors"]),
        key=lambda item: str(item["behavior_id"]),
    )
    return sha256_hex(canonical_json_bytes(payload))


def _normalized_behavior(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(value)
    for field_name, field_value in normalized.items():
        if isinstance(field_value, list):
            if field_name == "source_bindings":
                normalized[field_name] = sorted(
                    field_value,
                    key=lambda item: (str(item["relative_path"]), str(item["symbol"])),
                )
            else:
                normalized[field_name] = sorted(field_value)
    return normalized


def _source_links(locations: tuple[SourceLocation, ...]) -> tuple[SourceBehaviorLink, ...]:
    behavior_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    for location in locations:
        behavior_ids[(location.relative_path, location.symbol)].add(location.behavior_id)
    return tuple(
        SourceBehaviorLink(
            relative_path=path,
            symbol=symbol,
            behavior_ids=tuple(sorted(ids)),
        )
        for (path, symbol), ids in sorted(behavior_ids.items())
    )


def _rule_links(manifest: BehaviorManifest) -> tuple[RuleBehaviorLink, ...]:
    behavior_ids: dict[str, set[str]] = defaultdict(set)
    for behavior in manifest.behaviors:
        for rule_version_id in behavior.governing_rule_version_ids:
            behavior_ids[rule_version_id].add(behavior.behavior_id)
    return tuple(
        RuleBehaviorLink(rule_version_id=rule_id, behavior_ids=tuple(sorted(ids)))
        for rule_id, ids in sorted(behavior_ids.items())
    )


def _artifact_hash(json_bytes: bytes, markdown_bytes: bytes) -> str:
    return sha256_hex(
        canonical_json_bytes(
            {
                "handbook_json_sha256": sha256_hex(json_bytes),
                "handbook_markdown_sha256": sha256_hex(markdown_bytes),
            }
        )
    )


def _source_tree_hash(source_hashes: Mapping[str, str | None]) -> str:
    return sha256_hex(
        canonical_json_bytes(
            [
                {"relative_path": path, "source_hash": source_hash}
                for path, source_hash in sorted(source_hashes.items())
            ]
        )
    )


def _finding(
    code: HandbookFindingCode,
    behavior: BehaviorEntry,
    location: str,
    message: str,
) -> HandbookFinding:
    return HandbookFinding(
        code=code,
        message=message,
        behavior_id=behavior.behavior_id,
        location=location,
    )


def _deduplicate_findings(findings: list[HandbookFinding]) -> tuple[HandbookFinding, ...]:
    retained: list[HandbookFinding] = []
    seen: set[tuple[HandbookFindingCode, str | None, str | None]] = set()
    for finding in findings:
        identity = (finding.code, finding.behavior_id, finding.location)
        if identity not in seen:
            seen.add(identity)
            retained.append(finding)
    return tuple(retained)


def _repository_root(repository_root: Path) -> Path:
    absolute = repository_root.absolute()
    _assert_no_link_or_reparse(absolute)
    if not absolute.exists() or not _is_regular_directory(absolute):
        raise PathContainmentError("repository root must be an existing regular directory")
    resolved = absolute.resolve(strict=True)
    _assert_no_link_or_reparse(resolved)
    return resolved


def _contained_path(root: Path, relative_value: str) -> Path:
    _validate_repository_relative_path(relative_value)
    relative = Path(*relative_value.split("/"))
    candidate = root.joinpath(relative)
    try:
        candidate.absolute().relative_to(root)
    except ValueError:
        raise PathContainmentError("declared path escapes repository root") from None
    _assert_no_link_or_reparse(candidate)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathContainmentError("declared path escapes repository root") from None
    _assert_no_link_or_reparse(candidate)
    return candidate


def _validate_repository_relative_path(relative_value: str) -> None:
    posix_path = PurePosixPath(relative_value)
    windows_path = PureWindowsPath(relative_value)
    segments = relative_value.split("/")
    if (
        not relative_value
        or "\\" in relative_value
        or ":" in relative_value
        or relative_value.startswith("/")
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
        or any(_is_noncanonical_path_segment(segment) for segment in segments)
        or posix_path.as_posix() != relative_value
    ):
        raise PathContainmentError(
            "declared path must use canonical forward-slash repository-relative syntax"
        )


def _is_noncanonical_path_segment(segment: str) -> bool:
    windows_basename = segment.split(".", 1)[0].upper()
    return (
        segment in {"", ".", ".."}
        or segment.endswith((".", " "))
        or any(character in segment for character in _WINDOWS_INVALID_FILENAME_CHARACTERS)
        or windows_basename in _WINDOWS_RESERVED_PATH_NAMES
    )


def _assert_no_link_or_reparse(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            entry = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(entry.st_mode) or (
            getattr(entry, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise PathContainmentError("declared path contains a symlink or reparse point")


def _is_regular_file(path: Path) -> bool:
    _assert_no_link_or_reparse(path)
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _is_regular_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _decode_python(source_bytes: bytes) -> str:
    readline = iter(source_bytes.splitlines(keepends=True)).__next__
    encoding, _ = tokenize.detect_encoding(readline)
    return source_bytes.decode(encoding)


def _module_name(relative_path: str) -> str:
    return ".".join(PurePosixPath(relative_path).with_suffix("").parts)


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _markdown_values(values: list[str]) -> list[str]:
    return [*(f"- {_code_span(item)}" for item in values)] or ["- None"]


def _escape_markdown_text(value: str) -> str:
    return "".join(
        f"\\{character}" if character in string.punctuation else character for character in value
    )


def _code_span(value: str) -> str:
    longest_run = max((len(run) for run in _backtick_runs(value)), default=0)
    delimiter = "`" * (longest_run + 1)
    padding = " " if value.startswith(("`", " ")) or value.endswith(("`", " ")) else ""
    return f"{delimiter}{padding}{value}{padding}{delimiter}"


def _backtick_runs(value: str) -> tuple[str, ...]:
    retained: list[str] = []
    current = ""
    for character in value:
        if character == "`":
            current += character
        elif current:
            retained.append(current)
            current = ""
    if current:
        retained.append(current)
    return tuple(retained) or ("",)


__all__ = ["build_handbook", "manifest_schema_bytes"]
