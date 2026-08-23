from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from test_compiler import _actor, _assessment, _rebuild_step, _replace_step, valid_request

from super_scientist.domain.cognition.grounding import assess_capability
from super_scientist.domain.cognition.models import (
    CapabilityAssertion,
    CapabilityAssessment,
    CapabilityDisposition,
    CapabilityEvidenceStatus,
)
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.procedures import (
    AcceptedSourceReceiptRef,
    GroundedCapabilityAssessment,
    ProcedureAuthority,
    ProcedureCompilationRequest,
    ProcedureEvidenceSourceKind,
    ProcedureFindingCode,
    ProcedureValidationStatus,
    canonical_model_hash,
    compile_method,
    validate_procedure,
)
from super_scientist.domain.procedures.compiler import compile_declared_stages

REQUEST_BYTE_LIMIT = 65_536
PRIVATE_MARKER = "PRIVATE_PROCEDURE_MARKER_" + ("x" * 200)


def _assert_sanitized_boundary_error(error: BaseException, expected_message: str) -> None:
    assert str(error) == expected_message
    assert PRIVATE_MARKER not in str(error)
    assert PRIVATE_MARKER not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    structured_errors = getattr(error, "errors", None)
    if callable(structured_errors):
        assert PRIVATE_MARKER not in repr(structured_errors())


def _rebuild_receipt(
    receipt: AcceptedSourceReceiptRef,
    **updates: Any,
) -> AcceptedSourceReceiptRef:
    values = receipt.model_dump(mode="python", exclude={"content_hash"})
    values.update(updates)
    return AcceptedSourceReceiptRef.build(**values)


def _forged_verified_assessment_from_unknown() -> GroundedCapabilityAssessment:
    grounded = _assessment(
        CapabilityDisposition.UNKNOWN,
        CapabilityEvidenceStatus.UNKNOWN,
    )
    requirement = grounded.assessment.requirement
    forged_assertion = CapabilityAssertion(
        assertion_id="forged-assertion",
        capability_id=requirement.capability_id,
        task_family_id=requirement.task_family_id,
        status=CapabilityEvidenceStatus.VERIFIED,
        evidence_ids=("forged-evidence",),
        validator_id="validator",
        validator_version="validator-v1",
        evidence_snapshot_hash=requirement.evidence_snapshot_hash,
    )
    forged_profile = grounded.profile.model_copy(update={"assertions": (forged_assertion,)})
    forged = grounded.model_copy(
        update={
            "profile": forged_profile,
            "assessment": assess_capability(forged_profile, requirement),
        }
    )
    return forged.model_copy(
        update={"content_hash": canonical_model_hash(forged, exclude_fields={"content_hash"})}
    )


def _request_with_validator_actor_id(actor_id: str) -> ProcedureCompilationRequest:
    request = valid_request()
    first = _rebuild_step(
        request.candidate.stages[0],
        validator=_actor(actor_id, ActorKind.HUMAN),
    )
    return _replace_step(request, 0, first)


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


def test_grounded_assessment_builder_rejects_stale_profile_content_hash() -> None:
    grounded = _assessment(
        CapabilityDisposition.UNKNOWN,
        CapabilityEvidenceStatus.UNKNOWN,
    )
    forged = _forged_verified_assessment_from_unknown()

    with pytest.raises(ValidationError, match="canonically address the capability profile"):
        GroundedCapabilityAssessment.build(
            profile=forged.profile,
            assessment=forged.assessment,
            profile_receipt=grounded.profile_receipt,
        )


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


def test_validation_rejects_rehashed_wrapper_with_stale_profile_hash() -> None:
    request = valid_request().model_copy(
        update={"capability_assessments": (_forged_verified_assessment_from_unknown(),)}
    )
    procedure = compile_declared_stages(request)

    findings = validate_procedure(request, procedure)

    assert ProcedureFindingCode.SPOOFED_EVIDENCE_BINDING in tuple(
        finding.code for finding in findings
    )


