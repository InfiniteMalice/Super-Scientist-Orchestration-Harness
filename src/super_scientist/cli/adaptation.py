from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import BaseModel

from super_scientist.cli.kernel import (
    CliBoundaryError,
    JsonInputFile,
    JsonOutput,
    Root,
    _command_boundary,
    build_runtime,
    load_json_object,
    submit_json_mutation,
    validate_stable_identifier,
)
from super_scientist.cli.output import emit
from super_scientist.domain.progress.calculations import (
    calculate_progress,
    current_progress_plan,
)
from super_scientist.kernel.transactions.models import (
    AppendProgressEvent,
    ConsolidateBehavioralRule,
    CreateResearchRun,
    ImportReviewerAssessment,
    ProposalBase,
    ProposalKind,
    ProposeBehavioralRule,
    ProposeEvidenceTrailNodes,
    ProposeEvidenceTrailRelations,
    ProposeGovernancePolicyTransition,
    ProposeHypothesisVersion,
    ProposePrimitiveVersion,
    RecordPrimitiveEvaluation,
    RecordProgressPlan,
    RecordSelfImprovementMeasurement,
    RecordVerificationResult,
    RegisterExecutableModel,
    ReviseHypothesis,
)
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from super_scientist.providers.storage.repositories import StorageIntegrityError

research_run_app = typer.Typer(no_args_is_help=True)
governance_app = typer.Typer(no_args_is_help=True)
improvement_app = typer.Typer(no_args_is_help=True)
progress_app = typer.Typer(no_args_is_help=True)
trail_app = typer.Typer(no_args_is_help=True)
rule_app = typer.Typer(no_args_is_help=True)
rule_review_app = typer.Typer(no_args_is_help=True)
primitive_app = typer.Typer(no_args_is_help=True)
hypothesis_app = typer.Typer(no_args_is_help=True)
model_app = typer.Typer(no_args_is_help=True)
verifier_app = typer.Typer(no_args_is_help=True)
rule_app.add_typer(rule_review_app, name="review")

StableId = Annotated[str, typer.Argument()]


def _mutate(
    *,
    root: Path,
    input_path: Path,
    json_output: bool,
    command: str,
    proposal_kind: ProposalKind,
    proposal_model: type[ProposalBase],
    human_command: str | None = None,
) -> None:
    payload = load_json_object(input_path)
    submit_json_mutation(
        root=root,
        command=command,
        proposal_kind=proposal_kind,
        proposal_model=proposal_model,
        payload=payload,
        json_output=json_output,
        human_command=human_command,
    )


def _dump(record: BaseModel) -> dict[str, object]:
    return record.model_dump(mode="json", warnings="none")


def _not_found(command: str, identifier: str, json_output: bool) -> NoReturn:
    emit(
        command,
        False,
        json_output,
        errors=[{"code": "NOT_FOUND", "message": identifier}],
    )
    raise typer.Exit(code=4)


@research_run_app.command("create")
@_command_boundary("research-run create", integrity_exit_code=3)
def research_run_create(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="research-run create",
        proposal_kind="create_research_run",
        proposal_model=CreateResearchRun,
    )


@governance_app.command("propose")
@_command_boundary("governance propose", integrity_exit_code=3)
def governance_propose(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="governance propose",
        proposal_kind="propose_governance_policy_transition",
        proposal_model=ProposeGovernancePolicyTransition,
    )


@governance_app.command("show")
@_command_boundary("governance show", integrity_exit_code=3)
def governance_show(
    root: Root,
    json_output: JsonOutput = False,
) -> None:
    with build_runtime(root) as runtime, DatabaseUnitOfWork(runtime.engine) as uow:
        policy = uow.repositories().policies.get_active()
    if policy is None:
        raise StorageIntegrityError("active governance policy is missing")
    emit("governance show", True, json_output, data=_dump(policy))


@improvement_app.command("classify")
@_command_boundary("improvement classify", integrity_exit_code=3)
def improvement_classify(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="improvement classify",
        proposal_kind="record_self_improvement_measurement",
        proposal_model=RecordSelfImprovementMeasurement,
    )


