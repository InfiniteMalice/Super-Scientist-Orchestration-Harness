from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

STEP_CODES = (
    "initialize_v1_kernel",
    "approve_v1_to_v2_transition",
    "add_synthetic_source_evidence",
    "create_research_run_and_progress_plan",
    "propose_competing_thermal_hypotheses",
    "register_builtin_thermal_simulator",
    "record_predictions_and_falsification_criteria",
    "construct_and_validate_natural_evidence_trail",
    "validate_partial_progress",
    "reject_false_finish",
    "preserve_failed_hypothesis_and_revision",
    "record_incident_and_propose_rule",
    "import_five_reviewer_roles",
    "consolidate_canonical_boundary_rule",
    "preserve_incident_regression_cases",
    "link_rule_and_verify_source_mapping",
    "compare_matched_budget_harness_candidate",
    "reject_benchmark_specific_discovery_gain",
    "admit_held_out_transfer_candidate",
    "export_self_improvement_measurement_report",
    "verify_workspace_and_mixed_policy_audit",
)


def _run_example(example: Path, workspace: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(example), "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    return result


@pytest.mark.e2e
def test_governed_adaptation_vertical_slice(tmp_path: Path) -> None:
    example = Path("examples/governed_adaptation_vertical_slice.py")
    assert example.is_file()

    result = _run_example(example, tmp_path / "workspace")
    assert result["policy_versions"] == [1, 2]
    assert result["false_finish_rejected"] is True
    assert result["failed_hypothesis_preserved"] is True
    assert result["first_harness_candidate_status"] == "BENCHMARK_SPECIFIC"
    assert result["second_harness_candidate_status"] == "ADMITTED"
    assert result["audit_valid"] is True
    steps = result["steps"]
    assert isinstance(steps, list)
    assert [step["number"] for step in steps] == list(range(1, 22))
    assert [step["code"] for step in steps] == list(STEP_CODES)
    assert all(step["completed"] is True for step in steps)


@pytest.mark.e2e
def test_vertical_slice_is_byte_deterministic_and_contains_no_live_paths(
    tmp_path: Path,
) -> None:
    example = Path("examples/governed_adaptation_vertical_slice.py")
    assert example.is_file()

    first = _run_example(example, tmp_path / "first")
    second = _run_example(example, tmp_path / "second")
    serialized = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    assert first == second
    assert str(tmp_path) not in serialized
    assert "protected_expected_output" not in serialized
    assert "protected_store" not in serialized
