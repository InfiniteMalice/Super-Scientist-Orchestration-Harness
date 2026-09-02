import json
from pathlib import Path
from typing import Protocol

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from super_scientist.cli.main import app

runner = CliRunner()


class CliResult(Protocol):
    stdout: str


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


@pytest.mark.parametrize(
    ("command_path", "command"),
    [
        pytest.param(("research-run", "create"), "research-run create", id="research-run"),
        pytest.param(("governance", "propose"), "governance propose", id="governance"),
        pytest.param(("improvement", "classify"), "improvement classify", id="improvement"),
        pytest.param(("progress", "add"), "progress add", id="progress-add"),
        pytest.param(("progress", "validate"), "progress validate", id="progress-validate"),
        pytest.param(("trail", "create"), "trail create", id="trail-create"),
        pytest.param(("trail", "add-node"), "trail add-node", id="trail-node"),
        pytest.param(("trail", "add-relation"), "trail add-relation", id="trail-relation"),
        pytest.param(("rule", "propose"), "rule propose", id="rule-propose"),
        pytest.param(("rule", "review", "import"), "rule review import", id="rule-review"),
        pytest.param(("rule", "consolidate"), "rule consolidate", id="rule-consolidate"),
        pytest.param(("primitive", "propose"), "primitive propose", id="primitive-propose"),
        pytest.param(("primitive", "evaluate"), "primitive evaluate", id="primitive-evaluate"),
        pytest.param(("hypothesis", "propose"), "hypothesis propose", id="hypothesis-propose"),
        pytest.param(("hypothesis", "revise"), "hypothesis revise", id="hypothesis-revise"),
        pytest.param(("model", "register"), "model register", id="model-register"),
        pytest.param(("verifier", "record"), "verifier record", id="verifier-record"),
        pytest.param(("harness-eval", "create"), "harness-eval create", id="harness-create"),
        pytest.param(("harness-eval", "record"), "harness-eval record", id="harness-record"),
    ],
)
def test_every_json_mutation_uses_one_stable_error_envelope(
    tmp_path: Path,
    command_path: tuple[str, ...],
    command: str,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_bytes(b"{")

    result = runner.invoke(
        app,
        [
            *command_path,
            "--root",
            str(tmp_path),
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == command
    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["decision"] is None
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert result.stdout.count("\n") == 1


@given(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(),
        st.lists(st.integers(), max_size=5),
    )
)
@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_json_mutations_reject_every_non_object_top_level(
    tmp_path: Path,
    value: object,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(value), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research-run",
            "create",
            "--root",
            str(tmp_path),
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == "research-run create"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"


def test_existing_command_parse_errors_keep_the_same_envelope(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["claim", "propose", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == "claim propose"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"


def test_cognitive_inspect_parse_errors_keep_the_same_envelope(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "cognitive",
            "inspect",
            "--root",
            str(tmp_path),
            "--kind",
            "capability-profile",
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == "cognitive inspect"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
