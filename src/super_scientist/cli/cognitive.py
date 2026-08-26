from __future__ import annotations

import ntpath
import os
import sqlite3
import stat
from pathlib import Path
from typing import Annotated, Never
from urllib.parse import quote

import typer
from sqlalchemy import Engine, create_engine

from super_scientist.application.cognitive.reader import (
    CognitiveRecordKind,
    CognitiveRecordReader,
    validate_cognitive_record_id,
)
from super_scientist.application.workspace_integrity import require_workspace_integrity
from super_scientist.cli.kernel import CliBoundaryError, JsonOutput, _command_boundary
from super_scientist.cli.output import emit
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.primitives import sha256_hex
from super_scientist.providers.storage.database import configure_database_engine
from super_scientist.providers.storage.repositories import RepositorySet

cognitive_app = typer.Typer(no_args_is_help=True)

MAX_WORKSPACE_PATH_LENGTH = 4_096
MAX_WORKSPACE_PATH_BYTES = 16_384
WorkspaceRoot = Annotated[str, typer.Option("--root", metavar="PATH")]
RecordKind = Annotated[CognitiveRecordKind, typer.Option("--kind", case_sensitive=True)]
RecordId = Annotated[str, typer.Option("--id")]


def _raise_read_only() -> Never:
    raise PermissionError("cognitive inspection artifact access is read-only")


class _ReadOnlyArtifactStore:
    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        absolute = root.absolute()
        _require_static_namespace(absolute)
        if not absolute.is_dir():
            raise CliBoundaryError(
                "WORKSPACE_NOT_INITIALIZED",
                "workspace artifact store is unavailable",
            )
        self._root = absolute.resolve(strict=True)

    def put(self, data: bytes, media_type: str) -> Never:
        del data, media_type
        _raise_read_only()

    def read(self, ref: ArtifactRef) -> bytes:
        relative = Path(ref.relative_path)
        expected = Path("sha256") / ref.sha256[:2] / ref.sha256
        if relative != expected or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact path does not match content address")
        target = self._root / relative
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise ValueError("artifact path escapes configured root") from error
        _require_static_namespace(target)
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            raise ValueError("artifact is unavailable") from None
        if not stat.S_ISREG(mode) or not target.resolve(strict=True).is_relative_to(self._root):
            raise ValueError("artifact path must be a contained regular file")
        data = target.read_bytes()
        if len(data) != ref.size_bytes or sha256_hex(data) != ref.sha256:
            raise ValueError("artifact hash mismatch")
        return data


def _require_static_namespace(path: Path) -> None:
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
            raise CliBoundaryError(
                "INVALID_ARGUMENT",
                "workspace path contains a symlink or reparse point",
            )


def _raise_noncanonical_windows_root() -> Never:
    raise CliBoundaryError(
        "INVALID_ARGUMENT",
        "workspace path must use exact canonical Windows segments",
    )


def _windows_segment_is_noncanonical(segment: str) -> bool:
    return not segment or segment in {".", ".."} or segment.endswith((" ", "."))


def _require_canonical_unc_root(value: str) -> bool:
    if len(value) < 2 or value[0] not in {"/", "\\"} or value[1] not in {"/", "\\"}:
        return False
    separator = value[0]
    if value[1] != separator:
        _raise_noncanonical_windows_root()
    alternate_separator = "\\" if separator == "/" else "/"
    body = value[2:]
    if alternate_separator in body:
        _raise_noncanonical_windows_root()
    components = body.split(separator)
    if len(components) < 2:
        _raise_noncanonical_windows_root()
    server, share, *tail = components
    if (
        server == "?"
        or _windows_segment_is_noncanonical(server)
        or _windows_segment_is_noncanonical(share)
    ):
        _raise_noncanonical_windows_root()
    if tail == [""]:
        return True
    if any(_windows_segment_is_noncanonical(segment) for segment in tail):
        _raise_noncanonical_windows_root()
    return True


def _require_canonical_windows_root_text(value: str) -> None:
    if os.name != "nt" or _require_canonical_unc_root(value):
        return
    _root, tail = ntpath.splitdrive(value)
    canonical_tail = tail.replace("/", "\\")
    if canonical_tail.startswith("\\"):
        canonical_tail = canonical_tail[1:]
    if not canonical_tail:
        return
    if any(_windows_segment_is_noncanonical(segment) for segment in canonical_tail.split("\\")):
        _raise_noncanonical_windows_root()


def _validated_workspace_root(value: object) -> Path:
    if type(value) is not str:
        raise CliBoundaryError("INVALID_ARGUMENT", "workspace path must be exact text")
    if not value or len(value) > MAX_WORKSPACE_PATH_LENGTH or "\x00" in value:
        raise CliBoundaryError("INVALID_ARGUMENT", "workspace path is invalid")
    try:
        if len(value.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES:
            raise CliBoundaryError("INVALID_ARGUMENT", "workspace path is invalid")
        _require_canonical_windows_root_text(value)
        absolute = Path(value).absolute()
        _require_static_namespace(absolute)
        resolved = absolute.resolve(strict=True)
    except (OSError, UnicodeError, ValueError):
        raise CliBoundaryError("INVALID_ARGUMENT", "workspace path is invalid") from None
    if not resolved.is_dir():
        raise CliBoundaryError("INVALID_ARGUMENT", "workspace root must be a directory")
    database = resolved / "scientist-harness.db"
    if not database.is_file():
        raise CliBoundaryError(
            "WORKSPACE_NOT_INITIALIZED",
            "workspace is not initialized; run init first",
        )
    _require_static_namespace(database)
    if any(Path(f"{database}{suffix}").exists() for suffix in ("-journal", "-shm", "-wal")):
        raise CliBoundaryError(
            "WORKSPACE_BUSY",
            "workspace has an active database journal",
        )
    return resolved


def _read_only_database_connection(database: Path) -> sqlite3.Connection:
    encoded_path = quote(database.as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{encoded_path}?mode=ro",
        uri=True,
        check_same_thread=False,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
    except BaseException:
        connection.close()
        raise
    return connection


def _read_only_engine(database: Path) -> Engine:
    return configure_database_engine(
        create_engine(
            "sqlite+pysqlite://",
            creator=lambda: _read_only_database_connection(database),
            future=True,
        )
    )


@cognitive_app.command("inspect")
@_command_boundary("cognitive inspect", integrity_exit_code=3)
def cognitive_inspect(
    root: WorkspaceRoot,
    kind: RecordKind,
    record_id: RecordId,
    json_output: JsonOutput = False,
) -> None:
    if type(kind) is not CognitiveRecordKind:
        raise CliBoundaryError("INVALID_ARGUMENT", "cognitive record kind is invalid")
    try:
        identifier = validate_cognitive_record_id(record_id)
    except (TypeError, ValueError):
        raise CliBoundaryError(
            "INVALID_ARGUMENT", "cognitive record identifier is invalid"
        ) from None
    workspace = _validated_workspace_root(root)
    artifacts = _ReadOnlyArtifactStore(workspace / "artifacts")
    engine = _read_only_engine(workspace / "scientist-harness.db")
    try:
        with engine.connect() as connection:
            repositories = RepositorySet(connection)
            require_workspace_integrity(repositories, artifacts)
            record = CognitiveRecordReader(connection).get(kind, identifier)
    finally:
        engine.dispose()
    if record is None:
        raise CliBoundaryError("MISSING_ENTITY", "cognitive record was not found")
    emit(
        "cognitive inspect",
        True,
        json_output,
        data={
            "kind": kind.value,
            "record": record.model_dump(mode="json", warnings="none"),
        },
    )
