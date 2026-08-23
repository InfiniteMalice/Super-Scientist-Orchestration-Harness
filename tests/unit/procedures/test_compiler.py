from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from super_scientist.domain.cognition.models import (
    CapabilityAssessment,
    CapabilityDisposition,
    CapabilityEvidenceStatus,
    CapabilityRequirement,
)
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.models import ResourceBudget
from super_scientist.domain.procedures import (
    ArtifactCatalogEntry,
    CandidateMethod,
    CatalogFactStatus,
    DeclaredProcedureArtifact,
    ProcedureAuthority,
    ProcedureCompilationRequest,
    ProcedureFindingCode,
    ProcedureOperation,
    ProcedureStep,
    ProcedureTerminalOutcome,
    ProcedureValidationStatus,
    ProgressBudgetCategory,
    RecoveryDirective,
    RegisteredTool,
    RegisteredValidator,
    compile_method,
)
from super_scientist.domain.progress.models import BudgetReserves

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "a" * 64


def _budget(value: int = 10) -> ResourceBudget:
    return ResourceBudget(
        cost_usd=float(value),
        compute_units=float(value),
        tokens=value,
        elapsed_seconds=float(value),
        tool_calls=value,
        human_interventions=value,
    )


def _artifact(artifact_id: str) -> DeclaredProcedureArtifact:
    return DeclaredProcedureArtifact.build(
        artifact_id=artifact_id,
        media_type="application/json",
        integrity_sha256="f" * 64,
    )


def _actor(actor_id: str, kind: ActorKind) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=kind, created_at=NOW)


def _step(
    step_id: str,
    order: int,
    *,
    inputs: tuple[str, ...],
    output_id: str,
    dependencies: tuple[str, ...],
    operation: ProcedureOperation = ProcedureOperation.DERIVE_STRUCTURED_CANDIDATE,
    tool_ids: tuple[str, ...] = (),
    authority: tuple[ProcedureAuthority, ...] = (ProcedureAuthority.DERIVE_PUBLIC_DATA,),
    completion_criteria: tuple[str, ...] = ("Structured output matches schema",),
    evidence_requirements: tuple[str, ...] = ("retained-output",),
    validator_version: str = "validator-v1",
    recovery: RecoveryDirective | None = None,
    category: ProgressBudgetCategory = ProgressBudgetCategory.EXPLORATION,
    budget: ResourceBudget | None = None,
    weight: Decimal = Decimal("0.50"),
) -> ProcedureStep:
    return ProcedureStep.build(
        step_id=step_id,
        order=order,
        operation=operation,
        objective=f"Complete {step_id}",
        input_artifact_ids=inputs,
        outputs=(_artifact(output_id),),
        dependency_ids=dependencies,
        allowed_tool_ids=tool_ids,
        required_authorities=authority,
        preconditions=("Declared inputs are available",),
        completion_criteria=completion_criteria,
        evidence_requirements=evidence_requirements,
        validator=_actor("validator", ActorKind.HUMAN),
        validator_version=validator_version,
        failure_signals=("validator-rejected",),
        recovery=recovery
        or RecoveryDirective(terminal_outcome=ProcedureTerminalOutcome.ABANDONED),
        capability_requirement_ids=("requirement-1",),
        progress_budget_category=category,
        resource_budget=budget or _budget(1),
        progress_weight=weight,
    )


def _candidate(
    *steps: ProcedureStep,
    expected_output_ids: tuple[str, ...] = ("final",),
) -> CandidateMethod:
    return CandidateMethod.build(
        method_id="method-1",
        objective="Produce a validated final artifact",
        assumptions=("All inputs are public",),
        stages=steps,
        evidence_refs=(
            ArtifactRef(
                sha256="e" * 64,
                size_bytes=4,
                media_type="application/json",
                relative_path=f"sha256/ee/{'e' * 64}",
            ),
        ),
        claimed_capability_requirement_ids=("requirement-1",),
        expected_output_ids=expected_output_ids,
        verifier_requirement_ids=("validator:validator-v1",),
        resource_estimate=_budget(2),
        termination_conditions=("Final validator accepts or the method is abandoned",),
        provenance_contribution_ids=("contribution-1",),
    )


