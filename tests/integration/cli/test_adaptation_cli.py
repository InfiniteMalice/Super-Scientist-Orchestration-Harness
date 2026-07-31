import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from super_scientist.cli import adaptation, kernel
from super_scientist.cli.main import app

runner = CliRunner()


class CliResult(Protocol):
    stdout: str


class FakeRecord(BaseModel):
    identifier: str = "record-1"
    change_id: str = ""
    run_id: str = ""
    trail_version_id: str = ""
    rule_id: str = ""
    passed: bool = True
    unmeasured_coverage_gaps: tuple[str, ...] = ()


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


def _initialize(root: Path) -> None:
    result = runner.invoke(app, ["init", "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output


def _stub_repositories(
    monkeypatch: pytest.MonkeyPatch,
    repositories: object,
) -> None:
    runtime = SimpleNamespace(engine=object())
    unit_of_work = SimpleNamespace(repositories=lambda: repositories)
    monkeypatch.setattr(adaptation, "build_runtime", lambda _root: nullcontext(runtime))
    monkeypatch.setattr(
        adaptation,
        "DatabaseUnitOfWork",
        lambda _engine: nullcontext(unit_of_work),
    )


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(("research-run", "create"), id="research-run-create"),
        pytest.param(("governance", "propose"), id="governance-propose"),
        pytest.param(("governance", "show"), id="governance-show"),
        pytest.param(("improvement", "classify"), id="improvement-classify"),
        pytest.param(("improvement", "report"), id="improvement-report"),
        pytest.param(("progress", "add"), id="progress-add"),
        pytest.param(("progress", "validate"), id="progress-validate"),
        pytest.param(("progress", "status"), id="progress-status"),
        pytest.param(("trail", "create"), id="trail-create"),
        pytest.param(("trail", "add-node"), id="trail-add-node"),
        pytest.param(("trail", "add-relation"), id="trail-add-relation"),
        pytest.param(("trail", "validate"), id="trail-validate"),
        pytest.param(("rule", "propose"), id="rule-propose"),
        pytest.param(("rule", "review", "import"), id="rule-review-import"),
        pytest.param(("rule", "consolidate"), id="rule-consolidate"),
        pytest.param(("rule", "history"), id="rule-history"),
        pytest.param(("primitive", "propose"), id="primitive-propose"),
        pytest.param(("primitive", "evaluate"), id="primitive-evaluate"),
        pytest.param(("hypothesis", "propose"), id="hypothesis-propose"),
        pytest.param(("hypothesis", "revise"), id="hypothesis-revise"),
        pytest.param(("model", "register"), id="model-register"),
        pytest.param(("verifier", "record"), id="verifier-record"),
    ],
)
def test_adaptation_grouped_commands_are_registered(arguments: tuple[str, ...]) -> None:
    result = runner.invoke(app, [*arguments, "--help"])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("content", "error_fragment"),
    [
        pytest.param(b"{", "input is invalid", id="malformed-json"),
        pytest.param(b"\xff\xfe", "input is invalid", id="invalid-utf8"),
        pytest.param(b"[]", "JSON object", id="array"),
        pytest.param(b"null", "JSON object", id="null"),
    ],
)
def test_research_run_create_requires_strict_utf8_json_object(
    tmp_path: Path,
    content: bytes,
    error_fragment: str,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_bytes(content)

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
    assert payload["success"] is False
    assert payload["decision"] is None
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert error_fragment.lower() in payload["errors"][0]["message"].lower()
    assert result.stdout.count("\n") == 1


@pytest.mark.parametrize(
    "reserved_field",
    [
        "proposal_id",
        "idempotency_key",
        "proposal_type",
        "proposer",
        "approval",
        "module",
        "entry_point",
        "shell_command",
        "provider",
        "executable",
        "url",
        "python_source",
    ],
)
def test_json_input_cannot_supply_authority_or_runtime_extension_fields(
    tmp_path: Path,
    reserved_field: str,
) -> None:
    _initialize(tmp_path)
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({reserved_field: "attacker-controlled"}), encoding="utf-8")

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
    assert payload["decision"] is None
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
    transactions = runner.invoke(
        app,
        ["transaction", "list", "--root", str(tmp_path), "--json"],
    )
    assert transactions.exit_code == 0, transactions.output
    assert _json_payload(transactions)["data"] == []


