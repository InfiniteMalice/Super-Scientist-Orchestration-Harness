from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from super_scientist.cli.kernel import (
    CliBoundaryError,
    JsonOutput,
    Root,
    _command_boundary,
    load_json_object,
)
from super_scientist.cli.output import emit
from super_scientist.handbook.builder import (
    _contained_path,
    _repository_root,
    build_handbook,
)
from super_scientist.handbook.models import (
    BehaviorManifest,
    HandbookBuildError,
    PathContainmentError,
)
from super_scientist.handbook.verification import verify_handbook

handbook_app = typer.Typer(no_args_is_help=True)
Repository = Annotated[Path, typer.Option("--repository")]
Manifest = Annotated[Path, typer.Option("--manifest")]
OutputDirectory = Annotated[Path, typer.Option("--output-dir")]


def _contained_absolute(root: Path, candidate: Path) -> Path:
    contained_root = _repository_root(root)
    absolute_candidate = (
        candidate.absolute()
        if candidate.is_absolute()
        else contained_root.joinpath(candidate).absolute()
    )
    try:
        relative = absolute_candidate.relative_to(contained_root)
    except ValueError:
        raise PathContainmentError("declared path escapes repository root") from None
    if not relative.parts:
        return contained_root
    return _contained_path(contained_root, relative.as_posix())


def _paths(
    root: Path,
    repository: Path,
    manifest: Path,
    output_dir: Path | None = None,
) -> tuple[Path, Path, Path | None]:
    try:
        repository_path = _contained_absolute(root, repository)
        repository_root = _repository_root(repository_path)
        manifest_path = _contained_absolute(repository_root, manifest)
        output_path = (
            None if output_dir is None else _contained_absolute(repository_root, output_dir)
        )
    except PathContainmentError as error:
        raise CliBoundaryError("PATH_CONTAINMENT_ERROR", str(error)) from error
    if not manifest_path.is_file():
        raise CliBoundaryError(
            "INVALID_ARGUMENT",
            "manifest must be an existing regular file",
        )
    return repository_root, manifest_path, output_path


def _manifest(path: Path) -> BehaviorManifest:
    payload = load_json_object(path)
    return BehaviorManifest.model_validate_json(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@handbook_app.command("build")
@_command_boundary("handbook build")
def handbook_build(
    root: Root,
    repository: Repository,
    manifest: Manifest,
    output_dir: OutputDirectory,
    json_output: JsonOutput = False,
) -> None:
    repository_root, manifest_path, contained_output = _paths(
        root,
        repository,
        manifest,
        output_dir,
    )
    declared = _manifest(manifest_path)
    try:
        built = build_handbook(repository_root, declared)
    except HandbookBuildError as error:
        emit(
            "handbook build",
            False,
            json_output,
            errors=[{"code": "HANDBOOK_INTEGRITY_ERROR", "message": str(error)}],
        )
        raise typer.Exit(code=3) from None
    if contained_output is None:
        raise RuntimeError("output containment was not resolved")
    contained_output.mkdir(parents=True, exist_ok=True)
    json_path = contained_output / "handbook.json"
    markdown_path = contained_output / "handbook.md"
    json_path.write_bytes(built.json_bytes)
    markdown_path.write_bytes(built.markdown_bytes)
    emit(
        "handbook build",
        True,
        json_output,
        data={
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
            "generated_artifact_hash": built.generated_artifact_hash,
            "manifest_hash": built.manifest_hash,
            "source_tree_hash": built.source_tree_hash,
        },
    )


@handbook_app.command("verify")
@_command_boundary("handbook verify")
def handbook_verify(
    root: Root,
    repository: Repository,
    manifest: Manifest,
    json_output: JsonOutput = False,
) -> None:
    repository_root, manifest_path, _ = _paths(root, repository, manifest)
    declared = _manifest(manifest_path)
    try:
        result = verify_handbook(repository_root, declared)
    except PathContainmentError as error:
        raise CliBoundaryError("PATH_CONTAINMENT_ERROR", str(error)) from error
    errors = (
        []
        if result.valid
        else [
            {
                "code": "HANDBOOK_INTEGRITY_ERROR",
                "message": "handbook verification failed",
            }
        ]
    )
    emit(
        "handbook verify",
        result.valid,
        json_output,
        data=result.model_dump(mode="json"),
        errors=errors,
    )
    if not result.valid:
        raise typer.Exit(code=3)


__all__ = ["handbook_app"]
