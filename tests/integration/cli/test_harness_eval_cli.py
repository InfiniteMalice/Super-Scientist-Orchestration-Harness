import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from super_scientist.cli import harness_eval
from super_scientist.cli.main import app

runner = CliRunner()


class CliResult(Protocol):
    stdout: str


class FakeRecord(BaseModel):
    identifier: str = "record-1"
    campaign_id: str = "campaign-1"


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


@pytest.mark.parametrize("command", ["create", "record", "report"])
def test_harness_eval_commands_are_registered(command: str) -> None:
    result = runner.invoke(app, ["harness-eval", command, "--help"])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", ["create", "record"])
def test_harness_eval_mutations_use_strict_json_envelope(
    tmp_path: Path,
    command: str,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_bytes(b"{")

    result = runner.invoke(
        app,
        [
            "harness-eval",
            command,
            "--root",
            str(tmp_path),
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == f"harness-eval {command}"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert result.stdout.count("\n") == 1


def test_harness_eval_report_rejects_non_stable_identifier(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "harness-eval",
            "report",
            "--root",
            str(tmp_path),
            "../campaign",
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == "harness-eval report"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"


def test_harness_eval_report_missing_campaign_uses_not_found(
    tmp_path: Path,
) -> None:
    initialized = runner.invoke(app, ["init", "--root", str(tmp_path), "--json"])
    assert initialized.exit_code == 0, initialized.output

    result = runner.invoke(
        app,
        [
            "harness-eval",
            "report",
            "--root",
            str(tmp_path),
            "missing-campaign",
            "--json",
        ],
    )

    assert result.exit_code == 4
    payload = _json_payload(result)
    assert payload["command"] == "harness-eval report"
    assert payload["errors"] == [{"code": "NOT_FOUND", "message": "missing-campaign"}]


@pytest.mark.parametrize(
    "record_type",
    [None, 7, "unsupported"],
)
def test_harness_eval_record_rejects_unknown_record_types_as_invalid_arguments(
    tmp_path: Path,
    record_type: object,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({"record_type": record_type}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "harness-eval",
            "record",
            "--root",
            str(tmp_path),
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _json_payload(result)["errors"][0]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    ("record_type", "proposal_kind"),
    [
        ("iteration", "record_harness_iteration"),
        ("protected_result", "record_harness_protected_result"),
        ("confound", "record_harness_confound"),
        ("decision", "decide_harness_campaign"),
    ],
)
def test_harness_eval_record_selects_only_fixed_proposal_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_type: str,
    proposal_kind: str,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps({"record_type": record_type, "record": "retained"}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def capture_submission(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(harness_eval, "submit_json_mutation", capture_submission)

    result = runner.invoke(
        app,
        [
            "harness-eval",
            "record",
            "--root",
            str(tmp_path),
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["proposal_kind"] == proposal_kind
    assert captured["payload"] == {"record": "retained"}


def test_harness_eval_report_returns_only_campaign_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SimpleNamespace(engine=object())
    unit_of_work = SimpleNamespace(connection=object())
    monkeypatch.setattr(
        harness_eval,
        "build_runtime",
        lambda _root: nullcontext(runtime),
    )
    monkeypatch.setattr(
        harness_eval,
        "DatabaseUnitOfWork",
        lambda _engine: nullcontext(unit_of_work),
    )
    campaign = FakeRecord(identifier="campaign", campaign_id="campaign-1")
    monkeypatch.setattr(
        harness_eval,
        "HarnessCampaignRepository",
        lambda _connection: SimpleNamespace(get=lambda _identifier: campaign),
    )

    def list_repository(_connection: object) -> object:
        return SimpleNamespace(
            list_all=lambda: (
                FakeRecord(identifier="kept", campaign_id="campaign-1"),
                FakeRecord(identifier="other", campaign_id="campaign-2"),
            )
        )

    for repository_name in (
        "HarnessPartitionManifestRepository",
        "HarnessBudgetRepository",
        "HarnessObservationRepository",
        "HarnessMetricRepository",
        "HarnessConfoundRepository",
        "HarnessDecisionRepository",
    ):
        monkeypatch.setattr(harness_eval, repository_name, list_repository)

    result = runner.invoke(
        app,
        [
            "harness-eval",
            "report",
            "--root",
            str(tmp_path),
            "campaign-1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = _json_payload(result)["data"]
    assert data["campaign"]["identifier"] == "campaign"
    for key in (
        "partition_manifests",
        "budgets",
        "observations",
        "metrics",
        "confounds",
        "decisions",
    ):
        assert [record["identifier"] for record in data[key]] == ["kept"]


def test_harness_eval_report_fails_closed_without_an_active_unit_of_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SimpleNamespace(engine=object())
    unit_of_work = SimpleNamespace(connection=None)
    monkeypatch.setattr(
        harness_eval,
        "build_runtime",
        lambda _root: nullcontext(runtime),
    )
    monkeypatch.setattr(
        harness_eval,
        "DatabaseUnitOfWork",
        lambda _engine: nullcontext(unit_of_work),
    )

    result = runner.invoke(
        app,
        [
            "harness-eval",
            "report",
            "--root",
            str(tmp_path),
            "campaign-1",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert _json_payload(result)["errors"][0]["code"] == "COMMAND_FAILED"
