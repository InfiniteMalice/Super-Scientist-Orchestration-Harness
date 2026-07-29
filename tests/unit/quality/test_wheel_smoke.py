from __future__ import annotations

import importlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def _wheel_smoke() -> ModuleType:
    return importlib.import_module("super_scientist.quality.wheel_smoke")


def _write_test_wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "package-0.2.0.dist-info/entry_points.txt",
            "[console_scripts]\nscientist-harness = super_scientist.cli.bootstrap:main\n",
        )


def test_wheel_smoke_uses_built_distribution_and_fixed_cli_command() -> None:
    wheel_smoke = _wheel_smoke()

    plan = wheel_smoke.build_wheel_smoke_plan((Path("dist/package-0.2.0-py3-none-any.whl"),))

    assert plan.wheel_path == Path("dist/package-0.2.0-py3-none-any.whl")
    assert plan.smoke_argv[-3:] == ("scientist-harness", "--help", "--json")
    assert plan.install_argv[:-1] == (
        "{venv-python}",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--no-index",
        "--no-deps",
    )
    assert "--skip" not in (*plan.install_argv, *plan.smoke_argv)


def test_project_wheel_rejects_path_configuration_files(tmp_path: Path) -> None:
    wheel_smoke = _wheel_smoke()
    wheel = tmp_path / "package-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("package.pth", "import injected\n")

    with pytest.raises(ValueError, match="path configuration"):
        wheel_smoke._verify_project_wheel(wheel)


def test_dependency_free_smoke_bootstrap_preserves_exact_public_envelope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap = importlib.import_module("super_scientist.cli.bootstrap")
    monkeypatch.setattr(sys, "argv", ["scientist-harness", "--help", "--json"])

    with pytest.raises(SystemExit) as raised:
        bootstrap.main()

    assert raised.value.code == 2
    assert json.loads(capsys.readouterr().out) == {
        "command": "scientist-harness",
        "data": None,
        "decision": None,
        "errors": [{"code": "INVALID_ARGUMENT", "message": "No such option: --json"}],
        "schema_version": 1,
        "success": False,
    }


def test_default_runner_applies_a_finite_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel_smoke = _wheel_smoke()
    observed: dict[str, object] = {}

    def run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(wheel_smoke.subprocess, "run", run)

    wheel_smoke._run_fixed_command(("fixed", "command"))

    assert observed["timeout"] == 300.0


def test_executor_creates_an_isolated_environment_and_fails_safe_on_timeout(
    tmp_path: Path,
) -> None:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    wheel = root / "dist" / "package-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    _write_test_wheel(wheel)
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    created = temp_parent / "ssoh-wheel-smoke-timeout"
    calls: list[tuple[str, ...]] = []

    def temporary_directory_factory(*, prefix: str, dir: str) -> str:
        created.mkdir()
        return str(created)

    def command_runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, timeout=300.0)

    result = wheel_smoke.run_wheel_smoke(
        wheel_smoke.build_wheel_smoke_plan((Path("dist") / wheel.name,)),
        project_root=root,
        temp_parent=temp_parent,
        command_runner=command_runner,
        temporary_directory_factory=temporary_directory_factory,
    )

    assert calls == [
        (
            sys.executable,
            "-m",
            "venv",
            "--copies",
            str(created),
        )
    ]
    assert result.passed is False
    assert tuple(stage.name for stage in result.stages) == ("venv",)
    assert result.stages[0].returncode is None
    assert result.stages[0].stderr == "wheel smoke failed safely: TimeoutExpired"
    assert not created.exists()


@pytest.mark.parametrize(
    "distributions",
    [
        (),
        (Path("dist/a.whl"), Path("dist/b.whl")),
        (Path("dist/a.tar.gz"),),
        (Path("a.whl"),),
        (Path("../dist/a.whl"),),
        (Path("C:/dist/a.whl"),),
    ],
)
def test_wheel_smoke_plan_rejects_nonexact_distribution_selection(
    distributions: tuple[Path, ...],
) -> None:
    wheel_smoke = _wheel_smoke()

    with pytest.raises(ValueError):
        wheel_smoke.build_wheel_smoke_plan(distributions)


