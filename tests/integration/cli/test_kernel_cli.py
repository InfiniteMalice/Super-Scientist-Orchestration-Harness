import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Protocol

import pytest
from sqlalchemy.exc import SQLAlchemyError
from typer.testing import CliRunner

from super_scientist.cli import kernel
from super_scientist.cli.main import app

runner = CliRunner()


class CliResult(Protocol):
    stdout: str


def tracked_engines(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    real_create = kernel.create_database_engine
    states: list[dict[str, object]] = []

    def create(url: str) -> object:
        engine = real_create(url)
        original_dispose = engine.dispose
        state: dict[str, object] = {"engine": engine, "dispose_calls": 0}

        def dispose(*args: object, **kwargs: object) -> None:
            state["dispose_calls"] = int(state["dispose_calls"]) + 1
            original_dispose(*args, **kwargs)

        monkeypatch.setattr(engine, "dispose", dispose)
        states.append(state)
        return engine

    monkeypatch.setattr(kernel, "create_database_engine", create)
    return states


def json_payload(result: CliResult) -> dict[str, object]:
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert set(payload) == {"command", "data", "decision", "errors", "schema_version", "success"}
    return payload


def initialize_fixture(root: Path) -> dict[str, object]:
    result = runner.invoke(app, ["init", "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output
    return json_payload(result)


def claim_arguments(root: Path) -> list[str]:
    return [
        "claim",
        "propose",
        "--root",
        str(root),
        "--proposition",
        "supported",
        "--scope",
        "toy",
        "--system",
        "fixture",
        "--modality",
        "observed",
        "--json",
    ]


def test_init_emits_versioned_json_and_is_idempotent(tmp_path: Path) -> None:
    first = initialize_fixture(tmp_path)
    second = initialize_fixture(tmp_path)

    assert first["command"] == "init"
    assert first["success"] is True
    assert first["data"] == second["data"]


def test_init_disposes_every_created_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = tracked_engines(monkeypatch)

    result = runner.invoke(app, ["init", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    assert states
    assert all(state["dispose_calls"] == 1 for state in states)


def test_runtime_command_disposes_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_fixture(tmp_path)
    states = tracked_engines(monkeypatch)

    result = runner.invoke(app, ["transaction", "list", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    assert len(states) == 1
    assert states[0]["dispose_calls"] == 1


def test_runtime_build_failure_disposes_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_fixture(tmp_path)
    database = sqlite3.connect(tmp_path / "scientist-harness.db")
    try:
        database.execute("DELETE FROM governance_state")
        database.commit()
    finally:
        database.close()
    states = tracked_engines(monkeypatch)

    result = runner.invoke(app, ["transaction", "list", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 2
    assert len(states) == 1
    assert states[0]["dispose_calls"] == 1


def test_init_rejects_policy_change_with_json_error(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)
    (tmp_path / "governance-policy.json").write_text(
        '{"required_claim_checks":["source_exists"]}', encoding="utf-8"
    )

    result = runner.invoke(app, ["init", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 2
    payload = json_payload(result)
    assert payload["command"] == "init"
    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["decision"] is None
    assert payload["errors"] == [
        {
            "code": "POLICY_CHANGE_REJECTED",
            "message": "changing an initialized governance policy requires the approval workflow",
        }
    ]


def test_init_reports_malformed_policy_as_json_error(tmp_path: Path) -> None:
    (tmp_path / "governance-policy.json").write_text("{", encoding="utf-8")

    result = runner.invoke(app, ["init", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 2
    payload = json_payload(result)
    assert payload["command"] == "init"
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "INVALID_POLICY"


def test_init_reports_migration_failure_as_json_error(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    def fail_upgrade(url: str) -> None:
        del url
        raise SQLAlchemyError("migration unavailable")

    monkeypatch.setattr(kernel, "upgrade_database", fail_upgrade)

    result = runner.invoke(app, ["init", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 2
    payload = json_payload(result)
    assert payload["success"] is False
    assert payload["errors"] == [{"code": "STORAGE_ERROR", "message": "migration unavailable"}]


def test_uninitialized_runtime_reports_json_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["transaction", "list", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 2
    payload = json_payload(result)
    assert payload["command"] == "transaction list"
    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["decision"] is None
    assert payload["errors"][0]["code"] == "WORKSPACE_NOT_INITIALIZED"


def test_json_missing_file_parse_error_uses_one_versioned_envelope(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "evidence",
            "add",
            "--root",
            str(tmp_path),
            "--source",
            "fixture://missing",
            "--file",
            str(tmp_path / "missing.txt"),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = json_payload(result)
    assert payload["command"] == "evidence add"
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert result.stdout.count("\n") == 1


def test_json_missing_option_parse_error_uses_one_versioned_envelope(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "claim",
            "propose",
            "--root",
            str(tmp_path),
            "--proposition",
            "claim",
            "--scope",
            "fixture",
            "--system",
            "fixture",
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = json_payload(result)
    assert payload["command"] == "claim propose"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert result.stdout.count("\n") == 1


def test_json_invalid_option_value_uses_one_versioned_envelope(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)

    result = runner.invoke(app, [*claim_arguments(tmp_path), "--self-approve=maybe"])

    assert result.exit_code == 2
    payload = json_payload(result)
    assert payload["command"] == "claim propose"
    assert payload["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert result.stdout.count("\n") == 1


def test_human_parse_errors_keep_typer_rendering(tmp_path: Path) -> None:
    result = runner.invoke(app, ["claim", "propose", "--root", str(tmp_path)])

    assert result.exit_code == 2
    assert "Usage:" in result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


def test_evidence_add_replays_with_stable_identity_and_keeps_projections(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)
    input_file = tmp_path / "observation.txt"
    input_file.write_text("observation", encoding="utf-8")
    arguments = [
        "evidence",
        "add",
        "--root",
        str(tmp_path),
        "--source",
        "fixture://observation",
        "--file",
        str(input_file),
        "--json",
    ]

    first_result = runner.invoke(app, arguments)
    second_result = runner.invoke(app, arguments)

    assert first_result.exit_code == 0, first_result.output
    assert second_result.exit_code == 0, second_result.output
    first = json_payload(first_result)
    second = json_payload(second_result)
    assert first["data"] == second["data"]
    assert first["decision"]["accepted"] is True
    assert second["decision"]["accepted"] is True
    assert second["decision"]["replayed"] is True

    evidence_id = first["data"]["evidence_id"]
    show = runner.invoke(
        app,
        ["evidence", "show", "--root", str(tmp_path), evidence_id, "--json"],
    )
    transactions = runner.invoke(app, ["transaction", "list", "--root", str(tmp_path), "--json"])
    audit = runner.invoke(app, ["audit", "verify", "--root", str(tmp_path), "--json"])

    assert show.exit_code == 0, show.output
    assert json_payload(show)["data"]["evidence_id"] == evidence_id
    assert transactions.exit_code == 0, transactions.output
    assert len(json_payload(transactions)["data"]) == 1
    assert audit.exit_code == 0, audit.output
    assert json_payload(audit)["data"]["checked_events"] == 1


def test_claim_propose_replays_with_stable_identity_and_history(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)

    first_result = runner.invoke(app, claim_arguments(tmp_path))
    second_result = runner.invoke(app, claim_arguments(tmp_path))

    assert first_result.exit_code == 0, first_result.output
    assert second_result.exit_code == 0, second_result.output
    first = json_payload(first_result)
    second = json_payload(second_result)
    assert first["data"] == second["data"]
    assert first["decision"]["accepted"] is True
    assert second["decision"]["replayed"] is True

    claim_id = first["data"]["claim_id"]
    history = runner.invoke(
        app,
        ["claim", "history", "--root", str(tmp_path), claim_id, "--json"],
    )
    transactions = runner.invoke(app, ["transaction", "list", "--root", str(tmp_path), "--json"])
    audit = runner.invoke(app, ["audit", "verify", "--root", str(tmp_path), "--json"])

    assert history.exit_code == 0, history.output
    assert len(json_payload(history)["data"]) == 1
    assert transactions.exit_code == 0, transactions.output
    assert len(json_payload(transactions)["data"]) == 1
    assert audit.exit_code == 0, audit.output
    assert json_payload(audit)["data"]["checked_events"] == 1


def test_rejected_claim_returns_nonzero(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)
    result = runner.invoke(app, [*claim_arguments(tmp_path)[:-1], "--self-approve", "--json"])

    assert result.exit_code == 2
    payload = json_payload(result)
    assert payload["decision"]["reasons"][0]["code"] == "SELF_APPROVAL"


def test_missing_evidence_returns_not_found_envelope(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["evidence", "show", "--root", str(tmp_path), "missing", "--json"],
    )

    assert result.exit_code == 4
    payload = json_payload(result)
    assert payload["success"] is False
    assert payload["errors"] == [{"code": "NOT_FOUND", "message": "missing"}]


def test_audit_corruption_returns_invalid_envelope(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)
    first_claim = runner.invoke(app, claim_arguments(tmp_path))
    assert first_claim.exit_code == 0, first_claim.output
    database = sqlite3.connect(tmp_path / "scientist-harness.db")
    try:
        database.execute("DROP TRIGGER audit_events_no_update")
        database.execute("UPDATE audit_events SET event_hash = ? WHERE sequence = 1", ("f" * 64,))
        database.commit()
    finally:
        database.close()

    result = runner.invoke(app, ["audit", "verify", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 3
    payload = json_payload(result)
    assert payload["command"] == "audit verify"
    assert payload["success"] is False
    assert payload["data"]["valid"] is False
    assert payload["errors"][0]["code"] == "AUDIT_INTEGRITY_ERROR"


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_audit_verify_checks_authoritative_artifacts(tmp_path: Path, damage: str) -> None:
    initialize_fixture(tmp_path)
    input_file = tmp_path / "observation.txt"
    content = b"authoritative observation"
    input_file.write_bytes(content)
    added = runner.invoke(
        app,
        [
            "evidence",
            "add",
            "--root",
            str(tmp_path),
            "--source",
            "fixture://artifact-integrity",
            "--file",
            str(input_file),
            "--json",
        ],
    )
    assert added.exit_code == 0, added.output
    digest = hashlib.sha256(content).hexdigest()
    artifact_path = tmp_path / "artifacts" / "sha256" / digest[:2] / digest
    if damage == "missing":
        artifact_path.unlink()
    else:
        artifact_path.write_bytes(b"tampered")

    result = runner.invoke(app, ["audit", "verify", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 3
    payload = json_payload(result)
    assert payload["command"] == "audit verify"
    assert payload["success"] is False
    assert payload["data"]["valid"] is False
    assert payload["errors"][0]["code"] == "AUDIT_INTEGRITY_ERROR"
