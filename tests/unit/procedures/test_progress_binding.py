from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_compiler import NOW, POLICY_HASH, _replace_catalog, valid_request

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import canonical_json_bytes
from super_scientist.domain.procedures import (
    CompiledProgressPlanBinding,
    MethodDirectionOutcome,
    MethodDirectionStatus,
    ProcedureAuthority,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    ProcedureValidationReport,
    ProcedureValidationStatus,
    canonical_model_hash,
    compile_method,
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
    parsed = type(invalid).model_validate(payload)

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
