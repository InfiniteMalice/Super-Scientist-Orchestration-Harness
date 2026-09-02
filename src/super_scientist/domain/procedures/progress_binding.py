from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime

from super_scientist.domain.primitives import Sha256Hex, StableIdentifier, UtcTimestamp
from super_scientist.domain.procedures.models import (
    ExecutableProcedure,
    ProcedureBoundaryValidationError,
    ProcedureCompilationResult,
    ProcedureStep,
    ProcedureValidationStatus,
    parse_untrusted_procedure_compilation_result,
)
from super_scientist.domain.progress.calculations import calculate_progress
from super_scientist.domain.progress.models import ProgressPlan, ProgressSubtask

_STATIC_PLAN_ID = "procedure-static-validation-plan"
_STATIC_RUN_ID = "procedure-static-validation-run"
_STATIC_POLICY_HASH = "0" * 64
_STATIC_CREATED_AT = datetime(1970, 1, 1, tzinfo=UTC)


def _progress_subtask(step: ProcedureStep, plan_version_id: str) -> ProgressSubtask:
    return ProgressSubtask(
        subtask_id=step.step_id,
        plan_version_id=plan_version_id,
        description=step.objective,
        dependency_ids=step.dependency_ids,
        completion_criteria=step.completion_criteria,
        validator=step.validator,
        validator_version=step.validator_version,
        weight=step.progress_weight,
        evidence_requirements=step.evidence_requirements,
        order=step.order,
    )


def _build_progress_plan(
    procedure: ExecutableProcedure,
    *,
    run_id: StableIdentifier,
    plan_version_id: StableIdentifier,
    version: int,
    created_at: UtcTimestamp,
    governing_policy_hash: Sha256Hex,
) -> ProgressPlan:
    return ProgressPlan(
        plan_version_id=plan_version_id,
        run_id=run_id,
        version=version,
        subtasks=tuple(_progress_subtask(step, str(plan_version_id)) for step in procedure.steps),
        created_at=created_at,
        governing_policy_hash=governing_policy_hash,
    )


def validate_progress_mapping(procedure: ExecutableProcedure) -> None:
    plan = _build_progress_plan(
        procedure,
        run_id=_STATIC_RUN_ID,
        plan_version_id=_STATIC_PLAN_ID,
        version=1,
        created_at=_STATIC_CREATED_AT,
        governing_policy_hash=_STATIC_POLICY_HASH,
    )
    calculate_progress(plan, ())


def procedure_to_progress_plan(
    result: ProcedureCompilationResult,
    *,
    run_id: StableIdentifier,
    plan_version_id: StableIdentifier,
    version: int,
    created_at: UtcTimestamp,
    governing_policy_hash: Sha256Hex,
) -> ProgressPlan:
    validated_result: ProcedureCompilationResult | None = None
    with suppress(ProcedureBoundaryValidationError):
        validated_result = parse_untrusted_procedure_compilation_result(result)
    if validated_result is None:
        raise ProcedureBoundaryValidationError(
            "compilation result failed deterministic compiler revalidation"
        ) from None
    result = validated_result
    if result.report.status is not ProcedureValidationStatus.VALID:
        raise ValueError("only a valid procedure can produce a progress plan")
    from super_scientist.domain.procedures.compiler import compile_method

    recompiled_result: ProcedureCompilationResult | None = None
    try:
        request = result.parse_request()
        recompiled_result = compile_method(request)
    except (AttributeError, TypeError, ValueError):
        pass
    if recompiled_result != result:
        raise ProcedureBoundaryValidationError(
            "compilation result failed deterministic compiler revalidation"
        ) from None
    plan = _build_progress_plan(
        result.procedure,
        run_id=run_id,
        plan_version_id=plan_version_id,
        version=version,
        created_at=created_at,
        governing_policy_hash=governing_policy_hash,
    )
    calculate_progress(plan, ())
    return plan


__all__ = ["procedure_to_progress_plan"]
