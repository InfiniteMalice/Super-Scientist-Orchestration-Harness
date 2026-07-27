from __future__ import annotations

from typing import Annotated

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
from super_scientist.kernel.transactions.models import (
    CreateHarnessCampaign,
    DecideHarnessCampaign,
    ProposalBase,
    ProposalKind,
    RecordHarnessConfound,
    RecordHarnessIteration,
    RecordHarnessProtectedResult,
)
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from super_scientist.providers.storage.domain_records import (
    HarnessBudgetRepository,
    HarnessCampaignRepository,
    HarnessConfoundRepository,
    HarnessDecisionRepository,
    HarnessMetricRepository,
    HarnessObservationRepository,
    HarnessPartitionManifestRepository,
)

harness_eval_app = typer.Typer(no_args_is_help=True)
StableId = Annotated[str, typer.Argument()]

_RECORD_TYPES: dict[str, tuple[ProposalKind, type[ProposalBase]]] = {
    "iteration": ("record_harness_iteration", RecordHarnessIteration),
    "protected_result": (
        "record_harness_protected_result",
        RecordHarnessProtectedResult,
    ),
    "confound": ("record_harness_confound", RecordHarnessConfound),
    "decision": ("decide_harness_campaign", DecideHarnessCampaign),
}


def _dump(record: BaseModel) -> dict[str, object]:
    return record.model_dump(mode="json", warnings="none")


@harness_eval_app.command("create")
@_command_boundary("harness-eval create", integrity_exit_code=3)
def harness_eval_create(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    submit_json_mutation(
        root=root,
        command="harness-eval create",
        proposal_kind="create_harness_campaign",
        proposal_model=CreateHarnessCampaign,
        payload=load_json_object(input_path),
        json_output=json_output,
    )


@harness_eval_app.command("record")
@_command_boundary("harness-eval record", integrity_exit_code=3)
def harness_eval_record(
    root: Root,
    input_path: JsonInputFile,
    json_output: JsonOutput = False,
) -> None:
    payload = load_json_object(input_path)
    record_type = payload.pop("record_type", "iteration")
    if not isinstance(record_type, str) or record_type not in _RECORD_TYPES:
        raise CliBoundaryError(
            "INVALID_ARGUMENT",
            "harness-eval record requires a fixed record_type",
        )
    proposal_kind, proposal_model = _RECORD_TYPES[record_type]
    submit_json_mutation(
        root=root,
        command="harness-eval record",
        proposal_kind=proposal_kind,
        proposal_model=proposal_model,
        payload=payload,
        json_output=json_output,
        human_command=f"harness-eval {record_type.replace('_', ' ')} record",
    )


@harness_eval_app.command("report")
@_command_boundary("harness-eval report", integrity_exit_code=3)
def harness_eval_report(
    root: Root,
    campaign_id: StableId,
    json_output: JsonOutput = False,
) -> None:
    identifier = validate_stable_identifier(campaign_id)
    with build_runtime(root) as runtime, DatabaseUnitOfWork(runtime.engine) as uow:
        connection = uow.connection
        if connection is None:
            raise RuntimeError("unit of work is not active")
        campaign = HarnessCampaignRepository(connection).get(identifier)
        partitions = HarnessPartitionManifestRepository(connection).list_all()
        budgets = HarnessBudgetRepository(connection).list_all()
        observations = HarnessObservationRepository(connection).list_all()
        metrics = HarnessMetricRepository(connection).list_all()
        confounds = HarnessConfoundRepository(connection).list_all()
        decisions = HarnessDecisionRepository(connection).list_all()
    if campaign is None:
        emit(
            "harness-eval report",
            False,
            json_output,
            errors=[{"code": "NOT_FOUND", "message": identifier}],
        )
        raise typer.Exit(code=4)
    emit(
        "harness-eval report",
        True,
        json_output,
        data={
            "campaign": _dump(campaign),
            "partition_manifests": [
                _dump(item) for item in partitions if item.campaign_id == identifier
            ],
            "budgets": [_dump(item) for item in budgets if item.campaign_id == identifier],
            "observations": [
                _dump(item) for item in observations if item.campaign_id == identifier
            ],
            "metrics": [_dump(item) for item in metrics if item.campaign_id == identifier],
            "confounds": [_dump(item) for item in confounds if item.campaign_id == identifier],
            "decisions": [_dump(item) for item in decisions if item.campaign_id == identifier],
        },
    )


__all__ = ["harness_eval_app"]
