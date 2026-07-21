from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

COMMIT = "1" * 40


@pytest.fixture
def repository_root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    source = root / "src" / "sample.py"
    test_source = root / "tests" / "test_sample.py"
    source.parent.mkdir(parents=True)
    test_source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            (
                '"""A module whose syntax supplies locations, not behavioral truth."""',
                "",
                "def public_function(value: int) -> int:",
                "    return value + 1",
                "",
                "",
                "class PublicClass:",
                "    def method(self) -> str:",
                '        return "declared by humans"',
                "",
                "",
                "def unrelated_symbol() -> None:",
                "    pass",
                "",
            )
        ),
        encoding="utf-8",
        newline="\n",
    )
    test_source.write_text(
        "def test_placeholder() -> None:\n    assert True\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_binding(
    root: Path,
    *,
    path: str = "src/sample.py",
    symbol: str = "public_function",
    repository_commit: str = COMMIT,
    source_hash: str | None = None,
) -> Any:
    from super_scientist.handbook import SourceBinding

    target = root / Path(path)
    return SourceBinding(
        repository_commit=repository_commit,
        relative_path=path,
        symbol=symbol,
        source_hash=source_hash or digest(target),
    )


def behavior_entry(
    root: Path,
    *,
    behavior_id: str = "behavior-alpha",
    summary: str = "Human-authored summary",
    contracts: tuple[str, ...] = ("Returns a deterministic result.",),
    dependencies: tuple[str, ...] = (),
    governing_rule_version_ids: tuple[str, ...] = ("rule-version-alpha",),
    bindings: tuple[Any, ...] | None = None,
    tests: tuple[str, ...] = ("tests/test_sample.py",),
    related_behaviors: tuple[str, ...] = (),
) -> Any:
    from super_scientist.handbook import BehaviorEntry

    return BehaviorEntry(
        behavior_id=behavior_id,
        summary=summary,
        contracts=contracts,
        inputs=("A strict integer value.",),
        outputs=("A deterministic integer value.",),
        preconditions=("The input has already passed schema validation.",),
        postconditions=("The input repository remains unchanged.",),
        failure_modes=("Invalid declarations fail closed.",),
        state_read=("Declared source bytes.",),
        state_written=(),
        tools=("Python standard-library AST.",),
        permissions=("Read declared repository files only.",),
        dependencies=dependencies,
        governing_rule_version_ids=governing_rule_version_ids,
        source_bindings=bindings or (source_binding(root),),
        test_paths=tests,
        related_behaviors=related_behaviors,
    )


def manifest(root: Path, *behaviors: Any, repository_commit: str = COMMIT) -> Any:
    from super_scientist.handbook import BehaviorManifest

    entries = behaviors or (behavior_entry(root),)
    return BehaviorManifest(
        repository="fixture-repository",
        repository_commit=repository_commit,
        behaviors=entries,
    )
