import json
from pathlib import Path

from typer.testing import CliRunner

from super_scientist.cli.main import app

runner = CliRunner()


def initialize_fixture(root: Path) -> None:
    result = runner.invoke(app, ["init", "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output


def test_init_emits_versioned_json(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "init"
    assert payload["success"] is True


def test_rejected_claim_returns_nonzero(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "claim",
            "propose",
            "--root",
            str(tmp_path),
            "--proposition",
            "unsupported",
            "--scope",
            "toy",
            "--system",
            "fixture",
            "--modality",
            "observed",
            "--self-approve",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["decision"]["reasons"][0]["code"] == "SELF_APPROVAL"