def test_compiler_rejects_rehashed_wrapper_with_stale_profile_hash() -> None:
    request = valid_request().model_copy(
        update={"capability_assessments": (_forged_verified_assessment_from_unknown(),)}
    )

    result = compile_method(request)

    assert result.report.status is ProcedureValidationStatus.INVALID
    assert ProcedureFindingCode.SPOOFED_EVIDENCE_BINDING in tuple(
        finding.code for finding in result.report.findings
    )


def test_compiler_requires_strict_canonical_round_trip_before_valid_status() -> None:
    request = valid_request().model_copy(update={"request_id": " compile-request-1 "})

    with pytest.raises(ValueError, match="failed canonical validation"):
        compile_method(request)


def test_compiler_canonical_failure_does_not_retain_private_input() -> None:
    request = valid_request().model_copy(update={"request_id": PRIVATE_MARKER})

    with pytest.raises(ValueError) as caught:
        compile_method(request)

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation request failed canonical validation",
    )


@pytest.mark.parametrize("failure_kind", ("schema", "syntax"))
def test_result_request_parse_failure_does_not_retain_private_input(
    failure_kind: str,
) -> None:
    result = compile_method(valid_request())
    if failure_kind == "schema":
        request_payload = result.parse_request().model_dump(mode="json")
        request_payload["request_id"] = PRIVATE_MARKER
        request_json = canonical_json_bytes(request_payload).decode("utf-8")
    else:
        request_json = f'{{"{PRIVATE_MARKER}":'
    forged = result.model_copy(update={"request_json": request_json})

    with pytest.raises(ValueError) as caught:
        forged.parse_request()

    _assert_sanitized_boundary_error(
        caught.value,
        "compilation result request failed validation",
    )


def test_result_json_syntax_validation_sanitizes_decoder_failure() -> None:
    result = compile_method(valid_request())
    payload = result.model_dump(mode="python")
    payload["request_json"] = f'{{"{PRIVATE_MARKER}":'

    with pytest.raises(ValidationError) as caught:
        type(result).model_validate(payload)

    error = caught.value
    assert PRIVATE_MARKER not in str(error)
    assert PRIVATE_MARKER not in repr(error)
    structured_errors = error.errors(include_input=False)
    assert PRIVATE_MARKER not in repr(structured_errors)
    validator_error = structured_errors[0]["ctx"]["error"]
    assert validator_error.__cause__ is None
    assert validator_error.__context__ is None


def test_request_accepts_multibyte_payload_at_canonical_byte_boundary() -> None:
    prototype = _request_with_validator_actor_id("é")
    prototype_size = len(canonical_json_bytes(prototype.model_dump(mode="json")))
    actor_length = 1 + (REQUEST_BYTE_LIMIT - prototype_size) // 2
    request = _request_with_validator_actor_id("é" * actor_length)
    request_bytes = canonical_json_bytes(request.model_dump(mode="json"))

    assert len(request_bytes) <= REQUEST_BYTE_LIMIT
    assert len(request_bytes) + 2 > REQUEST_BYTE_LIMIT
    parsed = ProcedureCompilationRequest.model_validate_json(request_bytes, strict=True)

    assert parsed == request
    assert compile_method(parsed).parse_request() == parsed


def test_request_rejects_multibyte_payload_over_canonical_byte_limit() -> None:
    prototype = _request_with_validator_actor_id("é")
    prototype_size = len(canonical_json_bytes(prototype.model_dump(mode="json")))
    actor_length = 2 + (REQUEST_BYTE_LIMIT - prototype_size) // 2
    request = _request_with_validator_actor_id("é" * actor_length)
    request_bytes = canonical_json_bytes(request.model_dump(mode="json"))

    assert len(request_bytes) > REQUEST_BYTE_LIMIT
    with pytest.raises(ValidationError, match="canonical byte limit"):
        ProcedureCompilationRequest.model_validate_json(request_bytes, strict=True)


def test_compiler_rejects_oversize_model_copy_before_result_build() -> None:
    request = _request_with_validator_actor_id("é" * REQUEST_BYTE_LIMIT)

    with pytest.raises(ValueError, match="exceeds canonical byte limit"):
        compile_method(request)


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