def test_invalid_proposal_is_audited_through_trusted_attempt_and_engine_is_disposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize(tmp_path)
    input_path = tmp_path / "input.json"
    input_path.write_text("{}", encoding="utf-8")
    real_create = kernel.create_database_engine
    dispose_calls: list[int] = []

    def tracked_create(url: str) -> object:
        engine = real_create(url)
        real_dispose = engine.dispose

        def dispose(*args: object, **kwargs: object) -> None:
            dispose_calls.append(1)
            real_dispose(*args, **kwargs)

        monkeypatch.setattr(engine, "dispose", dispose)
        return engine

    monkeypatch.setattr(kernel, "create_database_engine", tracked_create)

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
    assert payload["decision"]["accepted"] is False
    assert payload["decision"]["reasons"][0]["code"] == "INVALID_PROPOSAL"
    assert dispose_calls == [1]
    transactions = runner.invoke(
        app,
        ["transaction", "list", "--root", str(tmp_path), "--json"],
    )
    stored = _json_payload(transactions)["data"]
    assert len(stored) == 1
    assert stored[0]["proposal"]["proposal_type"] == "invalid_proposal"
    assert stored[0]["proposal"]["attempted_proposal_kind"] == "create_research_run"
    audit = runner.invoke(app, ["audit", "verify", "--root", str(tmp_path), "--json"])
    assert audit.exit_code == 0, audit.output


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(("improvement", "report"), id="improvement-report"),
        pytest.param(("progress", "status"), id="progress-status"),
        pytest.param(("trail", "validate"), id="trail-validate"),
        pytest.param(("rule", "history"), id="rule-history"),
    ],
)
def test_read_commands_reject_non_stable_identifiers(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    _initialize(tmp_path)

    result = runner.invoke(
        app,
        [*arguments, "--root", str(tmp_path), "../outside", "--json"],
    )

    assert result.exit_code == 2
    payload = _json_payload(result)
    assert payload["command"] == " ".join(arguments)
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    ("arguments", "command"),
    [
        pytest.param(("improvement", "report"), "improvement report", id="improvement"),
        pytest.param(("progress", "status"), "progress status", id="progress"),
        pytest.param(("trail", "validate"), "trail validate", id="trail"),
        pytest.param(("rule", "history"), "rule history", id="rule"),
    ],
)
def test_missing_read_models_use_not_found_exit(
    tmp_path: Path,
    arguments: tuple[str, ...],
    command: str,
) -> None:
    _initialize(tmp_path)

    result = runner.invoke(
        app,
        [*arguments, "--root", str(tmp_path), "missing-record", "--json"],
    )

    assert result.exit_code == 4
    payload = _json_payload(result)
    assert payload["command"] == command
    assert payload["errors"] == [{"code": "NOT_FOUND", "message": "missing-record"}]


