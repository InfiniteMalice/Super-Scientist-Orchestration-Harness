from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import behavior_entry, manifest, source_binding
from pydantic import ValidationError

from super_scientist.domain.primitives import sha256_hex
from super_scientist.handbook import (
    BehaviorEntry,
    BehaviorManifest,
    HandbookBuildError,
    SourceBinding,
    build_handbook,
)


def test_manifest_contracts_are_strict_frozen_and_closed(repository_root: Path) -> None:
    declared = manifest(repository_root)
    assert declared.model_config.get("frozen") is True
    assert declared.behaviors[0].model_config.get("extra") == "forbid"

    with pytest.raises(ValidationError):
        BehaviorManifest.model_validate(declared.model_dump() | {"inferred_truth": True})
    with pytest.raises(ValidationError):
        BehaviorEntry.model_validate(declared.behaviors[0].model_dump() | {"summary": 7})
    with pytest.raises(ValidationError):
        SourceBinding.model_validate(
            declared.behaviors[0].source_bindings[0].model_dump()
            | {"repository_commit": "a" * 64, "source_hash": "not-a-hash"}
        )


@pytest.mark.parametrize("repository_commit", ("a" * 40, "b" * 64))
def test_source_binding_uses_the_git_object_id_contract(
    repository_root: Path,
    repository_commit: str,
) -> None:
    binding = source_binding(repository_root, repository_commit=repository_commit)
    assert binding.repository_commit == repository_commit


@pytest.mark.parametrize("repository_commit", ("a" * 39, "A" * 40, "g" * 64, "a" * 65))
def test_source_binding_rejects_non_git_object_ids(
    repository_root: Path,
    repository_commit: str,
) -> None:
    with pytest.raises(ValidationError):
        source_binding(repository_root, repository_commit=repository_commit)


def test_manifest_rejects_duplicate_behaviors_and_duplicate_set_like_fields(
    repository_root: Path,
) -> None:
    first = behavior_entry(repository_root)
    with pytest.raises(ValidationError, match="behavior_id"):
        manifest(repository_root, first, first)
    with pytest.raises(ValidationError, match="governing_rule_version_ids"):
        behavior_entry(
            repository_root,
            governing_rule_version_ids=("rule-version-alpha", "rule-version-alpha"),
        )


def test_builder_locates_module_function_class_and_method_without_execution(
    repository_root: Path,
) -> None:
    bindings = (
        source_binding(repository_root, symbol="<module>"),
        source_binding(repository_root, symbol="public_function"),
        source_binding(repository_root, symbol="PublicClass"),
        source_binding(repository_root, symbol="PublicClass.method"),
    )
    built = build_handbook(
        repository_root,
        manifest(repository_root, behavior_entry(repository_root, bindings=bindings)),
    )

    by_symbol = {location.symbol: location for location in built.source_locations}
    assert set(by_symbol) == {"<module>", "public_function", "PublicClass", "PublicClass.method"}
    assert by_symbol["public_function"].kind == "FUNCTION"
    assert by_symbol["PublicClass"].kind == "CLASS"
    assert by_symbol["PublicClass.method"].kind == "METHOD"
    assert by_symbol["<module>"].kind == "MODULE"
    assert by_symbol["public_function"].start_line == 3
    assert by_symbol["public_function"].end_line == 4
    assert by_symbol["PublicClass.method"].start_line == 8
    assert by_symbol["PublicClass.method"].end_line == 9
    assert all(location.module == "src.sample" for location in built.source_locations)


def test_builder_retains_only_human_declared_behavioral_truth(repository_root: Path) -> None:
    declared = behavior_entry(
        repository_root,
        summary="The human declaration is authoritative.",
        contracts=("Only this declared contract is represented.",),
    )
    built = build_handbook(repository_root, manifest(repository_root, declared))
    document = json.loads(built.json_bytes)

    assert [item["behavior_id"] for item in document["behaviors"]] == ["behavior-alpha"]
    assert document["behaviors"][0]["summary"] == declared.summary
    assert document["behaviors"][0]["contracts"] == list(declared.contracts)
    assert "unrelated_symbol" not in built.json_bytes.decode("utf-8")
    assert document["authority"]["syntax_infers_behavior"] is False


