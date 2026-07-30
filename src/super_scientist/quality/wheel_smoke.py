from __future__ import annotations

import configparser
import json
import os
import shutil
import stat
import subprocess  # nosec B404
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

# This quality check executes only fixed reviewed argv and never enables a shell.
IS_WINDOWS = os.name == "nt"
VENV_PYTHON_NAME = "python.exe" if IS_WINDOWS else "python"
VENV_CLI_NAME = "scientist-harness.exe" if IS_WINDOWS else "scientist-harness"
_VENV_BIN_DIRECTORY = "Scripts" if IS_WINDOWS else "bin"
_TEMP_PREFIX = "ssoh-wheel-smoke-"
_VENV_PYTHON_PLACEHOLDER = "{venv-python}"
_COMMAND_TIMEOUT_SECONDS = 300.0

CommandRunner = Callable[[tuple[str, ...]], subprocess.CompletedProcess[str]]
TemporaryDirectoryFactory = Callable[..., str]


@dataclass(frozen=True)
class WheelSmokePlan:
    wheel_path: Path
    install_argv: tuple[str, ...]
    smoke_argv: tuple[str, ...]


@dataclass(frozen=True)
class WheelSmokeStage:
    name: str
    status: str
    returncode: int | None
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class WheelSmokeResult:
    passed: bool
    stages: tuple[WheelSmokeStage, ...]


