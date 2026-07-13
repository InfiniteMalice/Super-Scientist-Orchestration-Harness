import json

import pytest
from typer.testing import CliRunner

from super_scientist.cli import main
from super_scientist.quality.runner import CHECKS, QualityCheckResult

cli_runner = CliRunner()


def test_quality_gate_json_records_every_fixed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pass_gate(reporter: object = None) -> int:
        assert callable(reporter)
        for check in CHECKS:
            reporter(QualityCheckResult(check.name, check.argv, 0, stdout="checked"))
        return 0

    monkeypatch.setattr(main, "run_quality_gate", pass_gate, raising=False)

    result = cli_runner.invoke(main.app, ["quality-gate", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "quality-gate"
    assert payload["success"] is True
    assert payload["errors"] == []
    assert tuple(item["name"] for item in payload["data"]["checks"]) == tuple(
        check.name for check in CHECKS
    )
    assert all(item["status"] == "passed" for item in payload["data"]["checks"])
    assert all(item["returncode"] == 0 for item in payload["data"]["checks"])


def test_quality_gate_json_preserves_exact_failure_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_gate(reporter: object = None) -> int:
        assert callable(reporter)
        reporter(QualityCheckResult(CHECKS[0].name, CHECKS[0].argv, 19, stderr="failed"))
        for check in CHECKS[1:]:
            reporter(QualityCheckResult(check.name, check.argv, None))
        return 19

    monkeypatch.setattr(main, "run_quality_gate", fail_gate)

    result = cli_runner.invoke(main.app, ["quality-gate", "--json"])

    assert result.exit_code == 19
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["errors"] == [
        {"code": "QUALITY_CHECK_FAILED", "message": "format exited with status 19"}
    ]
    assert tuple(item["status"] for item in payload["data"]["checks"]) == (
        "failed",
        "not_run",
        "not_run",
        "not_run",
        "not_run",
        "not_run",
        "not_run",
        "not_run",
    )


def test_quality_gate_terminal_mode_replays_check_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pass_gate(reporter: object = None) -> int:
        assert callable(reporter)
        reporter(
            QualityCheckResult(
                CHECKS[0].name,
                CHECKS[0].argv,
                0,
                stdout="formatter output\n",
                stderr="formatter warning\n",
            )
        )
        return 0

    monkeypatch.setattr(main, "run_quality_gate", pass_gate)

    result = cli_runner.invoke(main.app, ["quality-gate"])

    assert result.exit_code == 0
    assert "formatter output" in result.output
    assert "formatter warning" in result.output
    assert "quality-gate: ok" in result.output


@pytest.mark.parametrize(
    ("arguments", "option"),
    [
        pytest.param(("--command", "ruff check ."), "--command", id="arbitrary-command"),
        pytest.param(("--path", "src"), "--path", id="arbitrary-path"),
        pytest.param(("--skip", "format"), "--skip", id="skip"),
        pytest.param(("--check", "lint"), "--check", id="check-selection"),
        pytest.param(
            ("--cov-fail-under", "0"),
            "--cov-fail-under",
            id="threshold-override",
        ),
    ],
)
def test_quality_gate_rejects_extensibility_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, str],
    option: str,
) -> None:
    called = False

    def unexpected_gate(reporter: object = None) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(main, "run_quality_gate", unexpected_gate)

    result = cli_runner.invoke(main.app, ["quality-gate", *arguments])

    assert result.exit_code == 2
    assert called is False
    assert f"No such option: {option}" in result.output
