from __future__ import annotations

# Subprocess authority is constrained to the immutable CHECKS registry below.
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from dataclasses import dataclass
from glob import glob
from pathlib import Path
from shutil import rmtree


@dataclass(frozen=True)
class QualityCheck:
    name: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class QualityCheckResult:
    name: str
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""

    @property
    def status(self) -> str:
        if self.returncode is None:
            return "not_run"
        return "passed" if self.returncode == 0 else "failed"


QualityReporter = Callable[[QualityCheckResult], None]


PYTHON = sys.executable
CHECKS = (
    QualityCheck("format", (PYTHON, "-m", "ruff", "format", "--check", ".")),
    QualityCheck("lint", (PYTHON, "-m", "ruff", "check", ".")),
    QualityCheck("types", (PYTHON, "-m", "mypy", "src")),
    QualityCheck(
        "tests",
        (PYTHON, "-m", "pytest", "--cov=super_scientist", "--cov-branch", "--cov-fail-under=90"),
    ),
    QualityCheck("security", (PYTHON, "-m", "bandit", "-q", "-r", "src")),
    QualityCheck("dependencies", (PYTHON, "-m", "pip_audit")),
    QualityCheck("build", (PYTHON, "-m", "build")),
    QualityCheck("package", (PYTHON, "-m", "twine", "check", "dist/*")),
)


def _report_not_run(checks: tuple[QualityCheck, ...], reporter: QualityReporter) -> None:
    for check in checks:
        reporter(QualityCheckResult(check.name, check.argv, None))


def run_quality_gate(reporter: QualityReporter | None = None) -> int:
    for index, check in enumerate(CHECKS):
        argv = check.argv
        if check.name == "build":
            distribution_directory = Path("dist")
            if distribution_directory.exists():
                rmtree(distribution_directory)
        if check.name == "package":
            distributions = tuple(sorted(glob("dist/*")))
            if not distributions:
                message = "no distributions matched dist/*"
                if reporter is None:
                    print(message, file=sys.stderr)
                else:
                    reporter(QualityCheckResult(check.name, argv, 1, stderr=message))
                return 1
            argv = (*check.argv[:-1], *distributions)
        if reporter is None:
            # argv comes only from the fixed registry.
            returncode = subprocess.run(  # nosec B603
                argv,
                check=False,
            ).returncode
        else:
            # argv comes only from the fixed registry.
            completed = subprocess.run(  # nosec B603
                argv,
                check=False,
                capture_output=True,
                text=True,
            )
            reporter(
                QualityCheckResult(
                    check.name,
                    argv,
                    completed.returncode,
                    completed.stdout,
                    completed.stderr,
                )
            )
            returncode = completed.returncode
        if returncode != 0:
            if reporter is not None:
                _report_not_run(CHECKS[index + 1 :], reporter)
            return returncode
    return 0
