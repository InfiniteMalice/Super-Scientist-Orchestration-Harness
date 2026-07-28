from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Protocol

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.primitives import sha256_hex


class ArtifactStore(Protocol):
    def put(self, data: bytes, media_type: str) -> ArtifactRef: ...

    def read(self, ref: ArtifactRef) -> bytes: ...


class FileArtifactStore:
    """Store immutable artifacts beneath a private, trusted filesystem root.

    Static symlink and Windows reparse-point escapes fail closed. Concurrent replacement of
    namespace entries by a malicious local process is outside this store's threat boundary.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.absolute()
        self._assert_static_namespace(self._root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._assert_static_namespace(self._root)
        self._root = self._root.resolve()
        if not self._root.is_dir():
            raise ValueError("artifact root must be a directory")

    def put(self, data: bytes, media_type: str) -> ArtifactRef:
        ref, _ = self.put_with_creation_status(data, media_type)
        return ref

    def put_with_creation_status(
        self,
        data: bytes,
        media_type: str,
    ) -> tuple[ArtifactRef, bool]:
        digest = sha256_hex(data)
        relative = self._relative_path(digest)
        target = self._contained(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._assert_static_namespace(target)
        ref = ArtifactRef(
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
            relative_path=relative.as_posix(),
        )

        descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
        temporary = Path(temporary_name)
        created = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
                created = True
            except FileExistsError:
                self._verify_existing(target, digest)
        finally:
            temporary.unlink(missing_ok=True)

        return ref, created

    def read(self, ref: ArtifactRef) -> bytes:
        path = self.resolve(ref)
        self._require_regular_file(path)
        data = path.read_bytes()
        if len(data) != ref.size_bytes or sha256_hex(data) != ref.sha256:
            raise ValueError("artifact hash mismatch")
        return data

    def resolve(self, ref: ArtifactRef) -> Path:
        relative = Path(ref.relative_path)
        path = self._contained(relative)
        if relative != self._relative_path(ref.sha256):
            raise ValueError("artifact path does not match content address")
        return path

    def remove_if_matches(self, ref: ArtifactRef) -> bool:
        path = self.resolve(ref)
        self._assert_static_namespace(path)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(mode):
            raise ValueError("artifact path must be a regular file")
        data = path.read_bytes()
        if len(data) != ref.size_bytes or sha256_hex(data) != ref.sha256:
            raise ValueError("artifact hash mismatch")
        path.unlink()
        return True

    @staticmethod
    def _relative_path(digest: str) -> Path:
        return Path("sha256") / digest[:2] / digest

    def _verify_existing(self, target: Path, digest: str) -> None:
        self._assert_static_namespace(target)
        self._require_regular_file(target)
        if sha256_hex(target.read_bytes()) != digest:
            raise ValueError("artifact hash mismatch for existing content address")

    def _contained(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact path escapes configured root")
        candidate = self._root / relative
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError("artifact path escapes configured root") from error
        self._assert_static_namespace(candidate)
        if not candidate.resolve().is_relative_to(self._root):
            raise ValueError("artifact path escapes configured root")
        return candidate

    @staticmethod
    def _assert_static_namespace(path: Path) -> None:
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
                raise ValueError("artifact namespace contains a symlink or reparse point")

    def _require_regular_file(self, path: Path) -> None:
        self._assert_static_namespace(path)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            raise ValueError("artifact is unavailable") from None
        if not stat.S_ISREG(mode):
            raise ValueError("artifact path must be a regular file")