def _assessment(
    disposition: CapabilityDisposition = CapabilityDisposition.SATISFIED,
    evidence_status: CapabilityEvidenceStatus = CapabilityEvidenceStatus.VERIFIED,
    *,
    requirement_id: str = "requirement-1",
) -> CapabilityAssessment:
    verified = ("assertion-1",) if evidence_status is CapabilityEvidenceStatus.VERIFIED else ()
    matched = () if evidence_status is CapabilityEvidenceStatus.UNKNOWN else ("assertion-1",)
    return CapabilityAssessment(
        profile_id="profile-1",
        actor_id="worker-1",
        requirement=CapabilityRequirement(
            requirement_id=requirement_id,
            capability_id="capability-1",
            task_family_id="task-family-1",
            evidence_snapshot_hash="c" * 64,
        ),
        matched_assertion_ids=matched,
        verified_assertion_ids=verified,
        disposition=disposition,
        evidence_status=evidence_status,
        missing_dimensions=("capability_evidence",)
        if disposition is CapabilityDisposition.UNKNOWN
        else (),
        failed_dimensions=("capability_support",)
        if disposition is CapabilityDisposition.UNSATISFIED
        else (),
    )


def valid_request() -> ProcedureCompilationRequest:
    first = _step(
        "prepare",
        1,
        inputs=("source",),
        output_id="prepared",
        dependencies=(),
        operation=ProcedureOperation.INSPECT_DECLARED_ARTIFACT,
        authority=(ProcedureAuthority.READ_DECLARED_ARTIFACT,),
    )
    second = _step(
        "validate",
        2,
        inputs=("prepared",),
        output_id="final",
        dependencies=("prepare",),
        operation=ProcedureOperation.EVALUATE_WITH_REGISTERED_VALIDATOR,
        tool_ids=("fixture-tool",),
        authority=(ProcedureAuthority.RUN_REGISTERED_TOOL,),
        category=ProgressBudgetCategory.VERIFICATION,
    )
    return ProcedureCompilationRequest(
        request_id="compile-request-1",
        compiler_id="procedure-compiler",
        compiler_version="1.0.0",
        candidate=_candidate(first, second),
        capability_assessments=(_assessment(),),
        artifact_catalog=(
            ArtifactCatalogEntry(
                artifact_id="source",
                artifact=ArtifactRef(
                    sha256="d" * 64,
                    size_bytes=10,
                    media_type="application/json",
                    relative_path=f"sha256/dd/{'d' * 64}",
                ),
                availability=CatalogFactStatus.PRESENT,
            ),
        ),
        artifact_catalog_complete=True,
        tool_catalog=(
            RegisteredTool(
                tool=_actor("fixture-tool", ActorKind.TOOL),
                availability=CatalogFactStatus.PRESENT,
                authorization=CatalogFactStatus.PRESENT,
            ),
        ),
        tool_catalog_complete=True,
        validator_catalog=(
            RegisteredValidator(
                validator=_actor("validator", ActorKind.HUMAN),
                validator_version="validator-v1",
                registration=CatalogFactStatus.PRESENT,
            ),
        ),
        validator_catalog_complete=True,
        budget_envelope=BudgetReserves(
            exploration=_budget(),
            implementation=_budget(),
            verification=_budget(),
            recovery=_budget(),
            finalization=_budget(),
        ),
    )


def _replace_step(
    request: ProcedureCompilationRequest,
    index: int,
    step: ProcedureStep,
) -> ProcedureCompilationRequest:
    stages = list(request.candidate.stages)
    stages[index] = step
    candidate_values = request.candidate.model_dump(mode="python", exclude={"content_hash"})
    candidate_values["stages"] = tuple(stages)
    return request.model_copy(update={"candidate": CandidateMethod.build(**candidate_values)})


def _rebuild_step(step: ProcedureStep, **updates: Any) -> ProcedureStep:
    values = step.model_dump(mode="python", exclude={"content_hash"})
    values.update(updates)
    return ProcedureStep.build(**values)


def request_with_cycle() -> ProcedureCompilationRequest:
    request = valid_request()
    first = _rebuild_step(request.candidate.stages[0], dependency_ids=("validate",))
    return _replace_step(request, 0, first)


def request_with_missing_input() -> ProcedureCompilationRequest:
    request = valid_request()
    first = _rebuild_step(request.candidate.stages[0], input_artifact_ids=("missing",))
    return _replace_step(request, 0, first)


def request_with_undefined_output() -> ProcedureCompilationRequest:
    request = valid_request()
    values = request.candidate.model_dump(mode="python", exclude={"content_hash"})
    values["expected_output_ids"] = ("undefined",)
    return request.model_copy(update={"candidate": CandidateMethod.build(**values)})


def request_with_unavailable_tool() -> ProcedureCompilationRequest:
    request = valid_request()
    tool = request.tool_catalog[0].model_copy(update={"availability": CatalogFactStatus.ABSENT})
    return request.model_copy(update={"tool_catalog": (tool,)})


def request_with_unauthorized_tool() -> ProcedureCompilationRequest:
    request = valid_request()
    tool = request.tool_catalog[0].model_copy(update={"authorization": CatalogFactStatus.ABSENT})
    return request.model_copy(update={"tool_catalog": (tool,)})


