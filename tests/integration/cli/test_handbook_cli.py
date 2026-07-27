import json
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from super_scientist.cli import handbook
from super_scientist.cli.main import app
from super_scientist.handbook.models import (
    HandbookBuildError,
    HandbookFindingCode,
    PathContainmentError,
)

runner = CliRunner()


class CliResult(Protocol):
    stdout: str


class FakeVerificationResult(BaseModel):
    valid: bool
    marker: str = "verified"


def _json_payload(result: CliResult) -> dict[str, object]:
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert set(payload) == {
        "command",
        "data",
        "decision",
        "errors",
        "schema_version",
        "success",
    }
    return payload


@pytest.mark.parametrize("command", ["build", "verify"])
def test_handbook_commands_are_registered(command: str) -> None:
    result = runner.invoke(app, ["handbook", command, "--help"])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"{", id="malformed-json"),
        pytest.param(b"\xff\xfe", id="invalid-utf8"),
        pytest.param(b"[]", id="non-object"),
    ],
)
def test_handbook_manifest_requires_strict_utf8_json_object(
    tmp_path: Path,
    content: bytes,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(content)

    result = runner.invoke(
        app,
        [
            "handbook",
            "verify",
            "--root",
            str(tmp_path),
            "--repository",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == "handbook verify"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"


def test_handbook_build_rejects_output_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    manifest = repository / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"

    result = runner.invoke(
        app,
        [
            "handbook",
            "build",
            "--root",
            str(tmp_path),
            "--repository",
            str(repository),
            "--manifest",
            str(manifest),
            "--output-dir",
            str(outside),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == "handbook build"
    assert payload["errors"][0]["code"] == "PATH_CONTAINMENT_ERROR"
    assert not outside.exists()


def test_handbook_json_parser_error_uses_command_envelope(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "handbook",
            "build",
            "--root",
            str(tmp_path),
            "--repository",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == "handbook build"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert result.stdout.count("\n") == 1


def test_handbook_verify_rejects_a_missing_manifest(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    result = runner.invoke(
        app,
        [
            "handbook",
            "verify",
            "--root",
            str(tmp_path),
            "--repository",
            str(repository),
            "--manifest",
            str(repository / "missing.json"),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["errors"] == [
        {
            "code": "INVALID_ARGUMENT",
            "message": "manifest must be an existing regular file",
        }
    ]


def test_handbook_build_writes_only_the_fixed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    manifest = repository / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output = repository / "generated"
    monkeypatch.setattr(handbook, "_manifest", lambda _path: object())
    monkeypatch.setattr(
        handbook,
        "build_handbook",
        lambda _root, _manifest: SimpleNamespace(
            json_bytes=b'{"schema_version":1}\n',
            markdown_bytes=b"# Handbook\n",
            generated_artifact_hash="a" * 64,
            manifest_hash="b" * 64,
            source_tree_hash="c" * 64,
        ),
    )

    result = runner.invoke(
        app,
        [
            "handbook",
            "build",
            "--root",
            str(tmp_path),
            "--repository",
            str(repository),
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _json_payload(result)
    assert payload["data"]["json_path"] == str(output / "handbook.json")
    assert (output / "handbook.json").read_bytes() == b'{"schema_version":1}\n'
    assert (output / "handbook.md").read_bytes() == b"# Handbook\n"
    assert sorted(path.name for path in output.iterdir()) == ["handbook.json", "handbook.md"]


def test_handbook_build_reports_verified_integrity_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    manifest = repository / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output = repository / "generated"
    monkeypatch.setattr(handbook, "_manifest", lambda _path: object())

    def fail_build(_root: Path, _manifest: object) -> object:
        raise HandbookBuildError((HandbookFindingCode.SOURCE_NOT_FOUND,))

    monkeypatch.setattr(handbook, "build_handbook", fail_build)

    result = runner.invoke(
        app,
        [
            "handbook",
            "build",
            "--root",
            str(tmp_path),
            "--repository",
            str(repository),
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = _json_payload(result)
    assert payload["errors"][0]["code"] == "HANDBOOK_INTEGRITY_ERROR"
    assert not output.exists()


@pytest.mark.parametrize(
    ("valid", "expected_exit"),
    [
        pytest.param(True, 0, id="valid"),
        pytest.param(False, 3, id="invalid"),
    ],
)
def test_handbook_verify_preserves_verification_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid: bool,
    expected_exit: int,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    manifest = repository / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(handbook, "_manifest", lambda _path: object())
    monkeypatch.setattr(
        handbook,
        "verify_handbook",
        lambda _root, _manifest: FakeVerificationResult(valid=valid),
    )

    result = runner.invoke(
        app,
        [
            "handbook",
            "verify",
            "--root",
            str(tmp_path),
            "--repository",
            str(repository),
            "--manifest",
            str(manifest),
            "--json",
        ],
    )

    assert result.exit_code == expected_exit, result.output
    payload = _json_payload(result)
    assert payload["success"] is valid
    assert payload["data"] == {"valid": valid, "marker": "verified"}


def test_handbook_verify_maps_nested_path_failures_to_boundary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    manifest = repository / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(handbook, "_manifest", lambda _path: object())

    def fail_verification(_root: Path, _manifest: object) -> object:
        raise PathContainmentError("source escaped")

    monkeypatch.setattr(handbook, "verify_handbook", fail_verification)

    result = runner.invoke(
        app,
        [
            "handbook",
            "verify",
            "--root",
            str(tmp_path),
            "--repository",
            str(repository),
            "--manifest",
            str(manifest),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["errors"] == [{"code": "PATH_CONTAINMENT_ERROR", "message": "source escaped"}]
