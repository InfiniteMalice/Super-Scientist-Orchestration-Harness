from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from super_scientist.handbook import (
    BehaviorEntry,
    BehaviorManifest,
    PathContainmentError,
    SourceBinding,
    build_handbook,
    verify_handbook,
)

COMMIT = "a" * 40


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
    binding = SourceBinding(
        repository_commit=COMMIT,
        relative_path=path,
        symbol=symbol,
        source_hash=source_hash or (_digest(target) if target.is_file() else "f" * 64),
    )
    return BehaviorManifest(
        repository="adversarial-fixture",
        repository_commit=COMMIT,
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