def test_new_human_mode_keeps_existing_rendering_shape(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_bytes(b"{")

    result = runner.invoke(
        app,
        [
            "research-run",
            "create",
            "--root",
            str(tmp_path),
            "--input",
            str(input_path),
        ],
    )

    assert result.exit_code == 2
    assert result.stdout.startswith("research-run create: rejected\n")
    assert '"schema_version": 1' in result.stdout


def test_governance_show_returns_the_active_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = FakeRecord(identifier="active-policy")
    repositories = SimpleNamespace(
        policies=SimpleNamespace(get_active=lambda: policy),
    )
    _stub_repositories(monkeypatch, repositories)

    result = runner.invoke(
        app,
        ["governance", "show", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _json_payload(result)
    assert payload["data"]["identifier"] == "active-policy"


def test_governance_show_missing_policy_is_integrity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repositories = SimpleNamespace(
        policies=SimpleNamespace(get_active=lambda: None),
    )
    _stub_repositories(monkeypatch, repositories)

    result = runner.invoke(
        app,
        ["governance", "show", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 3
    payload = _json_payload(result)
    assert payload["errors"][0]["code"] == "STORAGE_INTEGRITY_ERROR"


def test_improvement_report_returns_matching_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = SimpleNamespace(
        measurements=(
            FakeRecord(
                identifier="kept",
                change_id="change-1",
                unmeasured_coverage_gaps=("production traffic remained unmeasured",),
            ),
            FakeRecord(identifier="other", change_id="change-2"),
        )
    )
    repositories = SimpleNamespace(adaptation_integrity_snapshot=lambda: snapshot)
    _stub_repositories(monkeypatch, repositories)

    result = runner.invoke(
        app,
        ["improvement", "report", "--root", str(tmp_path), "change-1", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _json_payload(result)
    assert [record["identifier"] for record in payload["data"]] == ["kept"]
    assert payload["data"][0]["unmeasured_coverage_gaps"] == [
        "production traffic remained unmeasured"
    ]


def test_progress_status_returns_current_plan_and_related_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = FakeRecord(identifier="plan", run_id="run-1")
    summary = FakeRecord(identifier="summary", run_id="run-1")
    snapshot = SimpleNamespace(
        plans=(plan,),
        events=(FakeRecord(identifier="event", run_id="run-1"),),
        budgets=(
            FakeRecord(identifier="budget", run_id="run-1"),
            FakeRecord(identifier="other-budget", run_id="run-2"),
        ),
        checkpoints=(FakeRecord(identifier="checkpoint", run_id="run-1"),),
        completion_decisions=(FakeRecord(identifier="decision", run_id="run-1"),),
    )
    repositories = SimpleNamespace(progress_integrity_snapshot=lambda: snapshot)
    _stub_repositories(monkeypatch, repositories)
    monkeypatch.setattr(
        adaptation,
        "current_progress_plan",
        lambda plans, run_id: plan if plans and run_id == "run-1" else None,
    )
    monkeypatch.setattr(
        adaptation,
        "calculate_progress",
        lambda current, events: summary if current is plan and events else None,
    )

    result = runner.invoke(
        app,
        ["progress", "status", "--root", str(tmp_path), "run-1", "--json"],
    )

    assert result.exit_code == 0, result.output
    data = _json_payload(result)["data"]
    assert data["plan"]["identifier"] == "plan"
    assert data["summary"]["identifier"] == "summary"
    assert [record["identifier"] for record in data["budgets"]] == ["budget"]


@pytest.mark.parametrize(
    ("passed", "expected_exit", "expected_valid"),
    [
        pytest.param(True, 0, True, id="valid"),
        pytest.param(False, 3, False, id="invalid"),
    ],
)
def test_trail_validate_reports_retained_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    passed: bool,
    expected_exit: int,
    expected_valid: bool,
) -> None:
    version = FakeRecord(identifier="version", trail_version_id="version-1")
    snapshot = SimpleNamespace(
        heads=(("trail-1", "version-1", 1),),
        versions=(version,),
        checks=(
            FakeRecord(
                identifier="check",
                trail_version_id="version-1",
                passed=passed,
            ),
        ),
        assessments=(FakeRecord(identifier="assessment", trail_version_id="version-1"),),
    )
    repositories = SimpleNamespace(trail_integrity_snapshot=lambda: snapshot)
    _stub_repositories(monkeypatch, repositories)

    result = runner.invoke(
        app,
        ["trail", "validate", "--root", str(tmp_path), "trail-1", "--json"],
    )

    assert result.exit_code == expected_exit, result.output
    payload = _json_payload(result)
    assert payload["data"]["valid"] is expected_valid
    assert payload["success"] is expected_valid


def test_trail_validate_rejects_a_dangling_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = SimpleNamespace(
        heads=(("trail-1", "missing-version", 1),),
        versions=(),
    )
    repositories = SimpleNamespace(trail_integrity_snapshot=lambda: snapshot)
    _stub_repositories(monkeypatch, repositories)

    result = runner.invoke(
        app,
        ["trail", "validate", "--root", str(tmp_path), "trail-1", "--json"],
    )

    assert result.exit_code == 3
    assert _json_payload(result)["errors"][0]["code"] == "STORAGE_INTEGRITY_ERROR"


def test_rule_history_returns_only_the_requested_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = SimpleNamespace(
        versions=(
            FakeRecord(identifier="v1", rule_id="rule-1"),
            FakeRecord(identifier="v2", rule_id="rule-2"),
        )
    )
    repositories = SimpleNamespace(rule_integrity_snapshot=lambda: snapshot)
    _stub_repositories(monkeypatch, repositories)

    result = runner.invoke(
        app,
        ["rule", "history", "--root", str(tmp_path), "rule-1", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = _json_payload(result)
    assert [record["identifier"] for record in payload["data"]] == ["v1"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-category"),
        pytest.param({"category": "UNKNOWN"}, id="unknown-category"),
        pytest.param(
            {
                "category": "FORMAL_VERIFIER",
                "verification_result": {"mechanism_type": "LEARNED_JUDGE"},
            },
            id="mismatched-category",
        ),
    ],
)
def test_verifier_record_rejects_invalid_categories_as_invalid_arguments(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "verifier",
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
    ("category", "human_command"),
    [
        ("FORMAL_VERIFIER", "formal verifier record"),
        ("DETERMINISTIC_CHECKER", "deterministic checker record"),
        ("LEARNED_JUDGE", "learned judge record"),
    ],
)
def test_verifier_record_preserves_precise_human_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    human_command: str,
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "category": category,
                "verification_result": {"mechanism_type": category},
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def capture_submission(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(adaptation, "submit_json_mutation", capture_submission)

    result = runner.invoke(
        app,
        [
            "verifier",
            "record",
            "--root",
            str(tmp_path),
            "--input",
            str(input_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["human_command"] == human_command
    assert captured["proposal_kind"] == "record_verification_result"