@improvement_app.command("report")
@_command_boundary("improvement report", integrity_exit_code=3)
def improvement_report(
    root: Root,
    change_id: StableId,
    json_output: JsonOutput = False,
) -> None:
    identifier = validate_stable_identifier(change_id)
    with build_runtime(root) as runtime, DatabaseUnitOfWork(runtime.engine) as uow:
        snapshot = uow.repositories().adaptation_integrity_snapshot()
    matches = tuple(
        measurement for measurement in snapshot.measurements if measurement.change_id == identifier
    )
    if not matches:
        _not_found("improvement report", identifier, json_output)
    emit(
        "improvement report",
        True,
        json_output,
        data=[_dump(measurement) for measurement in matches],
    )


@progress_app.command("add")
@_command_boundary("progress add", integrity_exit_code=3)
def progress_add(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="progress add",
        proposal_kind="record_progress_plan",
        proposal_model=RecordProgressPlan,
    )


@progress_app.command("validate")
@_command_boundary("progress validate", integrity_exit_code=3)
def progress_validate(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="progress validate",
        proposal_kind="append_progress_event",
        proposal_model=AppendProgressEvent,
    )


@progress_app.command("status")
@_command_boundary("progress status", integrity_exit_code=3)
def progress_status(
    root: Root,
    run_id: StableId,
    json_output: JsonOutput = False,
) -> None:
    identifier = validate_stable_identifier(run_id)
    with build_runtime(root) as runtime, DatabaseUnitOfWork(runtime.engine) as uow:
        snapshot = uow.repositories().progress_integrity_snapshot()
    plan = current_progress_plan(snapshot.plans, identifier)
    if plan is None:
        _not_found("progress status", identifier, json_output)
    events = tuple(event for event in snapshot.events if event.run_id == identifier)
    summary = calculate_progress(plan, events)
    emit(
        "progress status",
        True,
        json_output,
        data={
            "plan": _dump(plan),
            "summary": _dump(summary),
            "events": [_dump(event) for event in events],
            "budgets": [
                _dump(budget) for budget in snapshot.budgets if budget.run_id == identifier
            ],
            "checkpoints": [
                _dump(checkpoint)
                for checkpoint in snapshot.checkpoints
                if checkpoint.run_id == identifier
            ],
            "completion_decisions": [
                _dump(decision)
                for decision in snapshot.completion_decisions
                if decision.run_id == identifier
            ],
        },
    )


@trail_app.command("create")
@_command_boundary("trail create", integrity_exit_code=3)
def trail_create(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="trail create",
        proposal_kind="propose_evidence_trail_nodes",
        proposal_model=ProposeEvidenceTrailNodes,
    )


@trail_app.command("add-node")
@_command_boundary("trail add-node", integrity_exit_code=3)
def trail_add_node(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="trail add-node",
        proposal_kind="propose_evidence_trail_nodes",
        proposal_model=ProposeEvidenceTrailNodes,
    )


@trail_app.command("add-relation")
@_command_boundary("trail add-relation", integrity_exit_code=3)
def trail_add_relation(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="trail add-relation",
        proposal_kind="propose_evidence_trail_relations",
        proposal_model=ProposeEvidenceTrailRelations,
    )


@trail_app.command("validate")
@_command_boundary("trail validate", integrity_exit_code=3)
def trail_validate(
    root: Root,
    trail_id: StableId,
    json_output: JsonOutput = False,
) -> None:
    identifier = validate_stable_identifier(trail_id)
    with build_runtime(root) as runtime, DatabaseUnitOfWork(runtime.engine) as uow:
        snapshot = uow.repositories().trail_integrity_snapshot()
    head = next((item for item in snapshot.heads if item[0] == identifier), None)
    if head is None:
        _not_found("trail validate", identifier, json_output)
    version = next(
        (item for item in snapshot.versions if item.trail_version_id == head[1]),
        None,
    )
    if version is None:
        raise StorageIntegrityError("trail head references a missing version")
    checks = tuple(
        check for check in snapshot.checks if check.trail_version_id == version.trail_version_id
    )
    valid = bool(checks) and all(check.passed for check in checks)
    errors = (
        []
        if valid
        else [
            {
                "code": "TRAIL_INTEGRITY_ERROR",
                "message": version.trail_version_id,
            }
        ]
    )
    emit(
        "trail validate",
        valid,
        json_output,
        data={
            "valid": valid,
            "trail_version": _dump(version),
            "checks": [_dump(check) for check in checks],
            "assessments": [
                _dump(assessment)
                for assessment in snapshot.assessments
                if assessment.trail_version_id == version.trail_version_id
            ],
        },
        errors=errors,
    )
    if not valid:
        raise typer.Exit(code=3)


