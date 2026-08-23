from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from test_compiler import (
    _actor,
    _artifact,
    _assessment,
    _rebuild_step,
    _replace_step,
    valid_request,
)

from super_scientist.domain.cognition.models import (
    CapabilityDisposition,
    CapabilityEvidenceStatus,
)
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.procedures import (
    ArtifactCatalogEntry,
    CandidateMethod,
    CatalogFactStatus,
    ExecutableProcedure,
    ProcedureCompilationRequest,
    ProcedureCompilationResult,
    ProcedureFindingCode,
    ProcedureOperation,
    ProcedureStep,
    ProcedureValidationStatus,
    RecoveryDirective,
    RegisteredTool,
    RegisteredValidator,
    canonical_model_hash,
    compile_method,
    validate_procedure,
)


def _replace_candidate(
    request: ProcedureCompilationRequest,
    **updates: Any,
) -> ProcedureCompilationRequest:
    values = request.candidate.model_dump(mode="python", exclude={"content_hash"})
    values.update(updates)
    return request.model_copy(update={"candidate": CandidateMethod.build(**values)})


def _codes(request: ProcedureCompilationRequest) -> tuple[ProcedureFindingCode, ...]:
    return tuple(finding.code for finding in compile_method(request).report.findings)


def test_compiler_runs_all_sixteen_checks_in_stable_order() -> None:
    result = compile_method(valid_request())

    assert result.report.checks_run == tuple(range(1, 17))

    malformed = valid_request().model_copy(
        update={"compiler_version": "unsupported", "tool_catalog": ()}
    )
    first = compile_method(malformed).report.findings
    second = compile_method(malformed).report.findings
    assert first == second
    assert tuple(item.check_number for item in first) == tuple(
        sorted(item.check_number for item in first)
    )


def test_check_1_rejects_unsupported_schema_and_compiler_version() -> None:
    request = valid_request().model_copy(update={"compiler_version": "2.0.0"})
    unsupported_schema = request.model_construct(**{**request.__dict__, "schema_version": 2})

    assert ProcedureFindingCode.UNSUPPORTED_COMPILER_VERSION in _codes(request)
    assert ProcedureFindingCode.UNSUPPORTED_SCHEMA_VERSION in _codes(unsupported_schema)


def test_request_cannot_supply_compiler_support_policy() -> None:
    payload = valid_request().model_dump(mode="python")
    payload["supported_compiler_versions"] = ("attacker-version",)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProcedureCompilationRequest.model_validate(payload)


def test_check_2_rejects_duplicate_ids_and_noncanonical_ordering() -> None:
    request = valid_request()
    duplicate = _rebuild_step(request.candidate.stages[1], step_id="prepare")
    duplicate_request = _replace_candidate(request, stages=(request.candidate.stages[0], duplicate))
    reversed_request = _replace_candidate(request, stages=tuple(reversed(request.candidate.stages)))

    assert ProcedureFindingCode.DUPLICATE_STEP_ID in _codes(duplicate_request)
    assert ProcedureFindingCode.NONCANONICAL_STEP_ORDER in _codes(reversed_request)


def test_check_3_rejects_unknown_dependencies() -> None:
    request = valid_request()
    second = _rebuild_step(request.candidate.stages[1], dependency_ids=("absent",))

    assert ProcedureFindingCode.UNKNOWN_DEPENDENCY in _codes(_replace_step(request, 1, second))


def test_check_5_rejects_ambiguous_artifact_producers() -> None:
    request = valid_request()
    second = _rebuild_step(
        request.candidate.stages[1],
        outputs=(_artifact("prepared"), _artifact("final")),
    )

    assert ProcedureFindingCode.AMBIGUOUS_ARTIFACT_PRODUCER in _codes(
        _replace_step(request, 1, second)
    )


def test_check_8_rejects_referenced_outputs_that_do_not_exist() -> None:
    request = valid_request()
    second = _rebuild_step(
        request.candidate.stages[1],
        input_artifact_ids=("prepared", "referenced-but-absent"),
    )

    assert ProcedureFindingCode.MISSING_REFERENCED_OUTPUT in _codes(
        _replace_step(request, 1, second)
    )


@pytest.mark.parametrize(
    ("disposition", "evidence_status", "expected_status", "expected_code"),
    (
        (
            CapabilityDisposition.UNSATISFIED,
            CapabilityEvidenceStatus.UNSUPPORTED,
            ProcedureValidationStatus.INVALID,
            ProcedureFindingCode.CAPABILITY_UNVERIFIED,
        ),
        (
            CapabilityDisposition.UNKNOWN,
            CapabilityEvidenceStatus.UNKNOWN,
            ProcedureValidationStatus.INCONCLUSIVE,
            ProcedureFindingCode.CAPABILITY_UNKNOWN,
        ),
    ),
)
def test_check_9_requires_current_verified_capability_evidence(
    disposition: CapabilityDisposition,
    evidence_status: CapabilityEvidenceStatus,
    expected_status: ProcedureValidationStatus,
    expected_code: ProcedureFindingCode,
) -> None:
    request = valid_request().model_copy(
        update={"capability_assessments": (_assessment(disposition, evidence_status),)}
    )

    result = compile_method(request)
    assert result.report.status is expected_status
    assert expected_code in tuple(item.code for item in result.report.findings)