def test_module_entrypoint_has_no_user_selection_and_fails_without_built_wheel(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "super_scientist.quality.wheel_smoke"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {
        "passed": False,
        "stages": [
            {
                "name": "selection",
                "returncode": None,
                "status": "failed",
                "stderr": "wheel smoke requires exactly one built wheel",
                "stdout": "",
            }
        ],
    }
    assert completed.stderr == ""


def test_module_entrypoint_rejects_every_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wheel_smoke = _wheel_smoke()

    assert wheel_smoke.main(("--wheel", "dist/unreviewed.whl")) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["stages"][0]["stderr"] == "wheel smoke accepts no arguments"


def _run_fake_smoke(
    tmp_path: Path,
    *,
    smoke_returncode: int,
    smoke_stdout: str,
) -> tuple[object, list[tuple[str, ...]], Path]:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    wheel = root / "dist" / "package-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    _write_test_wheel(wheel)
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    created = temp_parent / "ssoh-wheel-smoke-fixed"
    calls: list[tuple[str, ...]] = []

    def temporary_directory_factory(*, prefix: str, dir: str) -> str:
        assert prefix == "ssoh-wheel-smoke-"
        assert Path(dir) == temp_parent
        created.mkdir()
        return str(created)

    def command_runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1:3] == ("-m", "venv"):
            scripts = created / ("Scripts" if wheel_smoke.IS_WINDOWS else "bin")
            scripts.mkdir()
            (scripts / wheel_smoke.VENV_PYTHON_NAME).write_bytes(b"")
            (scripts / wheel_smoke.VENV_CLI_NAME).write_bytes(b"")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if "pip" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="installed", stderr="")
        return subprocess.CompletedProcess(
            argv,
            smoke_returncode,
            stdout=smoke_stdout,
            stderr="",
        )

    plan = wheel_smoke.build_wheel_smoke_plan((Path("dist") / wheel.name,))
    result = wheel_smoke.run_wheel_smoke(
        plan,
        project_root=root,
        temp_parent=temp_parent,
        command_runner=command_runner,
        temporary_directory_factory=temporary_directory_factory,
    )
    return result, calls, created


@pytest.mark.parametrize(
    ("failed_stage", "expected_stages"),
    [
        ("venv", ("venv",)),
        ("install", ("venv", "install")),
    ],
)
def test_executor_stops_at_failed_setup_stage_and_cleans_temp(
    tmp_path: Path,
    failed_stage: str,
    expected_stages: tuple[str, ...],
) -> None:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    wheel = root / "dist" / "package-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    _write_test_wheel(wheel)
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    created = temp_parent / "ssoh-wheel-smoke-failure"

    def temporary_directory_factory(*, prefix: str, dir: str) -> str:
        assert prefix == "ssoh-wheel-smoke-"
        assert Path(dir) == temp_parent
        created.mkdir()
        return str(created)

    def command_runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ("-m", "venv"):
            if failed_stage == "venv":
                return subprocess.CompletedProcess(argv, 9, stdout="", stderr="venv failed")
            scripts = created / ("Scripts" if wheel_smoke.IS_WINDOWS else "bin")
            scripts.mkdir()
            (scripts / wheel_smoke.VENV_PYTHON_NAME).write_bytes(b"")
            (scripts / wheel_smoke.VENV_CLI_NAME).write_bytes(b"")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 8, stdout="", stderr="install failed")

    result = wheel_smoke.run_wheel_smoke(
        wheel_smoke.build_wheel_smoke_plan((Path("dist") / wheel.name,)),
        project_root=root,
        temp_parent=temp_parent,
        command_runner=command_runner,
        temporary_directory_factory=temporary_directory_factory,
    )

    assert result.passed is False
    assert tuple(stage.name for stage in result.stages) == expected_stages
    assert result.stages[-1].status == "failed"
    assert not created.exists()


def test_executor_reports_missing_environment_executable_and_cleans_temp(
    tmp_path: Path,
) -> None:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    wheel = root / "dist" / "package-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    _write_test_wheel(wheel)
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    created = temp_parent / "ssoh-wheel-smoke-missing-python"

    def temporary_directory_factory(*, prefix: str, dir: str) -> str:
        created.mkdir()
        return str(created)

    def command_runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = wheel_smoke.run_wheel_smoke(
        wheel_smoke.build_wheel_smoke_plan((Path("dist") / wheel.name,)),
        project_root=root,
        temp_parent=temp_parent,
        command_runner=command_runner,
        temporary_directory_factory=temporary_directory_factory,
    )

    assert result.passed is False
    assert tuple(stage.name for stage in result.stages) == ("venv", "install")
    assert "not a verified file" in result.stages[-1].stderr
    assert not created.exists()


def test_executor_reports_missing_installed_cli_and_cleans_temp(tmp_path: Path) -> None:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    wheel = root / "dist" / "package-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    _write_test_wheel(wheel)
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    created = temp_parent / "ssoh-wheel-smoke-missing-cli"

    def temporary_directory_factory(*, prefix: str, dir: str) -> str:
        created.mkdir()
        return str(created)

    def command_runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ("-m", "venv"):
            scripts = created / ("Scripts" if wheel_smoke.IS_WINDOWS else "bin")
            scripts.mkdir()
            (scripts / wheel_smoke.VENV_PYTHON_NAME).write_bytes(b"")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = wheel_smoke.run_wheel_smoke(
        wheel_smoke.build_wheel_smoke_plan((Path("dist") / wheel.name,)),
        project_root=root,
        temp_parent=temp_parent,
        command_runner=command_runner,
        temporary_directory_factory=temporary_directory_factory,
    )

    assert result.passed is False
    assert tuple(stage.name for stage in result.stages) == (
        "venv",
        "install",
        "cli-smoke",
    )
    assert "not a verified file" in result.stages[-1].stderr
    assert not created.exists()


