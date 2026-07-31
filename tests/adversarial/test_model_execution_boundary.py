from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from super_scientist.application.hypothesis_testing.simulators import (
    SimulatorRegistry,
    UnsupportedModelError,
)
from super_scientist.domain.hypotheses.models import (
    ExecutableModelSpec,
    ExecutionMode,
    ModelInput,
    ModelType,
    NumericField,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import sha256_hex

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
HASH = sha256_hex(b"execution-boundary")
ROOT = Path(__file__).resolve().parents[2]
OWNED_RUNTIME = (
    ROOT / "src" / "super_scientist" / "domain" / "hypotheses",
    ROOT / "src" / "super_scientist" / "application" / "hypothesis_testing",
    ROOT / "src" / "super_scientist" / "application" / "transactions" / "hypotheses.py",
)
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "asyncio",
        "ctypes",
        "http",
        "importlib",
        "marshal",
        "multiprocessing",
        "os",
        "pathlib",
        "pickle",
        "runpy",
        "shlex",
        "socket",
        "subprocess",
        "urllib",
    }
)
FORBIDDEN_CALLS = frozenset(
    {
        "__import__",
        "breakpoint",
        "callable",
        "compile",
        "eval",
        "exec",
        "getattr",
        "globals",
        "hasattr",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
    }
)


def _metadata_model(artifact_name: str) -> ExecutableModelSpec:
    return ExecutableModelSpec(
        model_spec_id="inert-model",
        hypothesis_version_id="hypothesis-v1",
        model_type=ModelType.SOURCE_CONTROLLED_METADATA,
        execution_mode=ExecutionMode.METADATA_ONLY,
        artifact_hash=HASH,
        artifact_media_type="application/octet-stream",
        artifact_size_bytes=64,
        artifact_name=artifact_name,
        builtin_simulator_id=None,
        input_schema_id="inert-input-v1",
        output_schema_id="inert-output-v1",
        deterministic_seed=0,
        max_steps=1,
        max_state_bytes=64,
        registered_by=ActorIdentity(
            actor_id="registrar",
            kind=ActorKind.HUMAN,
            created_at=NOW,
        ),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "source",
        "source_text",
        "code",
        "module",
        "import_path",
        "entry_point",
        "argv",
        "command",
        "shell",
        "shell_command",
        "executable",
        "network_url",
        "url",
        "callable",
        "function",
    ],
)
def test_model_contract_has_no_execution_authority_field(forbidden_field: str) -> None:
    payload = _metadata_model("artifact.bin").model_dump(mode="python")
    payload[forbidden_field] = "untrusted"

    with pytest.raises(ValidationError):
        ExecutableModelSpec.model_validate(payload)


def test_malicious_artifact_name_remains_inert(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel.txt"
    model = _metadata_model(
        f"import os; os.system('write {sentinel.as_posix()}'); https://attacker.invalid"
    )
    model_input = ModelInput(
        model_input_id="inert-input",
        schema_id="inert-input-v1",
        values=(NumericField(name="value", value=1),),
        deterministic_seed=0,
    )

    with pytest.raises(UnsupportedModelError):
        SimulatorRegistry().execute(model, model_input, output_id="never-created")
    assert not sentinel.exists()


def test_task_12_runtime_contains_no_execution_or_reflection_dispatch() -> None:
    violations: list[str] = []
    for owned_path in OWNED_RUNTIME:
        paths = (owned_path,) if owned_path.is_file() else tuple(sorted(owned_path.glob("*.py")))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in FORBIDDEN_IMPORT_ROOTS:
                            violations.append(f"{path.name}:{node.lineno}:import {root}")
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    root = node.module.split(".", 1)[0]
                    if root in FORBIDDEN_IMPORT_ROOTS:
                        violations.append(f"{path.name}:{node.lineno}:from {root}")
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in FORBIDDEN_CALLS
                ):
                    violations.append(f"{path.name}:{node.lineno}:call {node.func.id}")
    assert violations == []


def test_registry_accepts_no_user_defined_simulator_or_callable() -> None:
    registry = SimulatorRegistry()

    with pytest.raises(TypeError):
        SimulatorRegistry({"untrusted": lambda value: value})  # type: ignore[call-arg]
    with pytest.raises((AttributeError, TypeError)):
        registry.simulators = {"untrusted": object()}  # type: ignore[misc]
