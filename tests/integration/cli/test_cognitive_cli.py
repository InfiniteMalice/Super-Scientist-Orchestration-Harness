from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Protocol

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from super_scientist.application.cognitive.reader import (
    CognitiveRecordKind,
    CognitiveRecordReader,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.cli import cognitive as cognitive_cli
from super_scientist.cli.kernel import CliBoundaryError
from super_scientist.cli.main import app
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.transactions.models import Approval, RecordCapabilityProfile
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from tests.integration.application.test_cognitive_workspace_integrity import (
    _governed_policy,
    _profile_for_policy,
)
from tests.integration.application.test_workspace_exchange import FixedClock

runner = CliRunner()

EXPECTED_KINDS = {
    "capability-profile",
    "cohort-plan",
    "diversity-assessment",
    "collaboration-session",
    "peer-request",
    "peer-contribution",
    "topology-event",
    "collaboration-termination",
    "procedure-compilation",
    "method-direction-outcome",
    "compiled-progress-plan-binding",
    "guidance-protocol",
    "guidance-cell",
    "model-harness-protocol",
    "model-harness-cell",
    "model-harness-analysis",
    "harness-trace",
    "reward-assessment",
}

WINDOWS_PROHIBITED_UNC_SERVER_CHARACTERS = tuple(chr(code) for code in range(32)) + tuple(
    ' <>:"|?*'
)
WINDOWS_PROHIBITED_UNC_SHARE_CHARACTERS = tuple(chr(code) for code in range(32)) + tuple(
    '"[]:|<>+=;,*?'
)


def _windows_character_id(character: str) -> str:
    return (
        f"control-{ord(character):02x}"
        if ord(character) < 32
        else {
            " ": "space",
            "<": "less-than",
            ">": "greater-than",
            ":": "colon",
            '"': "quote",
            "[": "left-bracket",
            "]": "right-bracket",
            "|": "pipe",
            "+": "plus",
            "=": "equals",
            ";": "semicolon",
            ",": "comma",
            "?": "question",
            "*": "asterisk",
        }[character]
    )


WINDOWS_PROHIBITED_UNC_SERVER_CHARACTER_IDS = tuple(
    _windows_character_id(character) for character in WINDOWS_PROHIBITED_UNC_SERVER_CHARACTERS
)
WINDOWS_PROHIBITED_UNC_SHARE_CHARACTER_IDS = tuple(
    _windows_character_id(character) for character in WINDOWS_PROHIBITED_UNC_SHARE_CHARACTERS
)


class CliResult(Protocol):
    stdout: str


def _payload(result: CliResult) -> dict[str, object]:
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "command",
        "data",
        "decision",
        "errors",
        "schema_version",
        "success",
    }
    return payload


def _workspace_state(root: Path) -> tuple[tuple[str, int, bytes | None], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(root.rglob("*"))
    )


@pytest.fixture
def populated_workspace(tmp_path: Path) -> tuple[Path, object]:
    workspace = (
        tmp_path / " leading-space" / "inner space.and.dot" / "unicode-root\N{NO-BREAK SPACE}"
    )
    workspace.mkdir(parents=True)
    policy = _governed_policy()
    database_url = f"sqlite:///{(workspace / 'scientist-harness.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(workspace / "artifacts")
    try:
        with DatabaseUnitOfWork(engine) as unit_of_work:
            unit_of_work.repositories().policies.add_and_activate(policy, FixedClock().now())
        profile = _profile_for_policy(policy)
        coordinator = TransactionCoordinator(
            lambda: DatabaseUnitOfWork(engine),
            policy,
            FixedClock(),
            artifacts,
        )
        decision = coordinator.submit(
            RecordCapabilityProfile(
                proposal_id="cognitive-cli-profile-proposal",
                idempotency_key="cognitive-cli-profile-proposal",
                proposer=ActorIdentity(
                    actor_id="cognitive-cli",
                    kind=ActorKind.SERVICE,
                    created_at=FixedClock().now(),
                ),
                approval=Approval(
                    approver=ActorIdentity(
                        actor_id="cognitive-cli-reviewer",
                        kind=ActorKind.HUMAN,
                        created_at=FixedClock().now(),
                    ),
                    approved_at=FixedClock().now(),
                ),
                profile=profile,
            )
        )
        assert decision.accepted is True, decision
    finally:
        engine.dispose()
    return workspace, profile