def test_check_10_treats_unknown_validator_catalog_fact_as_inconclusive() -> None:
    request = valid_request()
    catalog_entry = RegisteredValidator(
        validator=request.validator_catalog[0].validator,
        validator_version="validator-v1",
        registration=CatalogFactStatus.UNKNOWN,
    )
    result = compile_method(request.model_copy(update={"validator_catalog": (catalog_entry,)}))

    assert result.report.status is ProcedureValidationStatus.INCONCLUSIVE
    assert _finding_codes(result) == (
        ProcedureFindingCode.VALIDATOR_REGISTRATION_UNKNOWN,
        ProcedureFindingCode.VALIDATOR_REGISTRATION_UNKNOWN,
    )


def test_checks_6_and_10_do_not_synthesize_missing_incomplete_catalog_facts() -> None:
    request = valid_request().model_copy(
        update={
            "tool_catalog": (),
            "tool_catalog_complete": False,
            "validator_catalog": (),
            "validator_catalog_complete": False,
        }
    )
    result = compile_method(request)

    assert result.report.status is ProcedureValidationStatus.INCONCLUSIVE
    assert ProcedureFindingCode.TOOL_CATALOG_UNKNOWN in _finding_codes(result)
    assert ProcedureFindingCode.VALIDATOR_REGISTRATION_UNKNOWN in _finding_codes(result)


@pytest.mark.parametrize("explicit_unknown", (False, True))
def test_downstream_unknown_external_artifact_remains_inconclusive(
    explicit_unknown: bool,
) -> None:
    request = valid_request()
    second = _rebuild_step(
        request.candidate.stages[1],
        input_artifact_ids=("external-unknown", "prepared"),
    )
    catalog = request.artifact_catalog
    if explicit_unknown:
        catalog = (
            ArtifactCatalogEntry(
                artifact_id="external-unknown",
                artifact=None,
                availability=CatalogFactStatus.UNKNOWN,
            ),
            *catalog,
        )
    request = _replace_step(request, 1, second).model_copy(
        update={
            "artifact_catalog": catalog,
            "artifact_catalog_complete": explicit_unknown,
        }
    )

    result = compile_method(request)

    assert result.report.status is ProcedureValidationStatus.INCONCLUSIVE
    assert ProcedureFindingCode.ARTIFACT_CATALOG_UNKNOWN in _finding_codes(result)
    assert ProcedureFindingCode.MISSING_REFERENCED_OUTPUT not in _finding_codes(result)


def _snapshot_payload() -> dict[str, Any]:
    return valid_request().model_dump(mode="python")


def _contradictory_snapshot_entries(field_name: str) -> tuple[object, object]:
    request = valid_request()
    if field_name == "capability_assessments":
        return request.capability_assessments[0], _assessment(
            CapabilityDisposition.UNKNOWN,
            CapabilityEvidenceStatus.UNKNOWN,
        )
    if field_name == "artifact_catalog":
        return request.artifact_catalog[0], ArtifactCatalogEntry(
            artifact_id="source",
            artifact=None,
            availability=CatalogFactStatus.UNKNOWN,
        )
    if field_name == "tool_catalog":
        return request.tool_catalog[0], RegisteredTool(
            tool=request.tool_catalog[0].tool,
            availability=CatalogFactStatus.UNKNOWN,
            authorization=CatalogFactStatus.UNKNOWN,
        )
    return request.validator_catalog[0], RegisteredValidator(
        validator=request.validator_catalog[0].validator,
        validator_version=request.validator_catalog[0].validator_version,
        registration=CatalogFactStatus.UNKNOWN,
    )


@pytest.mark.parametrize(
    "field_name",
    (
        "capability_assessments",
        "artifact_catalog",
        "tool_catalog",
        "validator_catalog",
    ),
)
def test_direct_parsing_rejects_duplicate_snapshot_keys_in_either_position(
    field_name: str,
) -> None:
    left, right = _contradictory_snapshot_entries(field_name)
    for values in ((left, right), (right, left)):
        payload = _snapshot_payload()
        payload[field_name] = values
        with pytest.raises(ValidationError, match="unique logical keys"):
            ProcedureCompilationRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "second_entry"),
    (
        (
            "capability_assessments",
            _assessment(requirement_id="requirement-2"),
        ),
        (
            "artifact_catalog",
            ArtifactCatalogEntry(
                artifact_id="z-source",
                artifact=None,
                availability=CatalogFactStatus.UNKNOWN,
            ),
        ),
        (
            "tool_catalog",
            RegisteredTool(
                tool=_actor("z-tool", ActorKind.TOOL),
                availability=CatalogFactStatus.UNKNOWN,
                authorization=CatalogFactStatus.UNKNOWN,
            ),
        ),
        (
            "validator_catalog",
            RegisteredValidator(
                validator=_actor("z-validator", ActorKind.HUMAN),
                validator_version="validator-v1",
                registration=CatalogFactStatus.UNKNOWN,
            ),
        ),
    ),
)
def test_direct_parsing_rejects_noncanonical_snapshot_order(
    field_name: str,
    second_entry: object,
) -> None:
    payload = _snapshot_payload()
    payload[field_name] = (second_entry, payload[field_name][0])

    with pytest.raises(ValidationError, match="canonical order"):
        ProcedureCompilationRequest.model_validate(payload)


