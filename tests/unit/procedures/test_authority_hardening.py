from __future__ import annotations

import base64
import warnings
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
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
    MAX_PROCEDURE_RESULT_BYTES,
    AcceptedSourceReceiptRef,
    GroundedCapabilityAssessment,
    OpaqueProcedureCompilationEnvelope,
    ProcedureAuthority,
    ProcedureBoundaryValidationError,
    ProcedureCompilationRecord,
    ProcedureCompilationRequest,
    ProcedureCompilationResult,
    ProcedureEvidenceSourceKind,
    ProcedureFindingCode,
    ProcedureValidationStatus,
    canonical_model_hash,
    compile_method,
    parse_untrusted_procedure_compilation_envelope,
    parse_untrusted_procedure_compilation_result,
    validate_procedure,
)
from super_scientist.domain.procedures.compiler import compile_declared_stages

REQUEST_BYTE_LIMIT = 65_536
PRIVATE_MARKER = "PRIVATE_PROCEDURE_MARKER_" + ("x" * 200)
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class _RecordProcedureCompilationProposal(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_type: Literal["record_procedure_compilation"]
    compilation: OpaqueProcedureCompilationEnvelope


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


def _opaque_envelope_payload(result_json: bytes) -> dict[str, object]:
    return {
        "schema_version": 1,
        "compilation_id": "compilation-opaque",
        "result_json_base64": base64.b64encode(result_json).decode("ascii"),
        "result_json_hash": sha256_hex(result_json),
        "created_at": NOW.isoformat().replace("+00:00", "Z"),
        "governing_policy_hash": "f" * 64,
    }


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


def _request_with_invalid_nested_resource(*, construct: bool) -> ProcedureCompilationRequest:
    request = valid_request()
    resource = request.candidate.resource_estimate
    if construct:
        invalid_resource = type(resource).model_construct(
            **(resource.__dict__ | {"tokens": PRIVATE_MARKER})
        )
    else:
        invalid_resource = resource.model_copy(update={"tokens": PRIVATE_MARKER})
    candidate = request.candidate.model_copy(update={"resource_estimate": invalid_resource})
    return request.model_copy(update={"candidate": candidate})


def _result_with_invalid_constructed_resource() -> ProcedureCompilationResult:
    result = compile_method(valid_request())
    candidate = result.procedure.source_candidate
    resource = candidate.resource_estimate
    invalid_resource = type(resource).model_construct(
        **(resource.__dict__ | {"tokens": PRIVATE_MARKER})
    )
    invalid_candidate = candidate.model_construct(
        **(candidate.__dict__ | {"resource_estimate": invalid_resource})
    )
    invalid_procedure = result.procedure.model_construct(
        **(result.procedure.__dict__ | {"source_candidate": invalid_candidate})
    )
    return result.model_construct(**(result.__dict__ | {"procedure": invalid_procedure}))


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

    with pytest.raises(ProcedureBoundaryValidationError) as caught:
        compile_method(forged)

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation request failed canonical validation",
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

    with pytest.raises(ProcedureBoundaryValidationError) as caught:
        compile_method(forged)

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation request failed canonical validation",
    )


def test_validation_rejects_rehashed_wrapper_with_stale_profile_hash() -> None:
    valid = valid_request()
    request = valid.model_copy(
        update={"capability_assessments": (_forged_verified_assessment_from_unknown(),)}
    )
    procedure = compile_declared_stages(valid)

    findings = validate_procedure(request, procedure)

    assert ProcedureFindingCode.SPOOFED_EVIDENCE_BINDING in tuple(
        finding.code for finding in findings
    )


def test_compiler_rejects_rehashed_wrapper_with_stale_profile_hash() -> None:
    request = valid_request().model_copy(
        update={"capability_assessments": (_forged_verified_assessment_from_unknown(),)}
    )

    with pytest.raises(ProcedureBoundaryValidationError) as caught:
        compile_method(request)

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation request failed canonical validation",
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

    with pytest.raises(ProcedureBoundaryValidationError) as caught:
        parse_untrusted_procedure_compilation_result(payload)

    error = caught.value
    assert str(error) == "procedure compilation result failed validation"
    assert PRIVATE_MARKER not in str(error)
    assert PRIVATE_MARKER not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "errors")


def test_untrusted_result_boundary_accepts_valid_dictionary_and_json() -> None:
    result = compile_method(valid_request())
    payload = result.model_dump(mode="python")
    result_json = canonical_json_bytes(result.model_dump(mode="json"))

    assert parse_untrusted_procedure_compilation_result(payload) == result
    assert parse_untrusted_procedure_compilation_result(result_json) == result