def _run_fixed_command(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    # Every caller supplies argv assembled from reviewed constants and verified paths.
    return subprocess.run(  # nosec B603
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )


def build_wheel_smoke_plan(distributions: tuple[Path, ...]) -> WheelSmokePlan:
    if len(distributions) != 1:
        raise ValueError("wheel smoke requires exactly one distribution")
    wheel_path = distributions[0]
    portable = PurePosixPath(wheel_path.as_posix())
    if (
        wheel_path.is_absolute()
        or portable.is_absolute()
        or portable.parts[:1] != ("dist",)
        or len(portable.parts) != 2
        or any(part in {"", ".", ".."} for part in portable.parts)
        or portable.suffix != ".whl"
    ):
        raise ValueError("wheel smoke requires one exact relative dist wheel")
    normalized = Path(*portable.parts)
    return WheelSmokePlan(
        wheel_path=normalized,
        install_argv=(
            _VENV_PYTHON_PLACEHOLDER,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-index",
            "--no-deps",
            str(normalized),
        ),
        smoke_argv=("scientist-harness", "--help", "--json"),
    )


def resolve_built_wheel(plan: WheelSmokePlan, project_root: Path) -> Path:
    root = project_root.resolve(strict=True)
    candidate = project_root / plan.wheel_path
    if _is_link_or_reparse(candidate.lstat()):
        raise ValueError("built wheel cannot be a link or reparse point")
    resolved = candidate.resolve(strict=True)
    if root not in resolved.parents or resolved.parent != root / "dist":
        raise ValueError("built wheel must be the exact file under project dist")
    if not resolved.is_file():
        raise ValueError("built wheel does not name a regular file")
    return resolved


def run_wheel_smoke(
    plan: WheelSmokePlan,
    *,
    project_root: Path,
    temp_parent: Path,
    command_runner: CommandRunner = _run_fixed_command,
    temporary_directory_factory: TemporaryDirectoryFactory = tempfile.mkdtemp,
) -> WheelSmokeResult:
    stages: list[WheelSmokeStage] = []
    temporary_path: Path | None = None
    parent: Path | None = None
    cleanup_authorized = False
    current_stage = "selection"
    try:
        wheel = resolve_built_wheel(plan, project_root)
        _verify_project_wheel(wheel)
        parent = temp_parent.resolve(strict=True)
        current_stage = "venv"
        temporary_path = Path(temporary_directory_factory(prefix=_TEMP_PREFIX, dir=str(parent)))
        cleanup_authorized = _is_verified_temporary_directory(temporary_path, parent)
        if not cleanup_authorized:
            return WheelSmokeResult(
                passed=False,
                stages=(
                    WheelSmokeStage(
                        name="venv",
                        status="failed",
                        returncode=None,
                        stderr="temporary directory failed the deletion boundary",
                    ),
                ),
            )

        venv = _run_stage(
            "venv",
            (
                sys.executable,
                "-m",
                "venv",
                "--copies",
                str(temporary_path),
            ),
            command_runner,
        )
        stages.append(venv)
        if venv.status != "passed":
            return WheelSmokeResult(False, tuple(stages))

        scripts = temporary_path / _VENV_BIN_DIRECTORY
        venv_python = scripts / VENV_PYTHON_NAME
        venv_cli = scripts / VENV_CLI_NAME
        if not _is_verified_environment_executable(venv_python, temporary_path):
            stages.append(
                WheelSmokeStage(
                    name="install",
                    status="failed",
                    returncode=None,
                    stderr="isolated environment Python is not a verified file",
                )
            )
            return WheelSmokeResult(False, tuple(stages))

        install_argv = tuple(
            str(venv_python)
            if argument == _VENV_PYTHON_PLACEHOLDER
            else str(wheel)
            if argument == str(plan.wheel_path)
            else argument
            for argument in plan.install_argv
        )
        current_stage = "install"
        install = _run_stage("install", install_argv, command_runner)
        stages.append(install)
        if install.status != "passed":
            return WheelSmokeResult(False, tuple(stages))

        if not _is_verified_environment_executable(venv_cli, temporary_path):
            stages.append(
                WheelSmokeStage(
                    name="cli-smoke",
                    status="failed",
                    returncode=None,
                    stderr="isolated environment CLI is not a verified file",
                )
            )
            return WheelSmokeResult(False, tuple(stages))
        current_stage = "cli-smoke"
        smoke = _run_stage(
            "cli-smoke",
            (str(venv_cli), *plan.smoke_argv[1:]),
            command_runner,
            accept=_is_accepted_smoke_result,
        )
        stages.append(smoke)
        return WheelSmokeResult(smoke.status == "passed", tuple(stages))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        stages.append(
            WheelSmokeStage(
                name=current_stage,
                status="failed",
                returncode=None,
                stderr=f"wheel smoke failed safely: {type(exc).__name__}",
            )
        )
        return WheelSmokeResult(False, tuple(stages))
    finally:
        if (
            cleanup_authorized
            and temporary_path is not None
            and parent is not None
            and _is_verified_temporary_directory(temporary_path, parent)
        ):
            shutil.rmtree(temporary_path)


def _run_stage(
    name: str,
    argv: tuple[str, ...],
    command_runner: CommandRunner,
    *,
    accept: Callable[[subprocess.CompletedProcess[str]], bool] | None = None,
) -> WheelSmokeStage:
    completed = command_runner(argv)
    passed = completed.returncode == 0 if accept is None else accept(completed)
    return WheelSmokeStage(
        name=name,
        status="passed" if passed else "failed",
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _is_accepted_smoke_result(completed: subprocess.CompletedProcess[str]) -> bool:
    if completed.returncode == 0:
        return True
    if completed.returncode != 2:
        return False
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    errors = payload.get("errors")
    return (
        payload.get("schema_version") == 1
        and payload.get("command") == "scientist-harness"
        and payload.get("success") is False
        and isinstance(errors, list)
        and len(errors) == 1
        and isinstance(errors[0], dict)
        and errors[0].get("code") == "INVALID_ARGUMENT"
        and errors[0].get("message") == "No such option: --json"
    )


def _is_verified_temporary_directory(candidate: Path, parent: Path) -> bool:
    try:
        if _is_link_or_reparse(candidate.lstat()) or not candidate.is_dir():
            return False
        resolved = candidate.resolve(strict=True)
    except OSError:
        return False
    return (
        resolved.parent == parent and resolved.name.startswith(_TEMP_PREFIX) and resolved != parent
    )


def _is_verified_environment_executable(candidate: Path, temporary_path: Path) -> bool:
    try:
        if _is_link_or_reparse(candidate.lstat()) or not candidate.is_file():
            return False
        resolved = candidate.resolve(strict=True)
        temporary_root = temporary_path.resolve(strict=True)
    except OSError:
        return False
    return temporary_root in resolved.parents


def _verify_project_wheel(wheel: Path) -> None:
    try:
        with zipfile.ZipFile(wheel) as archive:
            entries = tuple(archive.infolist())
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or path.suffix.casefold() == ".pth"
                ):
                    raise ValueError("built wheel contains unsafe path configuration")
                mode = (entry.external_attr >> 16) & 0xFFFF
                if mode and stat.S_ISLNK(mode):
                    raise ValueError("built wheel contains a linked member")
            entry_points = tuple(
                entry
                for entry in entries
                if PurePosixPath(entry.filename).name == "entry_points.txt"
                and ".dist-info" in PurePosixPath(entry.filename).parent.name
            )
            if len(entry_points) != 1:
                raise ValueError("built wheel has no unique entry-point metadata")
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(archive.read(entry_points[0]).decode("utf-8"))
    except (OSError, UnicodeError, zipfile.BadZipFile, configparser.Error) as error:
        raise ValueError("built wheel metadata is invalid") from error
    if (
        parser.get("console_scripts", "scientist-harness", fallback=None)
        != "super_scientist.cli.bootstrap:main"
    ):
        raise ValueError("built wheel does not bind the reviewed smoke entry point")


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = tuple(sys.argv[1:]) if argv is None else argv
    if arguments:
        result = WheelSmokeResult(
            False,
            (
                WheelSmokeStage(
                    name="selection",
                    status="failed",
                    returncode=None,
                    stderr="wheel smoke accepts no arguments",
                ),
            ),
        )
        _print_result(result)
        return 2

    project_root = Path.cwd()
    wheels = tuple(
        path.relative_to(project_root)
        for path in sorted((project_root / "dist").glob("*.whl"))
        if path.is_file()
    )
    try:
        plan = build_wheel_smoke_plan(wheels)
    except ValueError:
        result = WheelSmokeResult(
            False,
            (
                WheelSmokeStage(
                    name="selection",
                    status="failed",
                    returncode=None,
                    stderr="wheel smoke requires exactly one built wheel",
                ),
            ),
        )
        _print_result(result)
        return 1
    result = run_wheel_smoke(
        plan,
        project_root=project_root,
        temp_parent=Path(tempfile.gettempdir()),
    )
    _print_result(result)
    return 0 if result.passed else 1


def _print_result(result: WheelSmokeResult) -> None:
    print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