def test_build_is_byte_identical_and_semantically_sorted(repository_root: Path) -> None:
    alpha = behavior_entry(repository_root, behavior_id="behavior-alpha")
    beta = behavior_entry(
        repository_root,
        behavior_id="behavior-beta",
        bindings=(source_binding(repository_root, symbol="PublicClass.method"),),
        governing_rule_version_ids=("rule-zeta", "rule-beta"),
        dependencies=("behavior-alpha",),
    )
    first = build_handbook(repository_root, manifest(repository_root, beta, alpha))
    second = build_handbook(repository_root, manifest(repository_root, alpha, beta))

    assert first.json_bytes == second.json_bytes
    assert first.markdown_bytes == second.markdown_bytes
    assert first.generated_artifact_hash == second.generated_artifact_hash
    document = json.loads(first.json_bytes)
    assert [item["behavior_id"] for item in document["behaviors"]] == [
        "behavior-alpha",
        "behavior-beta",
    ]
    assert document["behaviors"][1]["governing_rule_version_ids"] == [
        "rule-beta",
        "rule-zeta",
    ]


def test_generated_json_and_markdown_have_exactly_four_disclosure_levels(
    repository_root: Path,
) -> None:
    built = build_handbook(repository_root, manifest(repository_root))
    document = json.loads(built.json_bytes)

    assert [level["level"] for level in document["disclosure_levels"]] == [1, 2, 3, 4]
    assert [level["name"] for level in document["disclosure_levels"]] == [
        "summary",
        "contracts_dependencies_rules",
        "modules_symbols",
        "exact_source",
    ]
    markdown = built.markdown_bytes.decode("utf-8")
    for heading in (
        "## Level 1: Summary",
        "## Level 2: Contracts, dependencies, and governing rules",
        "## Level 3: Modules and symbols",
        "## Level 4: Exact commit, path, lines, and hashes",
    ):
        assert heading in markdown


def test_builder_derives_exact_reverse_source_and_rule_links(repository_root: Path) -> None:
    alpha = behavior_entry(
        repository_root,
        behavior_id="behavior-alpha",
        governing_rule_version_ids=("rule-shared", "rule-alpha"),
    )
    beta = behavior_entry(
        repository_root,
        behavior_id="behavior-beta",
        bindings=(source_binding(repository_root, symbol="PublicClass.method"),),
        governing_rule_version_ids=("rule-shared",),
    )
    built = build_handbook(repository_root, manifest(repository_root, beta, alpha))
    document = json.loads(built.json_bytes)

    assert document["source_to_behaviors"] == [
        {
            "behavior_ids": ["behavior-beta"],
            "relative_path": "src/sample.py",
            "symbol": "PublicClass.method",
        },
        {
            "behavior_ids": ["behavior-alpha"],
            "relative_path": "src/sample.py",
            "symbol": "public_function",
        },
    ]
    assert document["rule_to_behaviors"] == [
        {"behavior_ids": ["behavior-alpha"], "rule_version_id": "rule-alpha"},
        {
            "behavior_ids": ["behavior-alpha", "behavior-beta"],
            "rule_version_id": "rule-shared",
        },
    ]


def test_source_tree_and_symbol_hashes_are_exact(repository_root: Path) -> None:
    source_path = repository_root / "src" / "sample.py"
    built = build_handbook(repository_root, manifest(repository_root))
    document = json.loads(built.json_bytes)

    assert built.source_hashes == (sha256_hex(source_path.read_bytes()),)
    assert document["source_tree_hash"] == built.source_tree_hash
    location = built.source_locations[0]
    exact_symbol = b"def public_function(value: int) -> int:\n    return value + 1"
    assert location.symbol_source_hash == sha256_hex(exact_symbol)


