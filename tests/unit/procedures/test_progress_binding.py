from __future__ import annotations

from decimal import Decimal, getcontext, localcontext

import pytest
from pydantic import ValidationError
from test_compiler import (
    NOW,
    POLICY_HASH,
    _candidate,
    _rebuild_step,
    _replace_catalog,
    _step,
    valid_request,
)

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import canonical_json_bytes
from super_scientist.domain.procedures import (
    CompiledProgressPlanBinding,
    MethodDirectionOutcome,
    MethodDirectionStatus,
    ProcedureAuthority,
    ProcedureBoundaryValidationError,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    ProcedureValidationReport,
    ProcedureValidationStatus,
    canonical_model_hash,
    compile_method,
    parse_untrusted_procedure_compilation_result,
    procedure_to_progress_plan,
)
from super_scientist.domain.progress.calculations import calculate_progress, detect_false_finish
from super_scientist.domain.progress.models import FalseFinishResult

PRIVATE_MARKER = "PRIVATE_PROCEDURE_MARKER_" + ("x" * 200)


def _plan(result=None):  # type: ignore[no-untyped-def]
    compiled = result or compile_method(valid_request())
    return procedure_to_progress_plan(
        compiled,
        run_id="run-1",
        plan_version_id="plan-1",
        version=1,
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


def test_valid_procedure_maps_exactly_to_canonical_progress_types() -> None:
    result = compile_method(valid_request())
    plan = _plan(result)

    assert tuple(item.subtask_id for item in plan.subtasks) == ("prepare", "validate")
    assert plan.subtasks[1].dependency_ids == ("prepare",)
    assert sum((item.weight for item in plan.subtasks), Decimal("0")) == Decimal("1")
    assert calculate_progress(plan, ()).official_weight == Decimal("0")


def test_mixed_scale_progress_binding_is_independent_of_decimal_precision() -> None:
    request = valid_request()
    first = _rebuild_step(
        request.candidate.stages[0],
        progress_weight=Decimal("0.1400"),
    )
    second = _rebuild_step(
        request.candidate.stages[1],
        progress_weight=Decimal("0.14"),
    )
    third = _step(
        "finalize",
        3,
        inputs=("final",),
        output_id="delivered",
        dependencies=("validate",),
        operation=request.candidate.stages[1].operation,
        tool_ids=("fixture-tool",),
        authority=(ProcedureAuthority.RUN_REGISTERED_TOOL,),
        category=request.candidate.stages[1].progress_budget_category,
        weight=Decimal("0.720"),
    )
    candidate = _candidate(first, second, third, expected_output_ids=("delivered",))
    request = request.model_copy(update={"candidate": candidate})

    ambient_context = getcontext()
    original_context = ambient_context.copy()
    compiled = []
    plans = []
    for precision in (1, 2, 80):
        context = original_context.copy()
        context.prec = precision
        with localcontext(context):
            result = compile_method(request)
            compiled.append(result)
            plans.append(
                procedure_to_progress_plan(
                    result,
                    run_id="run-1",
                    plan_version_id="mixed-plan",
                    version=1,
                    created_at=NOW,
                    governing_policy_hash=POLICY_HASH,
                )
            )

    assert compiled[0] == compiled[1] == compiled[2]
    assert len({result.result_hash for result in compiled}) == 1
    assert all(result.report.status is ProcedureValidationStatus.VALID for result in compiled)
    assert len({canonical_model_hash(plan) for plan in plans}) == 1
    assert all(calculate_progress(plan, ()).total_weight == Decimal("1.0000") for plan in plans)
    assert getcontext() is ambient_context
    assert (getcontext().prec, getcontext().rounding, getcontext().Emin, getcontext().Emax) == (
        original_context.prec,
        original_context.rounding,
        original_context.Emin,
        original_context.Emax,
    )


@pytest.mark.parametrize("status_kind", ("invalid", "inconclusive"))
def test_only_valid_compilations_can_create_progress_plans(status_kind: str) -> None:
    request = valid_request()
    if status_kind == "invalid":
        request = request.model_copy(update={"compiler_version": "unsupported"})
    else:
        request = _replace_catalog(request, "TOOL_CATALOG", (), False)
    result = compile_method(request)
    assert result.report.status is not ProcedureValidationStatus.VALID
    assert result.procedure is not None

    with pytest.raises(ValueError, match="only a valid procedure can produce a progress plan"):
        _plan(result)


def test_rehashed_valid_report_cannot_map_an_impossible_governance_procedure() -> None:
    from test_compiler import _rebuild_step, _replace_step

    request = valid_request()
    first = _rebuild_step(
        request.candidate.stages[0],
        required_authorities=(ProcedureAuthority.GOVERNANCE_WRITE,),
    )
    invalid = compile_method(_replace_step(request, 0, first))
    forged = invalid.model_copy(
        update={
            "report": ProcedureValidationReport(
                status=ProcedureValidationStatus.VALID,
                findings=(),
                checks_run=tuple(range(1, 17)),
            )
        }
    )
    payload = forged.model_dump(mode="python", exclude={"result_hash"})
    payload["result_hash"] = canonical_model_hash(forged, exclude_fields={"result_hash"})
    parsed = parse_untrusted_procedure_compilation_result(payload)

    with pytest.raises(ValueError, match="deterministic compiler revalidation"):
        _plan(parsed)


def test_progress_revalidation_failure_does_not_retain_private_input() -> None:
    result = compile_method(valid_request())
    request_payload = result.parse_request().model_dump(mode="json")
    request_payload["request_id"] = PRIVATE_MARKER
    forged = result.model_copy(
        update={
            "request_json": canonical_json_bytes(request_payload).decode("utf-8"),
        }
    )

    with pytest.raises(ValueError) as caught:
        _plan(forged)

    error = caught.value
    assert str(error) == "compilation result failed deterministic compiler revalidation"
    assert PRIVATE_MARKER not in str(error)
    assert PRIVATE_MARKER not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    structured_errors = getattr(error, "errors", None)
    if callable(structured_errors):
        assert PRIVATE_MARKER not in repr(structured_errors())


def test_progress_mapping_rejects_deep_request_json_safely() -> None:
    result = compile_method(valid_request())
    deep_request_json = "[" * 10_000 + f'"{PRIVATE_MARKER}"' + "]" * 10_000
    forged = result.model_copy(update={"request_json": deep_request_json})

    with pytest.raises(ProcedureBoundaryValidationError) as caught:
        _plan(forged)

    error = caught.value
    assert str(error) == "compilation result failed deterministic compiler revalidation"
    assert PRIVATE_MARKER not in str(error)
    assert PRIVATE_MARKER not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "errors")