def test_cognitive_record_kind_is_closed_and_reader_has_only_point_lookup() -> None:
    assert {kind.value for kind in CognitiveRecordKind} == EXPECTED_KINDS
    assert len(CognitiveRecordKind) == 18
    assert (
        set(CognitiveRecordReader.__dict__)
        & {
            "add",
            "append",
            "delete",
            "import_records",
            "list_all",
            "run",
            "submit",
            "update",
        }
        == set()
    )


def test_cognitive_reader_routes_every_fixed_kind_with_point_lookups_only(
    populated_workspace: tuple[Path, object],
) -> None:
    workspace, expected_profile = populated_workspace
    before = _workspace_state(workspace)
    engine = create_database_engine(f"sqlite:///{(workspace / 'scientist-harness.db').as_posix()}")
    try:
        with engine.connect() as connection:
            reader = CognitiveRecordReader(connection)
            for kind in CognitiveRecordKind:
                record = reader.get(kind, "profile-peer-a")
                assert record == (
                    expected_profile if kind is CognitiveRecordKind.CAPABILITY_PROFILE else None
                )

            hooks: list[str] = []

            class HostileIdentifier(str):
                def strip(self, *args: object, **kwargs: object) -> str:
                    del args, kwargs
                    hooks.append("strip")
                    raise AssertionError("hostile identifier hook")

            with pytest.raises(ValueError, match="exact text"):
                reader.get(
                    CognitiveRecordKind.CAPABILITY_PROFILE,
                    HostileIdentifier("profile-peer-a"),
                )
            assert hooks == []
    finally:
        engine.dispose()
    assert _workspace_state(workspace) == before


def test_cognitive_workspace_connection_cannot_mutate_database(
    populated_workspace: tuple[Path, object],
) -> None:
    workspace, _ = populated_workspace
    before = _workspace_state(workspace)
    engine = cognitive_cli._read_only_engine(workspace / "scientist-harness.db")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 1
            with pytest.raises(OperationalError, match="readonly database"):
                connection.exec_driver_sql("CREATE TABLE forbidden_mutation (value INTEGER)")
    finally:
        engine.dispose()
    assert _workspace_state(workspace) == before


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param(" profile-peer-a", id="leading-space"),
        pytest.param("profile-peer-a ", id="trailing-space"),
        pytest.param("\tprofile-peer-a\n", id="ascii-whitespace"),
        pytest.param("\N{NO-BREAK SPACE}profile-peer-a\N{NO-BREAK SPACE}", id="unicode-whitespace"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param("x" * 201, id="max-plus-one"),
    ],
)
def test_cognitive_reader_rejects_noncanonical_identifier_without_query(
    populated_workspace: tuple[Path, object],
    identifier: str,
) -> None:
    workspace, _ = populated_workspace
    before = _workspace_state(workspace)
    engine = create_database_engine(f"sqlite:///{(workspace / 'scientist-harness.db').as_posix()}")
    statements: list[str] = []

    event.listen(
        engine,
        "before_cursor_execute",
        lambda _connection, _cursor, statement, _parameters, _context, _executemany: (
            statements.append(statement)
        ),
    )
    try:
        with engine.connect() as connection:
            reader = CognitiveRecordReader(connection)
            with pytest.raises(ValueError, match="identifier"):
                reader.get(CognitiveRecordKind.CAPABILITY_PROFILE, identifier)
    finally:
        engine.dispose()
    assert statements == []
    assert _workspace_state(workspace) == before


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param("x" * 200, id="exact-maximum"),
        pytest.param("\N{GREEK SMALL LETTER PI}-canonical", id="unicode"),
    ],
)
def test_cognitive_reader_accepts_exact_canonical_bounded_identifiers(
    populated_workspace: tuple[Path, object],
    identifier: str,
) -> None:
    workspace, _ = populated_workspace
    before = _workspace_state(workspace)
    engine = create_database_engine(f"sqlite:///{(workspace / 'scientist-harness.db').as_posix()}")
    try:
        with engine.connect() as connection:
            assert (
                CognitiveRecordReader(connection).get(
                    CognitiveRecordKind.CAPABILITY_PROFILE,
                    identifier,
                )
                is None
            )
    finally:
        engine.dispose()
    assert _workspace_state(workspace) == before