def test_proposal_parser_keeps_schema_invalid_result_opaque_until_safe_boundary() -> None:
    result = compile_method(valid_request())
    payload = result.model_dump(mode="json")
    request_payload = result.parse_request().model_dump(mode="json")
    request_payload["request_id"] = PRIVATE_MARKER
    payload["request_json"] = canonical_json_bytes(request_payload).decode("utf-8")
    invalid_result_json = canonical_json_bytes(payload)
    proposal_json = canonical_json_bytes(
        {
            "proposal_type": "record_procedure_compilation",
            "compilation": _opaque_envelope_payload(invalid_result_json),
        }
    )

    proposal = TypeAdapter(_RecordProcedureCompilationProposal).validate_json(proposal_json)

    with pytest.raises(ProcedureBoundaryValidationError) as caught:
        parse_untrusted_procedure_compilation_result(proposal.compilation)
    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation result failed validation",
    )


def test_valid_opaque_envelope_normalizes_to_result_and_record() -> None:
    result = compile_method(valid_request())
    envelope = OpaqueProcedureCompilationEnvelope.build(
        compilation_id="compilation-opaque",
        result=result,
        created_at=NOW,
        governing_policy_hash="f" * 64,
    )

    assert parse_untrusted_procedure_compilation_envelope(envelope) == envelope
    assert parse_untrusted_procedure_compilation_result(envelope) == result
    record = ProcedureCompilationRecord.build_from_untrusted_envelope(envelope)

    assert record.compilation_id == envelope.compilation_id
    assert record.result == result
    assert record.created_at == NOW
    assert record.governing_policy_hash == "f" * 64


@pytest.mark.parametrize(
    "field_name",
    ("compilation_id", "created_at", "governing_policy_hash"),
)
def test_safe_envelope_boundaries_reject_model_copy_metadata_markers(
    field_name: str,
) -> None:
    result = compile_method(valid_request())
    envelope = OpaqueProcedureCompilationEnvelope.build(
        compilation_id="compilation-opaque",
        result=result,
        created_at=NOW,
        governing_policy_hash="f" * 64,
    )
    forged = envelope.model_copy(update={field_name: PRIVATE_MARKER})
    boundaries = (
        (
            lambda: parse_untrusted_procedure_compilation_envelope(forged),
            "procedure compilation envelope failed validation",
        ),
        (
            lambda: parse_untrusted_procedure_compilation_result(forged),
            "procedure compilation result failed validation",
        ),
        (
            lambda: ProcedureCompilationRecord.build_from_untrusted_envelope(forged),
            "procedure compilation envelope failed validation",
        ),
    )

    for invoke, expected_message in boundaries:
        with pytest.raises(ProcedureBoundaryValidationError) as caught:
            invoke()
        _assert_sanitized_boundary_error(caught.value, expected_message)


def test_safe_envelope_parser_rejects_noncanonical_model_copy_metadata() -> None:
    result = compile_method(valid_request())
    envelope = OpaqueProcedureCompilationEnvelope.build(
        compilation_id="compilation-opaque",
        result=result,
        created_at=NOW,
        governing_policy_hash="f" * 64,
    )
    forged = envelope.model_copy(update={"compilation_id": " compilation-opaque "})

    with pytest.raises(ProcedureBoundaryValidationError) as caught:
        parse_untrusted_procedure_compilation_envelope(forged)

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation envelope failed validation",
    )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("compilation_id", "compilation-other"),
        ("created_at", datetime(2026, 8, 23, 13, 0, tzinfo=UTC)),
        ("governing_policy_hash", "e" * 64),
    ),
)
def test_record_hash_binds_validated_envelope_metadata(
    field_name: str,
    replacement: object,
) -> None:
    result = compile_method(valid_request())
    envelope = OpaqueProcedureCompilationEnvelope.build(
        compilation_id="compilation-opaque",
        result=result,
        created_at=NOW,
        governing_policy_hash="f" * 64,
    )
    baseline = ProcedureCompilationRecord.build_from_untrusted_envelope(envelope)
    changed = OpaqueProcedureCompilationEnvelope.build(
        compilation_id=(
            replacement
            if field_name == "compilation_id" and isinstance(replacement, str)
            else envelope.compilation_id
        ),
        result=result,
        created_at=(
            replacement
            if field_name == "created_at" and isinstance(replacement, datetime)
            else envelope.created_at
        ),
        governing_policy_hash=(
            replacement
            if field_name == "governing_policy_hash" and isinstance(replacement, str)
            else envelope.governing_policy_hash
        ),
    )

    changed_record = ProcedureCompilationRecord.build_from_untrusted_envelope(changed)

    assert changed_record.content_hash != baseline.content_hash


def test_opaque_envelope_rejects_over_depth_json_without_exposing_plaintext() -> None:
    result_json = (("[" * 129) + f'"{PRIVATE_MARKER}"' + ("]" * 129)).encode("utf-8")

    with pytest.raises(ValidationError) as caught:
        OpaqueProcedureCompilationEnvelope.model_validate_json(
            canonical_json_bytes(_opaque_envelope_payload(result_json)),
            strict=True,
        )

    assert PRIVATE_MARKER not in str(caught.value)
    assert PRIVATE_MARKER not in repr(caught.value)
    assert PRIVATE_MARKER not in repr(caught.value.errors())


