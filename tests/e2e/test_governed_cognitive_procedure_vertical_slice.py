from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import examples.governed_cognitive_procedure_vertical_slice as example
from examples.governed_cognitive_procedure_vertical_slice import run_example


def test_toy_validator_derives_success_and_failure_from_artifact_bytes() -> None:
    expected = hashlib.sha256(b"declared deterministic bytes").hexdigest()
    validator = example.DeterministicToyValidator()

    valid = validator.validate(
        artifact_id="toy-artifact",
        artifact_bytes=b"declared deterministic bytes",
        expected_sha256=expected,
    )
    tampered = validator.validate(
        artifact_id="toy-artifact",
        artifact_bytes=b"tampered deterministic bytes",
        expected_sha256=expected,
    )

    assert valid.passed is True
    assert valid.actual_sha256 == expected
    assert tampered.passed is False
    assert tampered.expected_sha256 == expected
    assert tampered.actual_sha256 == hashlib.sha256(b"tampered deterministic bytes").hexdigest()


class _FailOnceResearchCoordinator:
    def __init__(self, failure: str) -> None:
        self._failure = failure
        self._calls = 0
        self._delegate = example.ResearchCoordinator()

    def run_declared_slice(self, submitter, coordinator, proposals):
        self._calls += 1
        if self._calls == 1:
            if self._failure == "exception":
                raise OSError("injected admission failure")
            return ()
        return self._delegate.run_declared_slice(submitter, coordinator, proposals)


@pytest.mark.parametrize("failure", ["rejection", "exception"])
def test_evidence_retainer_retries_after_failed_admission(
    tmp_path: Path,
    failure: str,
) -> None:
    runtime = example.create_local_runtime(tmp_path / failure, example.fixed_policy())
    research = _FailOnceResearchCoordinator(failure)
    retainer = example.EvidenceRetainer(
        runtime,
        research,
        example.CognitiveOrchestrationService(),
    )
    evidence = {"retry-evidence": b"retry-safe retained evidence"}
    expected_error = OSError if failure == "exception" else RuntimeError
    try:
        with pytest.raises(expected_error):
            retainer.retain(evidence)
        with runtime.uow_factory() as unit_of_work:
            assert unit_of_work.repositories().evidence.get("retry-evidence") is None

        admitted = retainer.retain(evidence)

        assert len(admitted) == 1
        with runtime.uow_factory() as unit_of_work:
            stored = unit_of_work.repositories().evidence.get("retry-evidence")
        assert stored is not None
        assert stored.evidence_id == admitted[0].evidence.evidence_id
        assert stored.content_hash == admitted[0].evidence.content_hash
        assert stored.artifact == admitted[0].evidence.artifact
        assert stored.verification_state.value == "hash_verified"
        assert retainer.retain(evidence) == ()
    finally:
        runtime.engine.dispose()


def test_runtime_initialization_failure_disposes_engine_and_removes_owned_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "failed-runtime"

    class EngineProbe:
        dispose_calls = 0

        def dispose(self) -> None:
            self.dispose_calls += 1

    engine = EngineProbe()

    def upgrade_database(_database_url: str) -> None:
        (workspace_root / "scientist-harness.db").write_bytes(b"partial database")

    class FailingPolicies:
        def add_and_activate(self, _policy, _created_at) -> None:
            raise RuntimeError("injected policy initialization failure")

    class FailingRepositories:
        policies = FailingPolicies()

    class FailingUnitOfWork:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback) -> None:
            return None

        def repositories(self) -> FailingRepositories:
            return FailingRepositories()

    monkeypatch.setattr(example, "upgrade_database", upgrade_database)
    monkeypatch.setattr(example, "create_database_engine", lambda _database_url: engine)
    monkeypatch.setattr(example, "DatabaseUnitOfWork", lambda _engine: FailingUnitOfWork())

    with pytest.raises(RuntimeError, match="injected policy initialization failure"):
        example.create_local_runtime(workspace_root, example.fixed_policy())

    assert engine.dispose_calls == 1
    assert not workspace_root.exists()