def test_cognitive_cli_rejects_noncanonical_identifiers_before_database_open(
    populated_workspace: tuple[Path, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = populated_workspace
    before = _workspace_state(workspace)
    engine_calls: list[Path] = []

    def forbidden_engine(database: Path) -> object:
        engine_calls.append(database)
        raise AssertionError("identifier rejection must precede database open")

    monkeypatch.setattr(cognitive_cli, "_read_only_engine", forbidden_engine)
    invalid_identifiers = (
        " profile-peer-a",
        "profile-peer-a ",
        "\tprofile-peer-a\n",
        "\N{NO-BREAK SPACE}profile-peer-a\N{NO-BREAK SPACE}",
        "   ",
        "x" * 201,
    )
    for identifier in invalid_identifiers:
        for json_output in (False, True):
            arguments = [
                "cognitive",
                "inspect",
                "--root",
                str(workspace),
                "--kind",
                "capability-profile",
                "--id",
                identifier,
            ]
            if json_output:
                arguments.append("--json")

            result = runner.invoke(app, arguments)

            assert result.exit_code == 2
            envelope_text = (
                result.stdout
                if json_output
                else result.stdout.removeprefix("cognitive inspect: rejected\n")
            )
            assert json.loads(envelope_text)["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert engine_calls == []
    assert _workspace_state(workspace) == before


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param("x" * 200, id="exact-maximum"),
        pytest.param("\N{GREEK SMALL LETTER PI}-canonical", id="unicode"),
    ],
)
def test_cognitive_cli_accepts_exact_canonical_bounded_identifiers_read_only(
    populated_workspace: tuple[Path, object],
    identifier: str,
) -> None:
    workspace, _ = populated_workspace
    before = _workspace_state(workspace)

    result = runner.invoke(
        app,
        [
            "cognitive",
            "inspect",
            "--root",
            str(workspace),
            "--kind",
            "capability-profile",
            "--id",
            identifier,
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _payload(result)["errors"][0]["code"] == "MISSING_ENTITY"
    assert _workspace_state(workspace) == before


@pytest.mark.skipif(os.name != "nt", reason="Win32 path normalization is Windows-specific")
def test_cognitive_cli_rejects_windows_root_aliases_before_path_or_database_access(
    populated_workspace: tuple[Path, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = populated_workspace
    canonical = str(workspace)
    parent, leaf = canonical.rsplit("\\", maxsplit=1)
    grandparent, intermediate = parent.rsplit("\\", maxsplit=1)
    aliases = (
        canonical + " ",
        canonical + "   ",
        canonical + ".",
        canonical + "...   ",
        f"{grandparent}\\{intermediate} \\{leaf}",
        f"{grandparent}\\{intermediate}.\\{leaf}",
        f"{parent}\\\\{leaf}",
        f"{parent}\\.\\{leaf}",
        canonical + "\\",
    )
    before = _workspace_state(workspace)
    path_calls: list[object] = []
    engine_calls: list[Path] = []

    def forbidden_path(value: object) -> object:
        path_calls.append(value)
        raise AssertionError("raw root rejection must precede Path construction")

    def forbidden_engine(database: Path) -> object:
        engine_calls.append(database)
        raise AssertionError("raw root rejection must precede database open")

    monkeypatch.setattr(cognitive_cli, "Path", forbidden_path)
    monkeypatch.setattr(cognitive_cli, "_read_only_engine", forbidden_engine)
    for alias in aliases:
        for json_output in (False, True):
            arguments = [
                "cognitive",
                "inspect",
                "--root",
                alias,
                "--kind",
                "capability-profile",
                "--id",
                "profile-peer-a",
            ]
            if json_output:
                arguments.append("--json")

            result = runner.invoke(app, arguments)

            assert result.exit_code == 2
            envelope_text = (
                result.stdout
                if json_output
                else result.stdout.removeprefix("cognitive inspect: rejected\n")
            )
            assert json.loads(envelope_text)["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert path_calls == []
    assert engine_calls == []
    assert _workspace_state(workspace) == before


@pytest.mark.skipif(os.name != "nt", reason="Win32 UNC parsing is Windows-specific")
@pytest.mark.parametrize(
    ("component", "invalid_character"),
    (
        *(("server", character) for character in WINDOWS_PROHIBITED_UNC_SERVER_CHARACTERS),
        *(("share", character) for character in WINDOWS_PROHIBITED_UNC_SHARE_CHARACTERS),
    ),
    ids=(
        *(
            f"server-{_windows_character_id(character)}"
            for character in WINDOWS_PROHIBITED_UNC_SERVER_CHARACTERS
        ),
        *(
            f"share-{_windows_character_id(character)}"
            for character in WINDOWS_PROHIBITED_UNC_SHARE_CHARACTERS
        ),
    ),
)
def test_unc_components_reject_every_prohibited_character_before_path_or_database_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
    invalid_character: str,
) -> None:
    server = f"serv{invalid_character}er" if component == "server" else "server"
    share = f"sha{invalid_character}re" if component == "share" else "share"
    root = f"\\\\{server}\\{share}"
    before = _workspace_state(tmp_path)
    path_calls: list[object] = []
    engine_calls: list[Path] = []

    def forbidden_path(value: object) -> object:
        path_calls.append(value)
        raise AssertionError("UNC rejection must precede Path construction")

    def forbidden_engine(database: Path) -> object:
        engine_calls.append(database)
        raise AssertionError("UNC rejection must precede database open")

    monkeypatch.setattr(cognitive_cli, "Path", forbidden_path)
    monkeypatch.setattr(cognitive_cli, "_read_only_engine", forbidden_engine)
    result = runner.invoke(
        app,
        [
            "cognitive",
            "inspect",
            "--root",
            root,
            "--kind",
            "capability-profile",
            "--id",
            "profile-peer-a",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _payload(result)["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert path_calls == []
    assert engine_calls == []
    assert _workspace_state(tmp_path) == before


@pytest.mark.skipif(os.name != "nt", reason="Win32 UNC parsing is Windows-specific")
def test_cognitive_cli_rejects_unc_aliases_before_path_or_database_access(
    populated_workspace: tuple[Path, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = populated_workspace
    aliases = (
        "\\\\server\\share\\folder...   ",
        f"\\\\server\\{'s' * 81}",
        f"\\\\{'s' * 256}\\share",
        "\\\\server\\\\share",
        "//server//share",
        "\\\\server \\share",
        "\\\\server.\\share",
        "\\/server\\share",
        "/\\server/share",
        "\\\\server/share",
        "//server\\share",
        "\\\\\\share",
        "\\\\server",
        "\\\\server\\",
        "\\\\.\\share",
        "\\\\server\\..",
        "\\\\??\\C:\\workspace",
        "\\\\?\\C:\\workspace",
        "\\\\.\\C:\\workspace",
        "//??/C:/workspace",
        "//?/C:/workspace",
        "//./C:/workspace",
        "\\/??\\C:\\workspace",
        "/\\?/C:/workspace",
    )
    before = _workspace_state(workspace)
    path_calls: list[object] = []
    engine_calls: list[Path] = []

    def forbidden_path(value: object) -> object:
        path_calls.append(value)
        raise AssertionError("UNC rejection must precede Path construction")

    def forbidden_engine(database: Path) -> object:
        engine_calls.append(database)
        raise AssertionError("UNC rejection must precede database open")

    monkeypatch.setattr(cognitive_cli, "Path", forbidden_path)
    monkeypatch.setattr(cognitive_cli, "_read_only_engine", forbidden_engine)
    for alias in aliases:
        for json_output in (False, True):
            arguments = [
                "cognitive",
                "inspect",
                "--root",
                alias,
                "--kind",
                "capability-profile",
                "--id",
                "profile-peer-a",
            ]
            if json_output:
                arguments.append("--json")

            result = runner.invoke(app, arguments)

            assert result.exit_code == 2
            envelope_text = (
                result.stdout
                if json_output
                else result.stdout.removeprefix("cognitive inspect: rejected\n")
            )
            assert json.loads(envelope_text)["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert path_calls == []
    assert engine_calls == []
    assert _workspace_state(workspace) == before


@pytest.mark.skipif(os.name != "nt", reason="Win32 NT namespace parsing is Windows-specific")
@pytest.mark.parametrize(
    "namespace_root",
    (
        "\\??\\C:\\workspace",
        "\\dEvIcE\\HarddiskVolume1\\workspace",
        "\\GLOBAL??\\C:\\workspace",
        "\\dosDEVICES\\C:\\workspace",
        "/??/C:/workspace",
        "/DEVICE/HarddiskVolume1/workspace",
        "/global??\\C:\\workspace",
        "\\DosDevices/C:/workspace",
    ),
)
def test_cognitive_cli_rejects_single_leading_native_namespace_before_path_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    namespace_root: str,
) -> None:
    before = _workspace_state(tmp_path)
    path_calls: list[object] = []
    engine_calls: list[Path] = []

    def forbidden_path(value: object) -> object:
        path_calls.append(value)
        raise AssertionError("native namespace rejection must precede Path construction")

    def forbidden_engine(database: Path) -> object:
        engine_calls.append(database)
        raise AssertionError("native namespace rejection must precede database open")

    monkeypatch.setattr(cognitive_cli, "Path", forbidden_path)
    monkeypatch.setattr(cognitive_cli, "_read_only_engine", forbidden_engine)
    for json_output in (False, True):
        arguments = [
            "cognitive",
            "inspect",
            "--root",
            namespace_root,
            "--kind",
            "capability-profile",
            "--id",
            "profile-peer-a",
        ]
        if json_output:
            arguments.append("--json")

        result = runner.invoke(app, arguments)

        assert result.exit_code == 2
        envelope_text = (
            result.stdout
            if json_output
            else result.stdout.removeprefix("cognitive inspect: rejected\n")
        )
        assert json.loads(envelope_text)["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert path_calls == []
    assert engine_calls == []
    assert _workspace_state(tmp_path) == before


@pytest.mark.skipif(os.name != "nt", reason="Win32 path normalization is Windows-specific")
def test_windows_root_spelling_gate_preserves_root_syntax_and_canonical_unicode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = (
        "C:\\",
        "\\workspace",
        "\\\\server\\share",
        "\\\\server\\share\\",
        "\\\\server\\share\\path",
        "\\\\server\\share.",
        "\\\\server\\share.\\folder",
        "\\\\server\\share ",
        "\\\\server\\share \\folder",
        f"\\\\server\\{'s' * 80}",
        f"\\\\{'s' * 255}\\share",
        "//server/share",
        "//server/share/",
        "//server/share/path",
        "\\\\server-name\\share.name\\ leading\\inner space.and.dot\\unicode\N{NO-BREAK SPACE}",
        "\\\\serveur\N{NO-BREAK SPACE}unicode\\partage\N{NO-BREAK SPACE}\\path.name",
        "//serveur\N{NO-BREAK SPACE}/partage\N{NO-BREAK SPACE}/path.name",
        "C:\\ leading\\inner space.and.dot\\unicode\N{NO-BREAK SPACE}",
        "x" * cognitive_cli.MAX_WORKSPACE_PATH_LENGTH,
    )
    path_calls: list[object] = []

    class PathBoundaryReached(Exception):
        pass

    def reached_path_boundary(value: object) -> object:
        path_calls.append(value)
        raise PathBoundaryReached

    monkeypatch.setattr(cognitive_cli, "Path", reached_path_boundary)
    for value in accepted:
        with pytest.raises(PathBoundaryReached):
            cognitive_cli._validated_workspace_root(value)
    with pytest.raises(CliBoundaryError, match="workspace path is invalid"):
        cognitive_cli._validated_workspace_root("x" * (cognitive_cli.MAX_WORKSPACE_PATH_LENGTH + 1))
    assert path_calls == list(accepted)


def test_cognitive_inspect_returns_canonical_json_without_workspace_mutation(
    populated_workspace: tuple[Path, object],
) -> None:
    workspace, expected_profile = populated_workspace
    before = _workspace_state(workspace)

    result = runner.invoke(
        app,
        [
            "cognitive",
            "inspect",
            "--root",
            str(workspace),
            "--kind",
            "capability-profile",
            "--id",
            "profile-peer-a",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload == {
        "schema_version": 1,
        "command": "cognitive inspect",
        "success": True,
        "decision": None,
        "data": {
            "kind": "capability-profile",
            "record": expected_profile.model_dump(mode="json", warnings=False),
        },
        "errors": [],
    }
    assert result.stdout == json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    assert _workspace_state(workspace) == before


def test_cognitive_inspect_text_is_canonical_and_read_only(
    populated_workspace: tuple[Path, object],
) -> None:
    workspace, expected_profile = populated_workspace
    before = _workspace_state(workspace)

    result = runner.invoke(
        app,
        [
            "cognitive",
            "inspect",
            "--root",
            str(workspace),
            "--kind",
            "capability-profile",
            "--id",
            "profile-peer-a",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("cognitive inspect: ok\n")
    text_payload = json.loads(result.stdout.removeprefix("cognitive inspect: ok\n"))
    assert text_payload["data"]["record"] == expected_profile.model_dump(
        mode="json", warnings=False
    )
    assert _workspace_state(workspace) == before


def test_cognitive_inspect_unknown_id_is_missing_entity_and_read_only(
    populated_workspace: tuple[Path, object],
) -> None:
    workspace, _ = populated_workspace
    before = _workspace_state(workspace)

    result = runner.invoke(
        app,
        [
            "cognitive",
            "inspect",
            "--root",
            str(workspace),
            "--kind",
            "capability-profile",
            "--id",
            "missing-profile",
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _payload(result)
    assert payload["command"] == "cognitive inspect"
    assert payload["errors"] == [
        {"code": "MISSING_ENTITY", "message": "cognitive record was not found"}
    ]
    assert _workspace_state(workspace) == before


def test_cognitive_inspect_validates_full_workspace_integrity_before_read(
    populated_workspace: tuple[Path, object],
) -> None:
    workspace, _ = populated_workspace
    database_path = workspace / "scientist-harness.db"

    database = sqlite3.connect(database_path)
    try:
        database.execute("DROP TRIGGER capability_profiles_no_update")
        database.execute(
            "UPDATE capability_profiles "
            "SET record_json = json_set(record_json, '$.profile_id', 'forged-profile') "
            "WHERE profile_id = ?",
            ("profile-peer-a",),
        )
        database.commit()
    finally:
        database.close()
    before = _workspace_state(workspace)

    result = runner.invoke(
        app,
        [
            "cognitive",
            "inspect",
            "--root",
            str(workspace),
            "--kind",
            "capability-profile",
            "--id",
            "profile-peer-a",
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = _payload(result)
    assert payload["errors"][0]["code"] == "STORAGE_INTEGRITY_ERROR"
    assert _workspace_state(workspace) == before


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(
            ["--kind", "not-a-kind", "--id", "profile-peer-a"],
            id="unknown-kind",
        ),
        pytest.param(
            ["--kind", "capability-profile", "--id", "x" * 201],
            id="overlong-id",
        ),
        pytest.param(
            ["--kind", "capability-profile", "--id", "../profile-peer-a"],
            id="path-id",
        ),
    ],
)
def test_cognitive_inspect_rejects_malformed_input_without_writes(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    missing_root = tmp_path / "missing-workspace"

    result = runner.invoke(
        app,
        ["cognitive", "inspect", "--root", str(missing_root), *arguments, "--json"],
    )

    assert result.exit_code == 2
    assert _payload(result)["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert not missing_root.exists()


@pytest.mark.parametrize("root_kind", ["file", "missing-database", "overlong"])
def test_cognitive_inspect_rejects_malformed_workspace_paths_without_writes(
    tmp_path: Path,
    root_kind: str,
) -> None:
    if root_kind == "file":
        workspace_value = tmp_path / "not-a-workspace"
        workspace_value.write_bytes(b"sentinel")
        root_argument = str(workspace_value)
    elif root_kind == "missing-database":
        workspace_value = tmp_path / "empty-workspace"
        workspace_value.mkdir()
        root_argument = str(workspace_value)
    else:
        root_argument = "x" * 4_097
    before = _workspace_state(tmp_path)

    result = runner.invoke(
        app,
        [
            "cognitive",
            "inspect",
            "--root",
            root_argument,
            "--kind",
            "capability-profile",
            "--id",
            "profile-peer-a",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _payload(result)["errors"][0]["code"] in {
        "INVALID_ARGUMENT",
        "WORKSPACE_NOT_INITIALIZED",
    }
    assert _workspace_state(tmp_path) == before


def test_cognitive_cli_exposes_exactly_one_read_only_command_and_fixed_options() -> None:
    group = runner.invoke(app, ["cognitive", "--help"])
    command = runner.invoke(app, ["cognitive", "inspect", "--help"])

    assert group.exit_code == 0
    assert command.exit_code == 0
    command_lines = tuple(line.strip() for line in group.stdout.splitlines())
    assert sum("│ inspect" in line for line in command_lines) == 1
    forbidden_commands = ("import", "run", "submit", "write")
    assert all(f"│ {word}" not in group.stdout.casefold() for word in forbidden_commands)
    forbidden_options = ("--command", "--import", "--model", "--provider", "--tool")
    assert all(option not in command.stdout.casefold() for option in forbidden_options)
    assert {"--root", "--kind", "--id", "--json", "--help"}.issubset(set(command.stdout.split()))