def test_opaque_envelope_rejects_result_over_canonical_byte_limit() -> None:
    result_json = ('"' + ("x" * MAX_PROCEDURE_RESULT_BYTES) + '"').encode("utf-8")

    with pytest.raises(ValidationError, match="canonical byte limit"):
        OpaqueProcedureCompilationEnvelope.model_validate_json(
            canonical_json_bytes(_opaque_envelope_payload(result_json)),
            strict=True,
        )


def test_opaque_envelope_rejects_noncanonical_json_and_hash_mismatch() -> None:
    noncanonical_json = b'{"schema_version": 1}'
    with pytest.raises(ValidationError, match="canonical JSON"):
        OpaqueProcedureCompilationEnvelope.model_validate_json(
            canonical_json_bytes(_opaque_envelope_payload(noncanonical_json)),
            strict=True,
        )

    canonical_json = b'{"schema_version":1}'
    mismatched = _opaque_envelope_payload(canonical_json)
    mismatched["result_json_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="hash"):
        OpaqueProcedureCompilationEnvelope.model_validate_json(
            canonical_json_bytes(mismatched),
            strict=True,
        )


def test_untrusted_result_boundary_hides_default_structured_validation_input() -> None:
    result = compile_method(valid_request())
    request_payload = result.parse_request().model_dump(mode="json")
    request_payload["request_id"] = PRIVATE_MARKER
    payload = result.model_dump(mode="python")
    payload["request_json"] = canonical_json_bytes(request_payload).decode("utf-8")

    with pytest.raises(ProcedureBoundaryValidationError) as caught:
        parse_untrusted_procedure_compilation_result(payload)

    error = caught.value
    assert str(error) == "procedure compilation result failed validation"
    assert PRIVATE_MARKER not in str(error)
    assert PRIVATE_MARKER not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "errors")


def test_deep_request_json_fails_safely_at_result_and_request_boundaries() -> None:
    result = compile_method(valid_request())
    deep_request_json = "[" * 10_000 + f'"{PRIVATE_MARKER}"' + "]" * 10_000
    payload = result.model_dump(mode="python")
    payload["request_json"] = deep_request_json

    with pytest.raises(ProcedureBoundaryValidationError) as result_caught:
        parse_untrusted_procedure_compilation_result(payload)
    _assert_sanitized_boundary_error(
        result_caught.value,
        "procedure compilation result failed validation",
    )

    forged = result.model_copy(update={"request_json": deep_request_json})
    with pytest.raises(ProcedureBoundaryValidationError) as request_caught:
        forged.parse_request()
    _assert_sanitized_boundary_error(
        request_caught.value,
        "compilation result request failed validation",
    )


def test_compiler_deep_model_copy_fails_with_fixed_safe_error() -> None:
    nested_request_id: object = PRIVATE_MARKER
    for _ in range(10_000):
        nested_request_id = [nested_request_id]
    request = valid_request().model_copy(update={"request_id": nested_request_id})

    with warnings.catch_warnings(), pytest.raises(ProcedureBoundaryValidationError) as caught:
        warnings.simplefilter("ignore")
        compile_method(request)

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation request failed canonical validation",
    )


@pytest.mark.parametrize("construct", (False, True))
def test_compiler_rejects_invalid_nested_resource_without_marker_warning(
    construct: bool,
) -> None:
    request = _request_with_invalid_nested_resource(construct=construct)

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(ProcedureBoundaryValidationError) as caught:
            compile_method(request)

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation request failed canonical validation",
    )
    assert all(PRIVATE_MARKER not in str(item.message) for item in caught_warnings)


def test_untrusted_result_parser_rejects_constructed_nested_resource_without_warning() -> None:
    forged = _result_with_invalid_constructed_resource()

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(ProcedureBoundaryValidationError) as caught:
            parse_untrusted_procedure_compilation_result(forged)

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation result failed validation",
    )
    assert all(PRIVATE_MARKER not in str(item.message) for item in caught_warnings)


def test_result_request_parser_rejects_structurally_invalid_result_before_field_read() -> None:
    forged = _result_with_invalid_constructed_resource()

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(ProcedureBoundaryValidationError) as caught:
            forged.parse_request()

    _assert_sanitized_boundary_error(
        caught.value,
        "compilation result request failed validation",
    )
    assert all(PRIVATE_MARKER not in str(item.message) for item in caught_warnings)


def test_opaque_envelope_builder_rejects_invalid_typed_result_before_serialization() -> None:
    forged = _result_with_invalid_constructed_resource()

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(ProcedureBoundaryValidationError) as caught:
            OpaqueProcedureCompilationEnvelope.build(
                compilation_id="compilation-opaque",
                result=forged,
                created_at=NOW,
                governing_policy_hash="f" * 64,
            )

    _assert_sanitized_boundary_error(
        caught.value,
        "procedure compilation result failed validation",
    )
    assert all(PRIVATE_MARKER not in str(item.message) for item in caught_warnings)


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

    with pytest.raises(
        ProcedureBoundaryValidationError,
        match="failed canonical validation",
    ):
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
