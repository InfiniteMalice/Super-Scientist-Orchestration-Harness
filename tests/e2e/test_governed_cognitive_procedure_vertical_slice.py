from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from examples.governed_cognitive_procedure_vertical_slice import run_example


@pytest.mark.e2e
def test_example_is_deterministic_and_exercises_rejection_and_replay(tmp_path: Path) -> None:
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
        "harnesses": ["harness-a", "harness-b"],
        "metadata_availability": ["AVAILABLE", "UNAVAILABLE"],
        "models": ["model-a", "model-b"],
    }
    assert first["invalid_reward"]["accepted"] is True
    assert first["invalid_reward"]["decision_code"] is None
    assert first["invalid_reward"]["reward"] == "HIGH"
    assert first["invalid_reward"]["status"] == "INVALID"
    assert first["invalid_reward"]["promotion_evidence"] is False
    assert first["workspace"]["verified"] is True
    assert first["workspace"]["import_verified"] is True
    assert first["workspace"]["replay_verified"] is True
    assert first["workspace"]["exported_record_count"] > 0


@pytest.mark.e2e
def test_json_script_emits_one_stable_object_without_stderr(tmp_path: Path) -> None:
    script = Path("examples/governed_cognitive_procedure_vertical_slice.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path / "script"), "--json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=7200,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    payload = json.loads(completed.stdout)
    assert type(payload) is dict
    assert payload["workspace"]["verified"] is True
