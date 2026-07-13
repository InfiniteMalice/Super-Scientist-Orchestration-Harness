import subprocess

import pytest

from super_scientist.quality import runner
from super_scientist.quality.runner import CHECKS


def test_quality_registry_is_fixed_and_complete_for_kernel_slice() -> None:
    assert tuple(check.name for check in CHECKS) == (
        "format",
        "lint",
        "types",
        "tests",
        "security",
        "dependencies",
        "build",
        "package",
    )
    assert all(isinstance(check.argv, tuple) for check in CHECKS)


def test_report_records_failure_and_marks_remaining_checks_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_lint(
        argv: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        returncode = 7 if argv == CHECKS[1].argv else 0
        return subprocess.CompletedProcess(argv, returncode, stdout="output", stderr="error")

    monkeypatch.setattr(runner.subprocess, "run", fail_lint)
    results: list[runner.QualityCheckResult] = []

    returncode = runner.run_quality_gate(reporter=results.append)

    assert returncode == 7
    assert tuple(result.name for result in results) == tuple(check.name for check in CHECKS)
    assert tuple(result.status for result in results) == (
        "passed",
        "failed",
        "not_run",
        "not_run",
        "not_run",
        "not_run",
        "not_run",
        "not_run",
    )
    assert results[1].returncode == 7
    assert results[1].stdout == "output"
    assert results[1].stderr == "error"


def test_runner_expands_distributions_in_sorted_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def pass_check(
        argv: tuple[str, ...],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(runner.subprocess, "run", pass_check)
    monkeypatch.setattr(runner, "glob", lambda pattern: ["dist/z.whl", "dist/a.tar.gz"])

    assert runner.run_quality_gate() == 0
    assert calls[-1] == (*CHECKS[-1].argv[:-1], "dist/a.tar.gz", "dist/z.whl")


def test_runner_fails_when_no_distribution_exists(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def pass_check(
        argv: tuple[str, ...],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        assert check is False
        calls += 1
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(runner.subprocess, "run", pass_check)
    monkeypatch.setattr(runner, "glob", lambda pattern: [])

    assert runner.run_quality_gate() == 1
    assert calls == len(CHECKS) - 1
    assert capsys.readouterr().err == "no distributions matched dist/*\n"


def test_report_records_missing_distribution_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pass_check(
        argv: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", pass_check)
    monkeypatch.setattr(runner, "glob", lambda pattern: [])
    results: list[runner.QualityCheckResult] = []

    assert runner.run_quality_gate(reporter=results.append) == 1
    assert len(results) == len(CHECKS)
    assert results[-1].name == "package"
    assert results[-1].status == "failed"
    assert results[-1].stderr == "no distributions matched dist/*"


def test_runner_returns_exact_failure_without_reporting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_types(
        argv: tuple[str, ...],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        assert check is False
        calls += 1
        return subprocess.CompletedProcess(argv, 23 if argv == CHECKS[2].argv else 0)

    monkeypatch.setattr(runner.subprocess, "run", fail_types)

    assert runner.run_quality_gate() == 23
    assert calls == 3
