from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from super_scientist.handbook import (
    BehaviorEntry,
    BehaviorManifest,
    PathContainmentError,
    SourceBinding,
    build_handbook,
    verify_handbook,
)


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").decode("ascii").strip()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "inside.py").write_text(
        "def declared() -> str:\n    return 'inside'\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_inside.py").write_text(
        "def test_inside() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Handbook Fixture")
    _git(root, "config", "user.email", "handbook@example.invalid")
    _git(root, "add", "src/inside.py", "tests/test_inside.py")
    _git(root, "commit", "--quiet", "-m", "fixture snapshot")
    return root


def _manifest(
    root: Path,
    *,
    path: str = "src/inside.py",
    symbol: str = "declared",
    source_hash: str | None = None,
    test_paths: tuple[str, ...] = ("tests/test_inside.py",),
) -> BehaviorManifest:
    target = root / Path(path)
    commit = _head(root)
    binding = SourceBinding(
        repository_commit=commit,
        relative_path=path,
        symbol=symbol,
        source_hash=source_hash or (_digest(target) if target.is_file() else "f" * 64),
    )
    return BehaviorManifest(
        repository="adversarial-fixture",
        repository_commit=commit,
        behaviors=(
            BehaviorEntry(
                behavior_id="contained-behavior",
                summary="A declaration used to exercise containment.",
                contracts=("Read only the declared source.",),
                inputs=("A contained path.",),
                outputs=("Verified AST facts.",),
                preconditions=(),
                postconditions=("No source executes.",),
                failure_modes=("Escapes fail closed.",),
                state_read=("Declared source bytes.",),
                state_written=(),
                tools=("Python AST.",),
                permissions=("Repository read only.",),
                dependencies=(),
                governing_rule_version_ids=("rule-path-containment-v1",),
                source_bindings=(binding,),
                test_paths=test_paths,
                related_behaviors=(),
            ),
        ),
    )


@pytest.mark.parametrize(
    "escape",
    (
        "../outside.py",
        "src/../../outside.py",
        "/absolute/outside.py",
        "C:/outside.py",
        "C:\\outside.py",
        "//server/share/outside.py",
    ),
)
def test_manifest_cannot_escape_repository(tmp_path: Path, escape: str) -> None:
    root = _repository(tmp_path)
    (tmp_path / "outside.py").write_text("def declared() -> None:\n    pass\n", encoding="utf-8")
    with pytest.raises(PathContainmentError):
        verify_handbook(root, _manifest(root, path=escape, source_hash="f" * 64))


@pytest.mark.parametrize(
    "noncanonical_path",
    (
        "",
        ".",
        "./src/inside.py",
        "src/./inside.py",
        "src/../inside.py",
        "src//inside.py",
        "src/inside.py/",
        "src\\inside.py",
        "C:/outside.py",
        "C:\\outside.py",
        "/absolute/outside.py",
        "//server/share/outside.py",
        "\\\\server\\share\\outside.py",
        "\\\\?\\C:\\outside.py",
        "\\\\.\\C:\\outside.py",
        "CON",
        "src/NUL.py",
        "src/COM1",
        "src/LPT9.txt",
        "src:inside.py",
        "src/trailing./inside.py",
        "src/trailing /inside.py",
        " src/inside.py",
        "src/inside.py ",
    ),
)
def test_repository_paths_use_one_host_independent_canonical_syntax(
    tmp_path: Path,
    noncanonical_path: str,
) -> None:
    root = _repository(tmp_path)
    payload = json.loads(_manifest(root).model_dump_json())
    payload["behaviors"][0]["source_bindings"][0]["relative_path"] = noncanonical_path
    with pytest.raises((ValidationError, PathContainmentError)):
        declared = BehaviorManifest.model_validate_json(json.dumps(payload))
        verify_handbook(root, declared)


@pytest.mark.parametrize(
    ("field_path", "malicious_text"),
    (
        (("repository",), "repository\n## injected"),
        (("behaviors", 0, "summary"), "summary\r## injected"),
        (("behaviors", 0, "outputs", 0), "output\u2028## injected"),
        (("behaviors", 0, "contracts", 0), "contract\x00hidden"),
        (("behaviors", 0, "inputs", 0), "input\x1bhidden"),
        (
            ("behaviors", 0, "governing_rule_version_ids", 0),
            "rule-safe\u202eunsafe",
        ),
        (
            ("behaviors", 0, "source_bindings", 0, "relative_path"),
            "src/in\nside.py",
        ),
        (("behaviors", 0, "source_bindings", 0, "symbol"), "declared\n### injected"),
        (("behaviors", 0, "test_paths", 0), "tests/test_inside.py\rinjected"),
    ),
)
def test_every_renderable_manifest_string_is_single_line_and_control_free(
    tmp_path: Path,
    field_path: tuple[str | int, ...],
    malicious_text: str,
) -> None:
    payload = json.loads(_manifest(_repository(tmp_path)).model_dump_json())
    target: object = payload
    for component in field_path[:-1]:
        target = target[component]  # type: ignore[index]
    target[field_path[-1]] = malicious_text  # type: ignore[index]

    with pytest.raises(ValidationError, match="single-line and control-free"):
        BehaviorManifest.model_validate_json(json.dumps(payload))


