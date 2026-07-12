from pathlib import Path

import pytest

from super_scientist.providers.storage.artifacts import FileArtifactStore


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable in this environment: {error}")


def test_artifact_put_is_content_addressed(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)

    first = store.put(b"raw evidence", "text/plain")
    second = store.put(b"raw evidence", "text/plain")

    assert first == second
    assert store.read(first) == b"raw evidence"


def test_media_type_is_contextual_metadata_not_part_of_content_address(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)

    text_ref = store.put(b"raw evidence", " Text/Plain ")
    binary_ref = store.put(b"raw evidence", "application/octet-stream")
    relabeled_ref = text_ref.model_copy(update={"media_type": "application/json"})

    assert text_ref.media_type == "text/plain"
    assert binary_ref.media_type == "application/octet-stream"
    assert (text_ref.sha256, text_ref.relative_path, text_ref.size_bytes) == (
        binary_ref.sha256,
        binary_ref.relative_path,
        binary_ref.size_bytes,
    )
    assert store.read(relabeled_ref) == b"raw evidence"


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


def test_artifact_store_rejects_static_symlinked_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root_link = tmp_path / "artifact-root"
    _symlink_or_skip(root_link, outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink or reparse point"):
        FileArtifactStore(root_link)


def test_artifact_store_rejects_static_symlinked_parent(tmp_path: Path) -> None:
    root = tmp_path / "artifact-root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_or_skip(root / "sha256", outside, target_is_directory=True)
    store = FileArtifactStore(root)

    with pytest.raises(ValueError, match="symlink or reparse point"):
        store.put(b"original", "application/octet-stream")


def test_artifact_store_rejects_static_symlinked_leaf(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(b"original", "application/octet-stream")
    path = store.resolve(ref)
    path.unlink()
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"original")
    _symlink_or_skip(path, replacement)

    with pytest.raises(ValueError, match="symlink or reparse point"):
        store.put(b"original", "application/octet-stream")
