from __future__ import annotations

import ast
import importlib
import json
import os
import site
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLE_WHEEL_MEMBER = "super_scientist_examples/governed_cognitive_procedure_vertical_slice.py"
INSTALLED_EXAMPLE_BOOTSTRAP = """
import importlib
import pathlib
import sys
import sysconfig

install_root = pathlib.Path(sysconfig.get_paths()["purelib"]).resolve()
example = importlib.import_module(
    "super_scientist_examples.governed_cognitive_procedure_vertical_slice"
)


def require_installed_project_modules() -> None:
    origins = []
    for name, module in tuple(sys.modules.items()):
        if name != "super_scientist" and not name.startswith("super_scientist."):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is not None:
            origins.append(pathlib.Path(module_file).resolve())
    if not origins or any(not origin.is_relative_to(install_root) for origin in origins):
        raise SystemExit("project module resolved outside the fresh wheel install")
    example_file = pathlib.Path(example.__file__).resolve()
    if not example_file.is_relative_to(install_root):
        raise SystemExit("example resolved outside the fresh wheel install")


require_installed_project_modules()
exit_code = example.main(tuple(sys.argv[1:]))
require_installed_project_modules()
raise SystemExit(exit_code)
"""


def _wheel_smoke() -> ModuleType:
    return importlib.import_module("super_scientist.quality.wheel_smoke")


def _without_parent_coverage() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COV_CORE_") and key != "COVERAGE_PROCESS_START"
    }


def _installed_example_argv(venv_python: Path, workspace_root: Path) -> tuple[str, ...]:
    return (
        str(venv_python),
        "-I",
        "-c",
        INSTALLED_EXAMPLE_BOOTSTRAP,
        "--root",
        str(workspace_root),
        "--json",
    )


def _write_test_wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "package-0.2.0.dist-info/entry_points.txt",
            "[console_scripts]\nscientist-harness = super_scientist.cli.bootstrap:main\n",
        )
        archive.writestr(EXAMPLE_WHEEL_MEMBER, "def run_example(workspace_root): return {}\n")


def test_cognitive_vertical_slice_imports_only_wheel_and_standard_offline_modules() -> None:
    example = (
        Path(__file__).resolve().parents[3]
        / "examples"
        / "governed_cognitive_procedure_vertical_slice.py"
    )
    tree = ast.parse(example.read_text(encoding="utf-8"))
    imported_roots = {
        node.names[0].name.split(".", 1)[0]
        if isinstance(node, ast.Import)
        else (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }

    assert imported_roots == {
        "__future__",
        "argparse",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",
        "decimal",
        "itertools",
        "json",
        "pathlib",
        "pydantic",
        "sqlalchemy",
        "stat",
        "super_scientist",
    }


def test_wheel_smoke_uses_built_distribution_and_fixed_cli_command() -> None:
    wheel_smoke = _wheel_smoke()

    plan = wheel_smoke.build_wheel_smoke_plan((Path("dist/package-0.2.0-py3-none-any.whl"),))

    assert plan.wheel_path == Path("dist/package-0.2.0-py3-none-any.whl")
    assert plan.smoke_argv[-3:] == ("scientist-harness", "--version", "--json")
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


def test_project_wheel_requires_exact_cognitive_example_member(tmp_path: Path) -> None:
    wheel_smoke = _wheel_smoke()
    wheel = tmp_path / "package-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "package-0.2.0.dist-info/entry_points.txt",
            "[console_scripts]\nscientist-harness = super_scientist.cli.bootstrap:main\n",
        )

    with pytest.raises(ValueError, match="governed cognitive example"):
        wheel_smoke._verify_project_wheel(wheel)