def test_source_hashes_are_reproducible_across_git_checkout_line_endings(
    tmp_path: Path,
) -> None:
    git_executable = shutil.which("git")
    assert git_executable is not None, "Git executable is required for this test"
    source_lf = b"def public_function(value: int) -> int:\n    return value + 1\n"
    canonical_hash = sha256_hex(source_lf)
    seed = tmp_path / "seed"
    (seed / "src").mkdir(parents=True)
    (seed / "tests").mkdir()
    (seed / "src" / "sample.py").write_bytes(source_lf)
    (seed / "tests" / "test_sample.py").write_bytes(b"def test_sample():\n    pass\n")
    for arguments in (
        ("init", "--quiet"),
        ("config", "user.name", "Handbook Fixture"),
        ("config", "user.email", "handbook@example.invalid"),
        ("add", "src/sample.py", "tests/test_sample.py"),
        ("commit", "--quiet", "-m", "canonical source"),
    ):
        subprocess.run((git_executable, *arguments), cwd=seed, check=True, capture_output=True)
    declared = manifest(
        seed,
        behavior_entry(
            seed,
            bindings=(source_binding(seed, source_hash=canonical_hash),),
        ),
    )
    lf_build = build_handbook(seed, declared)

    clone = tmp_path / "autocrlf-clone"
    subprocess.run(
        (
            git_executable,
            "-c",
            "core.autocrlf=true",
            "clone",
            "--quiet",
            "--no-hardlinks",
            str(seed),
            str(clone),
        ),
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        (git_executable, "config", "core.autocrlf", "false"),
        cwd=clone,
        check=True,
        capture_output=True,
    )
    autocrlf_build = build_handbook(clone, declared)

    assert lf_build.source_tree_hash == autocrlf_build.source_tree_hash
    assert lf_build.source_hashes == autocrlf_build.source_hashes == (canonical_hash,)
    assert lf_build.json_bytes == autocrlf_build.json_bytes
    assert lf_build.markdown_bytes == autocrlf_build.markdown_bytes


def test_build_fails_closed_when_verification_has_findings(repository_root: Path) -> None:
    invalid = manifest(
        repository_root,
        behavior_entry(
            repository_root,
            bindings=(source_binding(repository_root, symbol="missing_symbol"),),
        ),
    )

    with pytest.raises(HandbookBuildError) as raised:
        build_handbook(repository_root, invalid)
    assert "SYMBOL_NOT_FOUND" in raised.value.finding_codes


def test_manifest_hash_excludes_no_human_authored_contract(repository_root: Path) -> None:
    original = build_handbook(repository_root, manifest(repository_root))
    changed = build_handbook(
        repository_root,
        manifest(
            repository_root,
            behavior_entry(repository_root, summary="A materially changed human declaration."),
        ),
    )
    assert original.manifest_hash != changed.manifest_hash
    assert original.json_bytes != changed.json_bytes


def test_repository_commit_override_must_match_manifest_and_bindings(repository_root: Path) -> None:
    with pytest.raises(HandbookBuildError) as raised:
        build_handbook(
            repository_root,
            manifest(repository_root),
            repository_commit="2" * 40,
        )
    assert raised.value.finding_codes == ("REPOSITORY_COMMIT_MISMATCH",)


def test_build_result_is_strict_and_generated_hash_binds_both_artifacts(
    repository_root: Path,
) -> None:
    built = build_handbook(repository_root, manifest(repository_root))
    assert built.model_config.get("frozen") is True
    with pytest.raises(ValidationError):
        type(built).model_validate(built.model_dump() | {"untrusted": "field"})
    assert built.generated_artifact_hash not in {
        sha256_hex(built.json_bytes),
        sha256_hex(built.markdown_bytes),
    }
