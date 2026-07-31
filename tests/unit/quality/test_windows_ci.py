from pathlib import Path

import yaml


def test_windows_artifact_security_job_is_focused_and_least_privilege() -> None:
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "windows-artifact-security:" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "contents: read" in workflow
    assert workflow.count("persist-credentials: false") == 2
    assert "tests/integration/storage/test_windows_reparse_artifacts.py" in workflow
    assert "secrets." not in workflow


def test_ci_installs_checkout_editably_for_source_coverage() -> None:
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert workflow.count('python -m pip install -e ".[dev]"') == 2


def test_ci_uses_pull_request_head_with_push_fallback_for_provenance() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/quality.yml").read_text(encoding="utf-8"))
    checkout_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses") == "actions/checkout@v4"
    ]

    assert len(checkout_steps) == 2
    assert all(
        step.get("with", {}).get("ref") == "${{ github.event.pull_request.head.sha || github.sha }}"
        for step in checkout_steps
    )


def test_ci_jobs_have_finite_and_workload_appropriate_timeouts() -> None:
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert workflow.count("timeout-minutes: 120") == 1
    assert workflow.count("timeout-minutes: 15") == 1