def test_markdown_escapes_valid_structural_punctuation_without_new_headings(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    base = _manifest(root).behaviors[0]
    dangerous_behavior = BehaviorEntry.model_validate(
        base.model_dump(mode="python")
        | {
            "behavior_id": "behavior`[pipe|marker]",
            "summary": "Summary # heading | pipe `ticks` [label](target)!",
            "contracts": ("Contract *emphasis* | `code` [link](target).",),
            "governing_rule_version_ids": ("rule`[pipe|marker]",),
        }
    )
    declared = BehaviorManifest(
        repository="repository`[pipe|marker]",
        repository_commit=_head(root),
        behaviors=(dangerous_behavior,),
    )

    markdown = build_handbook(root, declared).markdown_bytes.decode("utf-8")

    assert [line for line in markdown.splitlines() if line.startswith("## ")] == [
        "## Level 1: Summary",
        "## Level 2: Contracts, dependencies, and governing rules",
        "## Level 3: Modules and symbols",
        "## Level 4: Exact commit, path, lines, and hashes",
    ]
    assert "### ``behavior`[pipe|marker]``" in markdown
    assert r"Summary \# heading \| pipe \`ticks\` \[label\]\(target\)\!" in markdown
    assert r"Contract \*emphasis\* \| \`code\` \[link\]\(target\)\." in markdown


def test_symlinked_source_parent_escape_is_rejected(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.py").write_text("def declared() -> None:\n    pass\n", encoding="utf-8")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")

    with pytest.raises(PathContainmentError, match="symlink or reparse point"):
        verify_handbook(root, _manifest(root, path="linked/outside.py", source_hash="f" * 64))


def test_symlinked_source_leaf_escape_is_rejected(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("def declared() -> None:\n    pass\n", encoding="utf-8")
    link = root / "src" / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")

    with pytest.raises(PathContainmentError, match="symlink or reparse point"):
        verify_handbook(root, _manifest(root, path="src/linked.py", source_hash="f" * 64))


def test_symlinked_repository_root_is_rejected(tmp_path: Path) -> None:
    real_root = _repository(tmp_path)
    root_link = tmp_path / "repository-link"
    try:
        root_link.symlink_to(real_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    with pytest.raises(PathContainmentError, match="symlink or reparse point"):
        verify_handbook(root_link, _manifest(real_root))


def test_test_path_escape_is_rejected_with_the_same_boundary(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    with pytest.raises(PathContainmentError):
        verify_handbook(root, _manifest(root, test_paths=("../outside_test.py",)))


def test_directories_and_non_python_sources_fail_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    directory_result = verify_handbook(root, _manifest(root, path="src", source_hash="f" * 64))
    assert directory_result.valid is False
    assert "SOURCE_NOT_REGULAR_FILE" in directory_result.finding_codes

    text_source = root / "src" / "source.txt"
    text_source.write_text("def declared(): pass\n", encoding="utf-8")
    text_result = verify_handbook(root, _manifest(root, path="src/source.txt"))
    assert text_result.valid is False
    assert "SOURCE_NOT_PYTHON" in text_result.finding_codes


def test_ast_inventory_never_imports_or_executes_declared_source(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    marker = root / "executed.txt"
    source = root / "src" / "inside.py"
    source.write_text(
        "from pathlib import Path\n"
        f"Path({os.fspath(marker)!r}).write_text('executed')\n"
        "def declared() -> str:\n"
        "    return 'syntax only'\n",
        encoding="utf-8",
    )
    _git(root, "add", "src/inside.py")
    _git(root, "commit", "--quiet", "-m", "static AST fixture")
    built = build_handbook(root, _manifest(root))
    assert built.source_locations[0].symbol == "declared"
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse attributes are platform specific")
def test_windows_reparse_source_parent_is_rejected_without_symlink_privilege(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    outside = tmp_path / "junction-target"
    outside.mkdir()
    (outside / "outside.py").write_text("def declared() -> None:\n    pass\n", encoding="utf-8")
    junction = root / "junction"
    completed = os.system(f'cmd /c mklink /J "{junction}" "{outside}" >nul')
    if completed != 0:
        pytest.skip("junction creation is unavailable")
    with pytest.raises(PathContainmentError, match="symlink or reparse point"):
        verify_handbook(root, _manifest(root, path="junction/outside.py", source_hash="f" * 64))
