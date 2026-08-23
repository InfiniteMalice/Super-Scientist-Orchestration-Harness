from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from test_compiler import _rebuild_step, _replace_step, valid_request

from super_scientist.domain.cognition.models import CapabilityAssessment
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.procedures import (
    AcceptedSourceReceiptRef,
    GroundedCapabilityAssessment,
    ProcedureAuthority,
    ProcedureCompilationRequest,
    ProcedureEvidenceSourceKind,
    ProcedureFindingCode,
    ProcedureValidationStatus,
    compile_method,
)


def _rebuild_receipt(
    receipt: AcceptedSourceReceiptRef,
    **updates: Any,
) -> AcceptedSourceReceiptRef:
    values = receipt.model_dump(mode="python", exclude={"content_hash"})
    values.update(updates)
    return AcceptedSourceReceiptRef.build(**values)


def test_compilation_request_rejects_naked_catalog_and_capability_facts() -> None:
    request = valid_request()
    payload = request.model_dump(mode="python")
    del payload["artifact_catalog_receipt"]
    payload["capability_assessments"] = (
        request.capability_assessments[0].assessment.model_dump(mode="python"),
    )

    with pytest.raises(ValidationError):
        ProcedureCompilationRequest.model_validate(payload)


def test_catalog_receipt_binds_exact_authorization_facts() -> None:
    request = valid_request()
    forged_receipt = _rebuild_receipt(
        request.tool_catalog_receipt,
        source_content_hash="f" * 64,
    )
    forged = request.model_copy(update={"tool_catalog_receipt": forged_receipt})

    result = compile_method(forged)

    assert result.report.status is ProcedureValidationStatus.INVALID
    assert ProcedureFindingCode.SPOOFED_EVIDENCE_BINDING in tuple(
        finding.code for finding in result.report.findings
    )


def test_catalog_receipts_must_share_one_fixed_source_snapshot() -> None:
    request = valid_request()
    payload = request.model_dump(mode="python")
    payload["validator_catalog_receipt"] = _rebuild_receipt(
        request.validator_catalog_receipt,
        source_snapshot_id="different-snapshot",
        source_snapshot_hash="9" * 64,
    ).model_dump(mode="python")

    with pytest.raises(ValidationError, match="fixed source snapshot"):
        ProcedureCompilationRequest.model_validate(payload)


def test_catalog_receipt_cannot_be_repurposed_across_source_kinds() -> None:
    request = valid_request()
    payload = request.model_dump(mode="python")
    payload["artifact_catalog_receipt"] = _rebuild_receipt(
        request.artifact_catalog_receipt,
        source_kind=ProcedureEvidenceSourceKind.TOOL_CATALOG,
    ).model_dump(mode="python")

    with pytest.raises(ValidationError, match="artifact_catalog receipt"):
        ProcedureCompilationRequest.model_validate(payload)


def test_catalog_receipt_binds_the_exact_source_schema_version() -> None:
    request = valid_request()
    payload = request.model_dump(mode="python")
    payload["artifact_catalog_receipt"] = _rebuild_receipt(
        request.artifact_catalog_receipt,
        source_schema_version=2,
    ).model_dump(mode="python")

    with pytest.raises(ValidationError, match="artifact_catalog receipt"):
        ProcedureCompilationRequest.model_validate(payload)


def test_capability_assessment_is_recomputed_from_receipted_profile() -> None:
    grounded = valid_request().capability_assessments[0]
    forged_assessment = grounded.assessment.model_copy(update={"actor_id": "forged-worker"})
    payload = grounded.model_dump(mode="python")
    payload["assessment"] = forged_assessment.model_dump(mode="python")
    payload["content_hash"] = "f" * 64

    with pytest.raises(ValidationError, match="recomputed from the retained profile"):
        GroundedCapabilityAssessment.model_validate(payload)


def test_compiler_rejects_model_copy_bypass_of_capability_grounding() -> None:
    request = valid_request()
    grounded = request.capability_assessments[0]
    forged_assessment = grounded.assessment.model_copy(update={"actor_id": "forged-worker"})
    forged_grounded = grounded.model_copy(update={"assessment": forged_assessment})
    forged = request.model_copy(update={"capability_assessments": (forged_grounded,)})

    result = compile_method(forged)

    assert result.report.status is ProcedureValidationStatus.INVALID
    assert ProcedureFindingCode.SPOOFED_EVIDENCE_BINDING in tuple(
        finding.code for finding in result.report.findings
    )


def test_catalog_receipt_content_hash_covers_entries_and_completeness() -> None:
    request = valid_request()
    expected = sha256_hex(
        canonical_json_bytes(
            {
                "catalog_kind": "ARTIFACT_CATALOG",
                "entries": tuple(item.model_dump(mode="json") for item in request.artifact_catalog),
                "complete": True,
            }
        )
    )

    assert request.artifact_catalog_receipt.source_content_hash == expected


def test_impossible_authority_remains_invalid_with_grounded_evidence() -> None:
    request = valid_request()
    first = _rebuild_step(
        request.candidate.stages[0],
        required_authorities=(ProcedureAuthority.GOVERNANCE_WRITE,),
    )

    result = compile_method(_replace_step(request, 0, first))

    assert result.report.status is ProcedureValidationStatus.INVALID
    assert ProcedureFindingCode.IMPOSSIBLE_AUTHORITY in tuple(
        finding.code for finding in result.report.findings
    )


def test_grounded_capability_wrapper_rejects_a_naked_assessment_type() -> None:
    assessment = valid_request().capability_assessments[0].assessment

    assert isinstance(assessment, CapabilityAssessment)
    with pytest.raises(ValidationError):
        GroundedCapabilityAssessment.model_validate(assessment.model_dump(mode="python"))