@rule_app.command("propose")
@_command_boundary("rule propose", integrity_exit_code=3)
def rule_propose(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="rule propose",
        proposal_kind="propose_behavioral_rule",
        proposal_model=ProposeBehavioralRule,
    )


@rule_review_app.command("import")
@_command_boundary("rule review import", integrity_exit_code=3)
def rule_review_import(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="rule review import",
        proposal_kind="import_reviewer_assessment",
        proposal_model=ImportReviewerAssessment,
    )


@rule_app.command("consolidate")
@_command_boundary("rule consolidate", integrity_exit_code=3)
def rule_consolidate(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="rule consolidate",
        proposal_kind="consolidate_behavioral_rule",
        proposal_model=ConsolidateBehavioralRule,
    )


@rule_app.command("history")
@_command_boundary("rule history", integrity_exit_code=3)
def rule_history(
    root: Root,
    rule_id: StableId,
    json_output: JsonOutput = False,
) -> None:
    identifier = validate_stable_identifier(rule_id)
    with build_runtime(root) as runtime, DatabaseUnitOfWork(runtime.engine) as uow:
        snapshot = uow.repositories().rule_integrity_snapshot()
    versions = tuple(version for version in snapshot.versions if version.rule_id == identifier)
    if not versions:
        _not_found("rule history", identifier, json_output)
    emit(
        "rule history",
        True,
        json_output,
        data=[_dump(version) for version in versions],
    )


@primitive_app.command("propose")
@_command_boundary("primitive propose", integrity_exit_code=3)
def primitive_propose(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="primitive propose",
        proposal_kind="propose_primitive_version",
        proposal_model=ProposePrimitiveVersion,
    )


@primitive_app.command("evaluate")
@_command_boundary("primitive evaluate", integrity_exit_code=3)
def primitive_evaluate(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="primitive evaluate",
        proposal_kind="record_primitive_evaluation",
        proposal_model=RecordPrimitiveEvaluation,
    )


@hypothesis_app.command("propose")
@_command_boundary("hypothesis propose", integrity_exit_code=3)
def hypothesis_propose(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="hypothesis propose",
        proposal_kind="propose_hypothesis_version",
        proposal_model=ProposeHypothesisVersion,
    )


@hypothesis_app.command("revise")
@_command_boundary("hypothesis revise", integrity_exit_code=3)
def hypothesis_revise(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="hypothesis revise",
        proposal_kind="revise_hypothesis",
        proposal_model=ReviseHypothesis,
    )


@model_app.command("register")
@_command_boundary("model register", integrity_exit_code=3)
def model_register(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    _mutate(
        root=root,
        input_path=input_path,
        json_output=json_output,
        command="model register",
        proposal_kind="register_executable_model",
        proposal_model=RegisterExecutableModel,
    )


@verifier_app.command("record")
@_command_boundary("verifier record", integrity_exit_code=3)
def verifier_record(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    payload = load_json_object(input_path)
    category = payload.pop("category", None)
    allowed = {
        "FORMAL_VERIFIER": "formal verifier record",
        "DETERMINISTIC_CHECKER": "deterministic checker record",
        "LEARNED_JUDGE": "learned judge record",
    }
    if not isinstance(category, str) or category not in allowed:
        raise CliBoundaryError(
            "INVALID_ARGUMENT",
            "verifier record requires a fixed category",
        )
    result = payload.get("verification_result")
    if not isinstance(result, Mapping) or result.get("mechanism_type") != category:
        raise CliBoundaryError(
            "INVALID_ARGUMENT",
            "verifier category must match verification_result.mechanism_type",
        )
    submit_json_mutation(
        root=root,
        command="verifier record",
        proposal_kind="record_verification_result",
        proposal_model=RecordVerificationResult,
        payload=payload,
        json_output=json_output,
        human_command=allowed[category],
    )


__all__ = [
    "governance_app",
    "hypothesis_app",
    "improvement_app",
    "model_app",
    "primitive_app",
    "progress_app",
    "research_run_app",
    "rule_app",
    "trail_app",
    "verifier_app",
]