def request_without_completion_criteria() -> ProcedureCompilationRequest:
    request = valid_request()
    first = _rebuild_step(request.candidate.stages[0], completion_criteria=())
    return _replace_step(request, 0, first)


def request_with_invalid_validator() -> ProcedureCompilationRequest:
    request = valid_request()
    first = _rebuild_step(request.candidate.stages[0], validator_version="validator-v2")
    return _replace_step(request, 0, first)


def request_over_budget() -> ProcedureCompilationRequest:
    request = valid_request()
    first = _rebuild_step(request.candidate.stages[0], resource_budget=_budget(11))
    return _replace_step(request, 0, first)


def request_requiring_governance_write() -> ProcedureCompilationRequest:
    request = valid_request()
    first = _rebuild_step(
        request.candidate.stages[0],
        required_authorities=(ProcedureAuthority.GOVERNANCE_WRITE,),
    )
    return _replace_step(request, 0, first)


@pytest.mark.parametrize(
    ("request_factory", "code"),
    (
        (request_with_cycle, ProcedureFindingCode.DEPENDENCY_CYCLE),
        (request_with_missing_input, ProcedureFindingCode.MISSING_ARTIFACT),
        (request_with_undefined_output, ProcedureFindingCode.UNDEFINED_OUTPUT),
        (request_with_unavailable_tool, ProcedureFindingCode.TOOL_UNAVAILABLE),
        (request_with_unauthorized_tool, ProcedureFindingCode.TOOL_UNAUTHORIZED),
        (
            request_without_completion_criteria,
            ProcedureFindingCode.MISSING_COMPLETION_CRITERIA,
        ),
        (request_with_invalid_validator, ProcedureFindingCode.INVALID_VALIDATOR_BINDING),
        (request_over_budget, ProcedureFindingCode.BUDGET_EXCEEDED),
        (request_requiring_governance_write, ProcedureFindingCode.IMPOSSIBLE_AUTHORITY),
    ),
)
def test_invalid_method_preserves_exact_finding(
    request_factory: Callable[[], ProcedureCompilationRequest],
    code: ProcedureFindingCode,
) -> None:
    result = compile_method(request_factory())

    assert result.report.status is ProcedureValidationStatus.INVALID
    assert code in tuple(item.code for item in result.report.findings)
    assert result.procedure is not None


def test_valid_method_compiles_deterministically_and_retains_source_hash() -> None:
    request = valid_request()

    first = compile_method(request)
    second = compile_method(request)

    assert first == second
    assert first.report.status is ProcedureValidationStatus.VALID
    assert first.report.findings == ()
    assert first.procedure.source_candidate_hash == request.candidate.content_hash
    assert tuple(step.step_id for step in first.procedure.steps) == ("prepare", "validate")


def test_self_declared_arbitrary_compiler_version_cannot_become_valid() -> None:
    request = valid_request().model_copy(
        update={"compiler_version": "attacker-version"}
    )

    result = compile_method(request)

    assert result.report.status is ProcedureValidationStatus.INVALID
    assert ProcedureFindingCode.UNSUPPORTED_COMPILER_VERSION in tuple(
        finding.code for finding in result.report.findings
    )


def test_compiled_metadata_unions_candidate_and_step_capability_requirements() -> None:
    request = valid_request()
    second = _rebuild_step(
        request.candidate.stages[1],
        capability_requirement_ids=("requirement-1", "requirement-2"),
    )
    request = _replace_step(request, 1, second).model_copy(
        update={
            "capability_assessments": (
                request.capability_assessments[0],
                _assessment(requirement_id="requirement-2"),
            )
        }
    )

    result = compile_method(request)

    assert result.report.status is ProcedureValidationStatus.VALID
    assert result.procedure.required_capability_ids == ("requirement-1", "requirement-2")


def test_compiled_metadata_retains_nonancestor_producer_input_as_external() -> None:
    request = valid_request()
    second = _rebuild_step(request.candidate.stages[1], dependency_ids=())
    prepared = ArtifactCatalogEntry(
        artifact_id="prepared",
        artifact=ArtifactRef(
            sha256="4" * 64,
            size_bytes=8,
            media_type="application/json",
            relative_path=f"sha256/44/{'4' * 64}",
        ),
        availability=CatalogFactStatus.PRESENT,
    )
    request = _replace_step(request, 1, second).model_copy(
        update={"artifact_catalog": (prepared, *request.artifact_catalog)}
    )

    result = compile_method(request)

    assert result.report.status is ProcedureValidationStatus.VALID
    assert result.procedure.required_artifact_input_ids == ("prepared", "source")
