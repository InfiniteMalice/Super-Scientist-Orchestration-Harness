from pathlib import Path

import pytest

from super_scientist.providers.storage.artifacts import FileArtifactStore


def test_artifact_put_is_content_addressed(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)

    first = store.put(b"raw evidence", "text/plain")
    second = store.put(b"raw evidence", "text/plain")

    assert first == second
    assert store.read(first) == b"raw evidence"


def test_artifact_store_rejects_corrupted_existing_blob(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(b"original", "application/octet-stream")
    path = store.resolve(ref)
    path.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="artifact hash mismatch"):
        store.put(b"original", "application/octet-stream")


def test_artifact_store_detects_corruption_on_read(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(b"original", "application/octet-stream")
    store.resolve(ref).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="artifact hash mismatch"):
        store.read(ref)


@pytest.mark.parametrize("relative_path", ["../outside", "/outside"])
def test_artifact_store_rejects_paths_outside_configured_root(
    tmp_path: Path, relative_path: str
) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(b"original", "application/octet-stream")
    escaped = ref.model_copy(update={"relative_path": relative_path})

    with pytest.raises(ValueError, match="artifact path escapes configured root"):
        store.resolve(escaped)


def test_artifact_store_rejects_symlinked_existing_blob(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(b"original", "application/octet-stream")
    path = store.resolve(ref)
    path.unlink()
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"original")
    try:
        path.symlink_to(replacement)
    except OSError:
        pytest.skip("symlink creation is not permitted in this environment")

    with pytest.raises(ValueError, match="artifact path contains a symlink"):
        store.put(b"original", "application/octet-stream")