def test_cognitive_example_passes_strict_mypy() -> None:
    project_root = Path(__file__).resolve().parents[3]
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "examples/governed_cognitive_procedure_vertical_slice.py",
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.integration
def test_built_wheel_contains_and_loads_installed_cognitive_example(tmp_path: Path) -> None:
    wheel_smoke = _wheel_smoke()
    project_root = Path(__file__).resolve().parents[3]
    dist = tmp_path / "dist"
    built = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stderr
    wheels = tuple(dist.glob("*.whl"))
    assert len(wheels) == 1
    wheel_smoke._verify_project_wheel(wheels[0])
    with zipfile.ZipFile(wheels[0]) as archive:
        assert archive.namelist().count(EXAMPLE_WHEEL_MEMBER) == 1

    installed = tmp_path / "installed"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-index",
            "--no-deps",
            "--target",
            str(installed),
            str(wheels[0]),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert install.returncode == 0, install.stderr
    load = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import importlib,pathlib,sys;"
                "root=pathlib.Path(sys.argv[1]).resolve();"
                "sys.path.insert(0,str(root));"
                "module=importlib.import_module("
                "'super_scientist_examples.governed_cognitive_procedure_vertical_slice');"
                "assert pathlib.Path(module.__file__).resolve().is_relative_to(root);"
                "assert callable(module.run_example);"
                "module.main(('--help',))"
            ),
            str(installed),
        ],
        cwd=tmp_path,
        env=_without_parent_coverage(),
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert load.returncode == 0, load.stderr
    assert "usage:" in load.stdout


def test_installed_example_command_is_fixed_json_workload_in_fresh_root(
    tmp_path: Path,
) -> None:
    venv_python = tmp_path / "venv" / "Scripts" / "python.exe"
    workspace_root = tmp_path / "installed-workspace"

    argv = _installed_example_argv(venv_python, workspace_root)

    assert argv[0] == str(venv_python)
    assert argv[-3:] == ("--root", str(workspace_root), "--json")
    assert "--help" not in argv
    assert "--target" not in argv