def test_check_11_requires_evidence_requirements() -> None:
    request = valid_request()
    first = _rebuild_step(request.candidate.stages[0], evidence_requirements=())

    assert ProcedureFindingCode.MISSING_EVIDENCE_REQUIREMENT in _codes(
        _replace_step(request, 0, first)
    )


def test_check_13_rejects_unbounded_or_nonterminating_recovery_paths() -> None:
    request = valid_request()
    first = _rebuild_step(
        request.candidate.stages[0],
        recovery=RecoveryDirective(target_step_id="validate", max_attempts=1),
    )
    second = _rebuild_step(
        request.candidate.stages[1],
        recovery=RecoveryDirective(target_step_id="prepare", max_attempts=1),
    )
    request = _replace_step(_replace_step(request, 0, first), 1, second)

    assert ProcedureFindingCode.UNBOUNDED_RECOVERY in _codes(request)


def test_check_14_rejects_an_operation_outside_the_closed_vocabulary() -> None:
    request = valid_request()
    raw_step = request.candidate.stages[0].model_construct(
        **{
            **request.candidate.stages[0].__dict__,
            "operation": "RUN_ARBITRARY_COMMAND",
        }
    )
    candidate = request.candidate.model_construct(
        **{
            **request.candidate.__dict__,
            "stages": (raw_step, request.candidate.stages[1]),
        }
    )
    raw_procedure = compile_method(request).procedure.model_construct(
        **{
            **compile_method(request).procedure.__dict__,
            "steps": candidate.stages,
        }
    )

    assert ProcedureFindingCode.UNKNOWN_OPERATION in tuple(
        finding.code for finding in validate_procedure(request, raw_procedure)
    )


def test_check_15_schema_rejects_forbidden_execution_fields_at_parse_boundary() -> None:
    payload = valid_request().model_dump(mode="python")
    payload["command"] = "python -c pass"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProcedureCompilationRequest.model_validate(payload)


def test_check_16_reuses_progress_validation_for_invalid_weights() -> None:
    request = valid_request()
    first = _rebuild_step(request.candidate.stages[0], progress_weight=Decimal("0.40"))

    assert ProcedureFindingCode.INVALID_PROGRESS_MAPPING in _codes(
        _replace_step(request, 0, first)
    )


@pytest.mark.parametrize("model_name", ("candidate", "step", "procedure", "result"))
def test_direct_parsing_rejects_contradictory_rehashed_results(model_name: str) -> None:
    result = compile_method(valid_request())
    models = {
        "candidate": result.procedure.source_candidate,
        "step": result.procedure.steps[0],
        "procedure": result.procedure,
        "result": result,
    }
    model = models[model_name]
    payload = model.model_dump(mode="python")
    hash_field = "result_hash" if isinstance(model, ProcedureCompilationResult) else "content_hash"
    payload[hash_field] = "f" * 64

    with pytest.raises(ValidationError, match="canonically address"):
        type(model).model_validate(payload)


def test_direct_parsing_rejects_a_rehashed_result_with_a_contradictory_request_hash() -> None:
    result = compile_method(valid_request())
    contradictory = result.model_copy(update={"request_hash": "9" * 64})
    payload = contradictory.model_dump(mode="python")
    payload["result_hash"] = canonical_model_hash(
        contradictory,
        exclude_fields={"result_hash"},
    )

    with pytest.raises(ValidationError, match="request hash"):
        ProcedureCompilationResult.model_validate(payload)


def test_closed_operation_vocabulary_rejects_arbitrary_commands_imports_and_uris() -> None:
    step_payload = valid_request().candidate.stages[0].model_dump(mode="python")
    step_payload["operation"] = "https://provider.example/execute"

    with pytest.raises(ValidationError):
        ProcedureStep.model_validate(step_payload)
    assert tuple(ProcedureOperation) == (
        ProcedureOperation.INSPECT_DECLARED_ARTIFACT,
        ProcedureOperation.DERIVE_STRUCTURED_CANDIDATE,
        ProcedureOperation.RUN_REGISTERED_DETERMINISTIC_FIXTURE,
        ProcedureOperation.EVALUATE_WITH_REGISTERED_VALIDATOR,
        ProcedureOperation.RECORD_DECLARED_OUTPUT,
    )


def test_all_models_are_strict_frozen_and_extra_forbidden() -> None:
    result = compile_method(valid_request())
    with pytest.raises(ValidationError):
        result.procedure.steps[0].order = 2
    with pytest.raises(ValidationError):
        ExecutableProcedure.model_validate(
            {**result.procedure.model_dump(mode="python"), "secret": "protected-answer"}
        )


def _finding_codes(result: ProcedureCompilationResult) -> tuple[ProcedureFindingCode, ...]:
    return tuple(item.code for item in result.report.findings)
