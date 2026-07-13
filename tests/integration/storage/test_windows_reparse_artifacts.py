from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from super_scientist.providers.storage.artifacts import FileArtifactStore

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Windows junction fixtures exercise reparse-point handling",
)


def _create_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_artifact_store_rejects_junction_root_without_symlink_privilege(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "artifact-root"
    _create_junction(junction, target)
    try:
        with pytest.raises(ValueError, match="symlink or reparse point"):
            FileArtifactStore(junction)
    finally:
        junction.rmdir()


def test_artifact_store_rejects_junction_parent_without_symlink_privilege(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact-root"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    junction = root / "sha256"
    _create_junction(junction, target)
    try:
        store = FileArtifactStore(root)
        with pytest.raises(ValueError, match="symlink or reparse point"):
            store.put(b"evidence", "application/octet-stream")
    finally:
        junction.rmdir()