@pytest.mark.e2e
@pytest.mark.integration
def test_fresh_venv_executes_complete_installed_cognitive_example(
    tmp_path: Path,
) -> None:
    wheel_smoke = _wheel_smoke()
    project_root = Path(__file__).resolve().parents[3]
    dist = tmp_path / "dist"
    built = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    wheels = tuple(dist.glob("*.whl"))
    assert len(wheels) == 1
    wheel_smoke._verify_project_wheel(wheels[0])
    with zipfile.ZipFile(wheels[0]) as archive:
        assert archive.namelist().count(EXAMPLE_WHEEL_MEMBER) == 1

    venv_root = tmp_path / "venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv_root)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    executable_name = "python.exe" if sys.platform == "win32" else "python"
    executable_directory = "Scripts" if sys.platform == "win32" else "bin"
    venv_python = venv_root / executable_directory / executable_name
    assert venv_python.is_file()

    purelib_probe = subprocess.run(
        [
            str(venv_python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert purelib_probe.returncode == 0, purelib_probe.stdout + purelib_probe.stderr
    venv_purelib = Path(purelib_probe.stdout.strip()).resolve()
    dependency_roots = tuple(
        candidate.resolve()
        for raw_path in site.getsitepackages()
        if (candidate := Path(raw_path)).is_dir()
        and (candidate / "pydantic").is_dir()
        and (candidate / "sqlalchemy").is_dir()
    )
    assert len(dependency_roots) == 1
    # A stdlib venv cannot inherit an invoking venv. This fixed path supplies the
    # already-installed declared dependencies; the bootstrap below rejects any
    # project module that does not come from the newly installed wheel.
    (venv_purelib / "_declared_dependency_site.pth").write_text(
        f"{dependency_roots[0]}\n",
        encoding="utf-8",
    )

    installed = subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-index",
            "--no-deps",
            str(wheels[0]),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr

    workspace_root = tmp_path / "installed-workspace"
    completed = subprocess.run(
        _installed_example_argv(venv_python, workspace_root),
        cwd=tmp_path,
        env=_without_parent_coverage(),
        check=False,
        capture_output=True,
        text=True,
        timeout=14400,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    payload = json.loads(completed.stdout)
    assert payload["valid_compilation"]["validator_kind"] == "tool"
    assert payload["workspace"]["replayed_validator_kinds"] == ["tool"]
    assert payload["workspace"]["verified"] is True
    assert payload["workspace"]["import_verified"] is True
    assert payload["workspace"]["replay_verified"] is True


def test_installed_wheel_children_do_not_expand_parent_coverage_source_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COV_CORE_SOURCE", "super_scientist")
    monkeypatch.setenv("COV_CORE_CONFIG", "pyproject.toml")
    monkeypatch.setenv("COV_CORE_DATAFILE", ".coverage")
    monkeypatch.setenv("COV_CORE_BRANCH", "enabled")
    monkeypatch.setenv("COVERAGE_PROCESS_START", "pyproject.toml")
    monkeypatch.setenv("UNRELATED_ENVIRONMENT", "retained")

    environment = _without_parent_coverage()

    assert all(not key.startswith("COV_CORE_") for key in environment)
    assert "COVERAGE_PROCESS_START" not in environment
    assert environment["UNRELATED_ENVIRONMENT"] == "retained"


def test_dependency_free_smoke_bootstrap_returns_successful_version_envelope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap = importlib.import_module("super_scientist.cli.bootstrap")
    monkeypatch.setattr(sys, "argv", ["scientist-harness", "--version", "--json"])

    bootstrap.main()

    assert json.loads(capsys.readouterr().out) == {
        "command": "version",
        "data": {"version": "0.3.0"},
        "decision": None,
        "errors": [],
        "schema_version": 1,
        "success": True,
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


def test_executor_uses_fixed_argv_accepts_successful_version_json_and_cleans_temp(
    tmp_path: Path,
) -> None:
    wheel_smoke = _wheel_smoke()
    envelope = json.dumps(
        {
            "command": "version",
            "data": {"version": "0.3.0"},
            "decision": None,
            "errors": [],
            "schema_version": 1,
            "success": True,
        },
        sort_keys=True,
    )

    result, calls, created = _run_fake_smoke(
        tmp_path,
        smoke_returncode=0,
        smoke_stdout=envelope,
    )

    assert result.passed is True
    assert tuple(stage.name for stage in result.stages) == ("venv", "install", "cli-smoke")
    assert calls[0][1:4] == ("-m", "venv", "--copies")
    assert Path(calls[1][-1]).name == "package-0.2.0-py3-none-any.whl"
    assert Path(calls[2][0]).name == wheel_smoke.VENV_CLI_NAME
    assert calls[2][1:] == ("--version", "--json")
    assert not created.exists()


def test_executor_rejects_the_former_json_parser_error_and_still_cleans_temp(
    tmp_path: Path,
) -> None:
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
    result, _, created = _run_fake_smoke(
        tmp_path,
        smoke_returncode=2,
        smoke_stdout=envelope,
    )

    assert result.passed is False
    assert result.stages[-1].status == "failed"
    assert not created.exists()


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected_passed"),
    [
        (0, "help text", False),
        (3, "", False),
        (2, "{", False),
        (2, "[]", False),
    ],
)
def test_executor_accepts_only_exit_zero_successful_version_json(
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


def test_executor_converts_preparation_failure_to_structured_result(tmp_path: Path) -> None:
    wheel_smoke = _wheel_smoke()
    root = tmp_path / "repository"
    root.mkdir()
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()

    result = wheel_smoke.run_wheel_smoke(
        wheel_smoke.build_wheel_smoke_plan((Path("dist") / "missing-0.2.0-py3-none-any.whl",)),
        project_root=root,
        temp_parent=temp_parent,
    )

    assert result.passed is False
    assert tuple(stage.name for stage in result.stages) == ("selection",)
    assert result.stages[0].stderr == "wheel smoke failed safely: FileNotFoundError"


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
