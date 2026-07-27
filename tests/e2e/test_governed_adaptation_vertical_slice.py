from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from super_scientist.application.transactions.coordinator import (
    TransactionCoordinator as _TransactionCoordinator,  # noqa: F401
)
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
)

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

    workspace = tmp_path / "workspace"
    result = _run_example(example, workspace)
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

    engine = create_database_engine(
        f"sqlite:///{(workspace / 'governed-adaptation.db').as_posix()}"
    )
    artifacts = FileArtifactStore(workspace / "artifacts")
    try:
        with DatabaseUnitOfWork(engine) as unit_of_work:
            repositories = unit_of_work.repositories()
            proposal_types = {
                item.proposal.proposal_type for item in repositories.transactions.list_all()
            }
            verification = verify_workspace(repositories, artifacts)
            policy_versions = tuple(
                item.policy.schema_version for item in repositories.policies.list_all()
            )
            adaptation = repositories.adaptation_integrity_snapshot()
            progress = repositories.progress_integrity_snapshot()
            trail = repositories.trail_integrity_snapshot()
            rules = repositories.rule_integrity_snapshot()
            hypotheses = repositories.hypothesis_integrity_snapshot()
    finally:
        engine.dispose()

    assert {
        "create_research_run",
        "record_progress_plan",
        "propose_hypothesis_version",
        "register_executable_model",
        "record_simulation_result",
        "record_evidence_trail_version",
        "append_progress_event",
        "decide_completion",
        "revise_hypothesis",
        "record_rule_incident",
        "propose_behavioral_rule",
        "import_reviewer_assessment",
        "consolidate_behavioral_rule",
        "create_harness_campaign",
        "decide_harness_campaign",
    } <= proposal_types
    assert adaptation.research_run_heads
    assert progress.heads
    assert trail.heads
    assert rules.heads
    assert hypotheses.heads
    assert policy_versions == (1, 2)
    assert verification.valid is True


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