def test_progress_mapping_preserves_existing_false_finish_semantics() -> None:
    plan = _plan()
    finding = detect_false_finish(
        voluntary_termination=True,
        claims_completion=True,
        final_validator_result=AssessmentOutcome.FAILED,
        validated_weight=plan.subtasks[0].weight,
        unused_budget=True,
    )

    assert finding.result is FalseFinishResult.FALSE_FINISH


def test_compilation_record_receipt_and_binding_are_canonically_hashed() -> None:
    result = compile_method(valid_request())
    record = ProcedureCompilationRecord.build(
        compilation_id="compilation-1",
        result=result,
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    receipt = ProcedureCompilationReceiptRef(
        proposal_id="proposal-1",
        proposal_hash="1" * 64,
        audit_event_id="audit-1",
        audit_event_hash="2" * 64,
    )
    plan = _plan(result)
    binding = CompiledProgressPlanBinding.build(
        binding_id="binding-1",
        compilation_receipt=receipt,
        compilation_id=record.compilation_id,
        compilation_hash=record.content_hash,
        procedure_id=result.procedure.procedure_id,
        procedure_hash=result.procedure.content_hash,
        plan=plan,
        plan_hash=canonical_model_hash(plan),
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    assert binding.plan.plan_version_id == "plan-1"
    payload = binding.model_dump(mode="python")
    payload["plan_hash"] = "f" * 64
    with pytest.raises(ValidationError, match="plan hash"):
        CompiledProgressPlanBinding.model_validate(payload)


def test_method_direction_outcome_retains_negative_and_budget_evidence() -> None:
    evidence = ArtifactRef(
        sha256="3" * 64,
        size_bytes=5,
        media_type="application/json",
        relative_path=f"sha256/33/{'3' * 64}",
    )
    outcome = MethodDirectionOutcome.build(
        outcome_id="outcome-1",
        status=MethodDirectionStatus.UNSUPPORTED,
        evidence_refs=(evidence,),
        failed_method_ids=("method-1",),
        rejected_procedure_ids=("procedure-1",),
        budget_reference_ids=("budget-1",),
        terminal_rule="Independent validation rejected the method",
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    assert outcome.failed_method_ids == ("method-1",)
    assert outcome.rejected_procedure_ids == ("procedure-1",)