@pytest.mark.e2e
def test_example_is_cross_root_deterministic_before_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        example,
        "_verify_export_import_replay",
        lambda _runtime, _workspace_root: {"bounded_round_trip": True},
    )
    first = run_example(tmp_path / "first")
    second = run_example(tmp_path / "second")

    assert first == second
    assert first["schema_version"] == 1
    assert first["capabilities"] == [
        {
            "actor_id": "peer-critique",
            "disposition": "SATISFIED",
            "evidence_status": "VERIFIED",
        },
        {
            "actor_id": "peer-self-report",
            "disposition": "UNKNOWN",
            "evidence_status": "SELF_REPORTED",
        },
        {
            "actor_id": "peer-unknown",
            "disposition": "UNKNOWN",
            "evidence_status": "UNKNOWN",
        },
    ]
    assert first["diversity"] == {
        "independent": False,
        "member_ids": ["peer-critique", "peer-direct"],
        "model_family": "offline-model-family",
        "prompt_strategies": ["critique-first", "direct"],
    }
    assert first["collaboration"]["topology_operation"] == "DISABLE_EDGE"
    assert first["collaboration"]["challenge"]["bounded"] is True
    assert first["collaboration"]["challenge"]["request_id"] == "challenge-request"
    assert first["invalid_compilation"]["status"] == "INVALID"
    assert first["invalid_compilation"]["accepted"] is True
    assert first["valid_compilation"]["status"] == "VALID"
    assert first["valid_compilation"]["validator_passed"] is True
    assert first["valid_binding"]["accepted"] is True
    assert first["valid_binding"]["compilation_id"] == first["valid_compilation"]["compilation_id"]
    assert first["valid_binding"]["plan_id"] == "offline-progress-plan-v1"
    assert first["guidance"] == {
        "cell_count": 4,
        "conditions": [
            "FULL_PROCEDURE_GUIDANCE",
            "METHOD_ONLY",
            "OBJECTIVE_AND_DATA_ONLY",
            "OBJECTIVE_DATA_WITH_DISTRACTORS",
        ],
    }
    assert first["model_harness"] == {
        "cell_count": 4,
        "checker_passed": True,
        "harnesses": ["harness-a", "harness-b"],
        "metadata_availability": ["AVAILABLE", "UNAVAILABLE"],
        "models": ["model-a", "model-b"],
    }
    assert first["invalid_reward"]["accepted"] is True
    assert (
        first["invalid_reward"]["checker_actual_hash"]
        == hashlib.sha256(example.TAMPERED_EVIDENCE).hexdigest()
    )
    assert first["invalid_reward"]["checker_expected_hash"] == example.BASE_HASH
    assert first["invalid_reward"]["checker_passed"] is False
    assert first["invalid_reward"]["decision_code"] is None
    assert first["invalid_reward"]["reward"] == "HIGH"
    assert first["invalid_reward"]["status"] == "INVALID"
    assert first["invalid_reward"]["promotion_evidence"] is False
    assert first["workspace"] == {"bounded_round_trip": True}


@pytest.mark.e2e
def test_json_script_emits_one_stable_object_without_stderr(tmp_path: Path) -> None:
    script = Path("examples/governed_cognitive_procedure_vertical_slice.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path / "script"), "--json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=14400,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    payload = json.loads(completed.stdout)
    assert type(payload) is dict
    assert payload["workspace"]["verified"] is True
    assert payload["workspace"]["import_verified"] is True
    assert payload["workspace"]["replay_verified"] is True
    assert payload["workspace"]["exported_record_count"] > 0
    assert payload["valid_compilation"]["validator_passed"] is True
    assert payload["model_harness"]["checker_passed"] is True
    assert payload["invalid_reward"]["checker_passed"] is False
    assert payload["invalid_reward"]["promotion_evidence"] is False