def test_executor_converts_runner_exception_to_safe_failure_and_cleans_temp(
    tmp_path: Path,
) -> None:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    wheel = root / "dist" / "package-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    _write_test_wheel(wheel)
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    created = temp_parent / "ssoh-wheel-smoke-exception"

    def temporary_directory_factory(*, prefix: str, dir: str) -> str:
        created.mkdir()
        return str(created)

    def command_runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        raise OSError("simulated execution failure")

    result = wheel_smoke.run_wheel_smoke(
        wheel_smoke.build_wheel_smoke_plan((Path("dist") / wheel.name,)),
        project_root=root,
        temp_parent=temp_parent,
        command_runner=command_runner,
        temporary_directory_factory=temporary_directory_factory,
    )

    assert result.passed is False
    assert result.stages[0].name == "venv"
    assert result.stages[0].stderr == "wheel smoke failed safely: OSError"
    assert not created.exists()


def test_executor_uses_fixed_argv_accepts_exact_cli_envelope_and_cleans_temp(
    tmp_path: Path,
) -> None:
    wheel_smoke = _wheel_smoke()
    envelope = json.dumps(
        {
            "command": "scientist-harness",
            "data": None,
            "decision": None,
            "errors": [{"code": "INVALID_ARGUMENT", "message": "No such option: --json"}],
            "schema_version": 1,
            "success": False,
        },
        sort_keys=True,
    )

    result, calls, created = _run_fake_smoke(
        tmp_path,
        smoke_returncode=2,
        smoke_stdout=envelope,
    )

    assert result.passed is True
    assert tuple(stage.name for stage in result.stages) == ("venv", "install", "cli-smoke")
    assert calls[0][1:4] == ("-m", "venv", "--copies")
    assert Path(calls[1][-1]).name == "package-0.2.0-py3-none-any.whl"
    assert Path(calls[2][0]).name == wheel_smoke.VENV_PYTHON_NAME
    assert calls[2][1:4] == ("-I", "-m", "super_scientist.cli.bootstrap")
    assert calls[2][-2:] == ("--help", "--json")
    assert not created.exists()


def test_executor_rejects_arbitrary_cli_failure_and_still_cleans_temp(tmp_path: Path) -> None:
    result, _, created = _run_fake_smoke(
        tmp_path,
        smoke_returncode=2,
        smoke_stdout='{"success": false, "errors": [{"code": "OTHER"}]}',
    )

    assert result.passed is False
    assert result.stages[-1].status == "failed"
    assert not created.exists()


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected_passed"),
    [
        (0, "help text", True),
        (3, "", False),
        (2, "{", False),
        (2, "[]", False),
    ],
)
def test_executor_accepts_only_success_or_the_exact_fixed_cli_envelope(
    tmp_path: Path,
    returncode: int,
    stdout: str,
    expected_passed: bool,
) -> None:
    result, _, created = _run_fake_smoke(
        tmp_path,
        smoke_returncode=returncode,
        smoke_stdout=stdout,
    )

    assert result.passed is expected_passed
    assert not created.exists()


def test_executor_never_deletes_unverified_temporary_path(tmp_path: Path) -> None:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    wheel = root / "dist" / "package-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    _write_test_wheel(wheel)
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("retain", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def outside_factory(*, prefix: str, dir: str) -> str:
        return str(outside)

    def command_runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = wheel_smoke.run_wheel_smoke(
        wheel_smoke.build_wheel_smoke_plan((Path("dist") / wheel.name,)),
        project_root=root,
        temp_parent=temp_parent,
        command_runner=command_runner,
        temporary_directory_factory=outside_factory,
    )

    assert result.passed is False
    assert calls == []
    assert sentinel.read_text(encoding="utf-8") == "retain"


def test_executor_rejects_symlinked_wheel(tmp_path: Path) -> None:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    dist = root / "dist"
    dist.mkdir(parents=True)
    outside = tmp_path / "outside.whl"
    outside.write_bytes(b"wheel")
    wheel = dist / "package-0.2.0-py3-none-any.whl"
    try:
        wheel.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError):
        wheel_smoke.resolve_built_wheel(
            wheel_smoke.build_wheel_smoke_plan((Path("dist") / wheel.name,)),
            root,
        )
