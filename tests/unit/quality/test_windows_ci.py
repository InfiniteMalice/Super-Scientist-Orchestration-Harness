from pathlib import Path


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


def test_ci_jobs_have_finite_and_workload_appropriate_timeouts() -> None:
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert workflow.count("timeout-minutes: 120") == 1
    assert workflow.count("timeout-minutes: 15") == 1
