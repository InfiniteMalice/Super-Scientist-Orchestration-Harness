from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from super_scientist.domain.cognition.models import (
    CapabilityDisposition,
    CapabilityEvidenceStatus,
)
from super_scientist.domain.improvement.models import ResourceBudget
from super_scientist.domain.procedures.models import (
    CatalogFactStatus,
    ExecutableProcedure,
    ProcedureAuthority,
    ProcedureCompilationRequest,
    ProcedureCompilationResult,
    ProcedureFinding,
    ProcedureFindingCode,
    ProcedureFindingSeverity,
    ProcedureOperation,
    ProcedureStep,
    ProcedureValidationReport,
    ProcedureValidationStatus,
    ProgressBudgetCategory,
    canonical_model_hash,
)
from super_scientist.domain.procedures.progress_binding import validate_progress_mapping

_IMPOSSIBLE_AUTHORITIES = frozenset(
    {
        ProcedureAuthority.GOVERNANCE_WRITE,
        ProcedureAuthority.TRANSACTION_WRITE,
        ProcedureAuthority.PROTECTED_EVALUATOR,
        ProcedureAuthority.PROTECTED_ANSWER_ACCESS,
    }
)
_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "arbitrary_uri",
        "chain_of_thought",
        "command",
        "command_line",
        "dynamic_import",
        "hidden_reasoning",
        "import_path",
        "model_sdk",
        "network_request",
        "protected_answer",
        "provider_request",
        "secret",
        "subprocess",
        "training_request",
    }
)


def _finding(
    check_number: int,
    code: ProcedureFindingCode,
    severity: ProcedureFindingSeverity,
    message: str,
    subject_id: str | None = None,
) -> ProcedureFinding:
    return ProcedureFinding(
        check_number=check_number,
        code=code,
        severity=severity,
        subject_id=subject_id,
        message=message,
    )


def _ordered(findings: Iterable[ProcedureFinding]) -> tuple[ProcedureFinding, ...]:
    return tuple(
        sorted(
            findings,
            key=lambda item: (item.check_number, item.subject_id or "", item.code.value),
        )
    )


def _step_map(steps: tuple[ProcedureStep, ...]) -> dict[str, ProcedureStep]:
    result: dict[str, ProcedureStep] = {}
    for step in steps:
        result.setdefault(step.step_id, step)
    return result


def _producer_map(steps: tuple[ProcedureStep, ...]) -> dict[str, tuple[str, ...]]:
    producers: dict[str, list[str]] = {}
    for step in steps:
        for output in step.outputs:
            producers.setdefault(output.artifact_id, []).append(step.step_id)
    return {key: tuple(value) for key, value in producers.items()}


def _ancestor_ids(step_id: str, steps_by_id: Mapping[str, ProcedureStep]) -> frozenset[str]:
    visited: set[str] = set()
    step = steps_by_id.get(step_id)
    pending = list(step.dependency_ids) if step is not None else []
    while pending:
        dependency_id = pending.pop()
        if dependency_id in visited:
            continue
        visited.add(dependency_id)
        dependency = steps_by_id.get(dependency_id)
        if dependency is not None:
            pending.extend(dependency.dependency_ids)
    return frozenset(visited)


def _has_dependency_cycle(steps: tuple[ProcedureStep, ...]) -> bool:
    steps_by_id = _step_map(steps)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> bool:
        if step_id in visiting:
            return True
        if step_id in visited:
            return False
        visiting.add(step_id)
        step = steps_by_id[step_id]
        for dependency_id in step.dependency_ids:
            if dependency_id in steps_by_id and visit(dependency_id):
                return True
        visiting.remove(step_id)
        visited.add(step_id)
        return False

    return any(visit(step_id) for step_id in steps_by_id if step_id not in visited)


def _check_1_versions(
    request: ProcedureCompilationRequest,
    _procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    findings: list[ProcedureFinding] = []
    if request.schema_version != 1 or request.candidate.schema_version != 1:
        findings.append(
            _finding(
                1,
                ProcedureFindingCode.UNSUPPORTED_SCHEMA_VERSION,
                ProcedureFindingSeverity.ERROR,
                "procedure compilation schema version is unsupported",
            )
        )
    if request.compiler_version not in request.supported_compiler_versions:
        findings.append(
            _finding(
                1,
                ProcedureFindingCode.UNSUPPORTED_COMPILER_VERSION,
                ProcedureFindingSeverity.ERROR,
                "procedure compiler version is unsupported",
                request.compiler_id,
            )
        )
    return tuple(findings)


def _check_2_identity_and_order(
    _request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    findings: list[ProcedureFinding] = []
    step_ids = tuple(step.step_id for step in procedure.steps)
    duplicate_ids = sorted({step_id for step_id in step_ids if step_ids.count(step_id) > 1})
    findings.extend(
        _finding(
            2,
            ProcedureFindingCode.DUPLICATE_STEP_ID,
            ProcedureFindingSeverity.ERROR,
            "procedure step identifiers must be unique",
            step_id,
        )
        for step_id in duplicate_ids
    )
    expected = tuple(sorted(procedure.steps, key=lambda step: (step.order, step.step_id)))
    expected_orders = tuple(range(1, len(procedure.steps) + 1))
    actual_orders = tuple(step.order for step in procedure.steps)
    if procedure.steps != expected or actual_orders != expected_orders:
        findings.append(
            _finding(
                2,
                ProcedureFindingCode.NONCANONICAL_STEP_ORDER,
                ProcedureFindingSeverity.ERROR,
                "procedure steps must use contiguous canonical order",
            )
        )
    for step in procedure.steps:
        collections = (
            step.input_artifact_ids,
            step.dependency_ids,
            step.allowed_tool_ids,
            step.capability_requirement_ids,
        )
        if any(values != tuple(sorted(set(values))) for values in collections):
            findings.append(
                _finding(
                    2,
                    ProcedureFindingCode.NONCANONICAL_STEP_ORDER,
                    ProcedureFindingSeverity.ERROR,
                    "procedure step references must be unique and canonically ordered",
                    step.step_id,
                )
            )
    return tuple(findings)


def _check_3_dependencies(
    _request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    step_ids = {step.step_id for step in procedure.steps}
    findings = [
        _finding(
            3,
            ProcedureFindingCode.UNKNOWN_DEPENDENCY,
            ProcedureFindingSeverity.ERROR,
            "procedure dependency does not identify a declared step",
            step.step_id,
        )
        for step in procedure.steps
        if any(dependency_id not in step_ids for dependency_id in step.dependency_ids)
    ]
    if _has_dependency_cycle(procedure.steps):
        findings.append(
            _finding(
                3,
                ProcedureFindingCode.DEPENDENCY_CYCLE,
                ProcedureFindingSeverity.ERROR,
                "procedure dependency graph contains a cycle",
            )
        )
    return tuple(findings)


def _check_4_artifact_inputs(
    request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    entries = {entry.artifact_id: entry for entry in request.artifact_catalog}
    producers = _producer_map(procedure.steps)
    steps_by_id = _step_map(procedure.steps)
    findings: list[ProcedureFinding] = []
    for step in procedure.steps:
        ancestors = _ancestor_ids(step.step_id, steps_by_id)
        for artifact_id in step.input_artifact_ids:
            if artifact_id in producers and any(
                producer_id in ancestors for producer_id in producers[artifact_id]
            ):
                continue
            entry = entries.get(artifact_id)
            if entry is not None and entry.availability is CatalogFactStatus.PRESENT:
                continue
            if entry is not None and entry.availability is CatalogFactStatus.UNKNOWN:
                findings.append(
                    _finding(
                        4,
                        ProcedureFindingCode.ARTIFACT_CATALOG_UNKNOWN,
                        ProcedureFindingSeverity.UNKNOWN,
                        "required artifact availability is unknown",
                        artifact_id,
                    )
                )
            elif entry is None and not request.artifact_catalog_complete:
                findings.append(
                    _finding(
                        4,
                        ProcedureFindingCode.ARTIFACT_CATALOG_UNKNOWN,
                        ProcedureFindingSeverity.UNKNOWN,
                        "required artifact is absent from an incomplete catalog",
                        artifact_id,
                    )
                )
            else:
                findings.append(
                    _finding(
                        4,
                        ProcedureFindingCode.MISSING_ARTIFACT,
                        ProcedureFindingSeverity.ERROR,
                        "required artifact is not available from an ancestor or catalog",
                        artifact_id,
                    )
                )
    return tuple(findings)


def _check_5_artifact_producers(
    _request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    return tuple(
        _finding(
            5,
            ProcedureFindingCode.AMBIGUOUS_ARTIFACT_PRODUCER,
            ProcedureFindingSeverity.ERROR,
            "produced artifact has more than one declared producer",
            artifact_id,
        )
        for artifact_id, producer_ids in _producer_map(procedure.steps).items()
        if len(producer_ids) != 1
    )


def _check_6_tools(
    request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    catalog = {entry.tool.actor_id: entry for entry in request.tool_catalog}
    findings: list[ProcedureFinding] = []
    for step in procedure.steps:
        for tool_id in step.allowed_tool_ids:
            entry = catalog.get(tool_id)
            if entry is None:
                findings.append(
                    _finding(
                        6,
                        (
                            ProcedureFindingCode.TOOL_UNAVAILABLE
                            if request.tool_catalog_complete
                            else ProcedureFindingCode.TOOL_CATALOG_UNKNOWN
                        ),
                        (
                            ProcedureFindingSeverity.ERROR
                            if request.tool_catalog_complete
                            else ProcedureFindingSeverity.UNKNOWN
                        ),
                        "required tool is not established by the fixed catalog",
                        tool_id,
                    )
                )
                continue
            if entry.availability is CatalogFactStatus.ABSENT:
                findings.append(
                    _finding(
                        6,
                        ProcedureFindingCode.TOOL_UNAVAILABLE,
                        ProcedureFindingSeverity.ERROR,
                        "required registered tool is unavailable",
                        tool_id,
                    )
                )
            elif entry.availability is CatalogFactStatus.UNKNOWN:
                findings.append(
                    _finding(
                        6,
                        ProcedureFindingCode.TOOL_CATALOG_UNKNOWN,
                        ProcedureFindingSeverity.UNKNOWN,
                        "required registered tool availability is unknown",
                        tool_id,
                    )
                )
            if entry.authorization is CatalogFactStatus.ABSENT:
                findings.append(
                    _finding(
                        6,
                        ProcedureFindingCode.TOOL_UNAUTHORIZED,
                        ProcedureFindingSeverity.ERROR,
                        "required registered tool is not authorized for the session",
                        tool_id,
                    )
                )
            elif entry.authorization is CatalogFactStatus.UNKNOWN:
                findings.append(
                    _finding(
                        6,
                        ProcedureFindingCode.TOOL_CATALOG_UNKNOWN,
                        ProcedureFindingSeverity.UNKNOWN,
                        "required registered tool authorization is unknown",
                        tool_id,
                    )
                )
    return tuple(findings)


def _check_7_authority(
    _request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    return tuple(
        _finding(
            7,
            ProcedureFindingCode.IMPOSSIBLE_AUTHORITY,
            ProcedureFindingSeverity.ERROR,
            "procedure step requests authority unavailable to compiled procedures",
            step.step_id,
        )
        for step in procedure.steps
        if any(authority in _IMPOSSIBLE_AUTHORITIES for authority in step.required_authorities)
    )


def _check_8_outputs(
    request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    produced_ids = set(_producer_map(procedure.steps))
    catalog_ids = {
        entry.artifact_id
        for entry in request.artifact_catalog
        if entry.availability is CatalogFactStatus.PRESENT
    }
    findings = [
        _finding(
            8,
            ProcedureFindingCode.UNDEFINED_OUTPUT,
            ProcedureFindingSeverity.ERROR,
            "candidate method declares an output that no step produces",
            output_id,
        )
        for output_id in request.candidate.expected_output_ids
        if output_id not in produced_ids
    ]
    for step in procedure.steps:
        findings.extend(
            _finding(
                8,
                ProcedureFindingCode.MISSING_REFERENCED_OUTPUT,
                ProcedureFindingSeverity.ERROR,
                "procedure step references an artifact that is neither cataloged nor produced",
                artifact_id,
            )
            for artifact_id in step.input_artifact_ids
            if step.dependency_ids
            and artifact_id not in produced_ids
            and artifact_id not in catalog_ids
        )
    return tuple(findings)


def _check_9_capabilities(
    request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    assessments = {
        assessment.requirement.requirement_id: assessment
        for assessment in request.capability_assessments
    }
    requirement_ids = set(procedure.required_capability_ids)
    requirement_ids.update(
        requirement_id
        for step in procedure.steps
        for requirement_id in step.capability_requirement_ids
    )
    findings: list[ProcedureFinding] = []
    for requirement_id in sorted(requirement_ids):
        assessment = assessments.get(requirement_id)
        if assessment is None or assessment.disposition is CapabilityDisposition.UNKNOWN:
            findings.append(
                _finding(
                    9,
                    ProcedureFindingCode.CAPABILITY_UNKNOWN,
                    ProcedureFindingSeverity.UNKNOWN,
                    "current capability evidence is unknown",
                    requirement_id,
                )
            )
        elif (
            assessment.disposition is not CapabilityDisposition.SATISFIED
            or assessment.evidence_status is not CapabilityEvidenceStatus.VERIFIED
            or not assessment.verified_assertion_ids
        ):
            findings.append(
                _finding(
                    9,
                    ProcedureFindingCode.CAPABILITY_UNVERIFIED,
                    ProcedureFindingSeverity.ERROR,
                    "capability requirement lacks current verified evidence",
                    requirement_id,
                )
            )
    return tuple(findings)


def _check_10_validators(
    request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    catalog = {
        (entry.validator.actor_id, entry.validator_version): entry
        for entry in request.validator_catalog
    }
    findings: list[ProcedureFinding] = []
    for step in procedure.steps:
        entry = catalog.get((step.validator.actor_id, step.validator_version))
        if entry is None:
            findings.append(
                _finding(
                    10,
                    (
                        ProcedureFindingCode.INVALID_VALIDATOR_BINDING
                        if request.validator_catalog_complete
                        else ProcedureFindingCode.VALIDATOR_REGISTRATION_UNKNOWN
                    ),
                    (
                        ProcedureFindingSeverity.ERROR
                        if request.validator_catalog_complete
                        else ProcedureFindingSeverity.UNKNOWN
                    ),
                    "exact validator identity and version are not established",
                    step.step_id,
                )
            )
        elif entry.validator != step.validator:
            findings.append(
                _finding(
                    10,
                    ProcedureFindingCode.INVALID_VALIDATOR_BINDING,
                    ProcedureFindingSeverity.ERROR,
                    "validator actor identity does not match the registered identity",
                    step.step_id,
                )
            )
        elif entry.registration is CatalogFactStatus.ABSENT:
            findings.append(
                _finding(
                    10,
                    ProcedureFindingCode.INVALID_VALIDATOR_BINDING,
                    ProcedureFindingSeverity.ERROR,
                    "exact validator identity and version are not registered",
                    step.step_id,
                )
            )
        elif entry.registration is CatalogFactStatus.UNKNOWN:
            findings.append(
                _finding(
                    10,
                    ProcedureFindingCode.VALIDATOR_REGISTRATION_UNKNOWN,
                    ProcedureFindingSeverity.UNKNOWN,
                    "exact validator registration is unknown",
                    step.step_id,
                )
            )
    return tuple(findings)


def _check_11_completion(
    request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    findings: list[ProcedureFinding] = []
    for step in procedure.steps:
        if not step.completion_criteria:
            findings.append(
                _finding(
                    11,
                    ProcedureFindingCode.MISSING_COMPLETION_CRITERIA,
                    ProcedureFindingSeverity.ERROR,
                    "procedure step requires completion criteria",
                    step.step_id,
                )
            )
        if not step.evidence_requirements:
            findings.append(
                _finding(
                    11,
                    ProcedureFindingCode.MISSING_EVIDENCE_REQUIREMENT,
                    ProcedureFindingSeverity.ERROR,
                    "procedure step requires retained evidence requirements",
                    step.step_id,
                )
            )
    if not request.candidate.verifier_requirement_ids:
        findings.append(
            _finding(
                11,
                ProcedureFindingCode.INVALID_VALIDATOR_BINDING,
                ProcedureFindingSeverity.ERROR,
                "candidate method requires an exact verifier requirement",
            )
        )
    return tuple(findings)


def _resource_values(resource: ResourceBudget) -> dict[str, Decimal]:
    return {
        "cost_usd": Decimal(str(resource.cost_usd)),
        "compute_units": Decimal(str(resource.compute_units)),
        "tokens": Decimal(resource.tokens),
        "elapsed_seconds": Decimal(str(resource.elapsed_seconds)),
        "tool_calls": Decimal(resource.tool_calls),
        "human_interventions": Decimal(resource.human_interventions),
    }


def _check_12_budgets(
    request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    totals: dict[ProgressBudgetCategory, dict[str, Decimal]] = {}
    findings: list[ProcedureFinding] = []
    for step in procedure.steps:
        category = step.progress_budget_category
        if not isinstance(category, ProgressBudgetCategory):
            findings.append(
                _finding(
                    12,
                    ProcedureFindingCode.BUDGET_CATEGORY_UNKNOWN,
                    ProcedureFindingSeverity.ERROR,
                    "procedure resource cost does not map to a progress budget category",
                    step.step_id,
                )
            )
            continue
        category_totals = totals.setdefault(
            category,
            {field_name: Decimal("0") for field_name in _resource_values(step.resource_budget)},
        )
        for field_name, value in _resource_values(step.resource_budget).items():
            category_totals[field_name] += value
    for category, category_totals in totals.items():
        reserve = getattr(request.budget_envelope, category.value)
        reserve_values = _resource_values(reserve)
        if any(
            category_totals[field_name] > reserve_values[field_name]
            for field_name in category_totals
        ):
            findings.append(
                _finding(
                    12,
                    ProcedureFindingCode.BUDGET_EXCEEDED,
                    ProcedureFindingSeverity.ERROR,
                    "procedure resource allocation exceeds its progress budget reserve",
                    category.value,
                )
            )
    return tuple(findings)


def _check_13_termination(
    _request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    steps_by_id = _step_map(procedure.steps)
    findings: list[ProcedureFinding] = []
    recovery_targets: dict[str, str] = {}
    for step in procedure.steps:
        recovery = step.recovery
        target_id = recovery.target_step_id
        terminal = recovery.terminal_outcome
        invalid = (target_id is None) == (terminal is None)
        if target_id is not None:
            invalid = invalid or target_id == step.step_id or target_id not in steps_by_id
            invalid = invalid or recovery.max_attempts < 1
            recovery_targets[step.step_id] = target_id
        elif terminal is not None:
            invalid = invalid or recovery.max_attempts != 0
        if invalid:
            findings.append(
                _finding(
                    13,
                    ProcedureFindingCode.UNBOUNDED_RECOVERY,
                    ProcedureFindingSeverity.ERROR,
                    "procedure recovery must be bounded and terminate at a declared outcome",
                    step.step_id,
                )
            )
    for start_id in sorted(recovery_targets):
        visited: set[str] = set()
        current = start_id
        while current in recovery_targets:
            if current in visited:
                findings.append(
                    _finding(
                        13,
                        ProcedureFindingCode.UNBOUNDED_RECOVERY,
                        ProcedureFindingSeverity.ERROR,
                        "procedure recovery graph must not contain a cycle",
                        start_id,
                    )
                )
                break
            visited.add(current)
            current = recovery_targets[current]
    return tuple(findings)


def _check_14_operations(
    _request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    return tuple(
        _finding(
            14,
            ProcedureFindingCode.UNKNOWN_OPERATION,
            ProcedureFindingSeverity.ERROR,
            "procedure step operation is outside the closed vocabulary",
            step.step_id,
        )
        for step in procedure.steps
        if not isinstance(step.operation, ProcedureOperation)
    )


def _forbidden_keys(value: Any) -> tuple[str, ...]:
    found: set[str] = set()
    if isinstance(value, BaseModel):
        return _forbidden_keys(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _FORBIDDEN_FIELD_NAMES:
                found.add(key.lower())
            found.update(_forbidden_keys(item))
    elif isinstance(value, (tuple, list)):
        for item in value:
            found.update(_forbidden_keys(item))
    return tuple(sorted(found))


def _check_15_forbidden_fields(
    request: ProcedureCompilationRequest,
    _procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    return tuple(
        _finding(
            15,
            ProcedureFindingCode.FORBIDDEN_FIELD,
            ProcedureFindingSeverity.ERROR,
            "procedure input contains a forbidden execution or protected-data field",
            field_name,
        )
        for field_name in _forbidden_keys(request)
    )


def _check_16_progress_mapping(
    _request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    try:
        validate_progress_mapping(procedure)
    except (TypeError, ValueError):
        return (
            _finding(
                16,
                ProcedureFindingCode.INVALID_PROGRESS_MAPPING,
                ProcedureFindingSeverity.ERROR,
                "generated progress mapping fails progress-domain validation",
            ),
        )
    return ()


_CHECKS: tuple[
    Callable[[ProcedureCompilationRequest, ExecutableProcedure], tuple[ProcedureFinding, ...]],
    ...,
] = (
    _check_1_versions,
    _check_2_identity_and_order,
    _check_3_dependencies,
    _check_4_artifact_inputs,
    _check_5_artifact_producers,
    _check_6_tools,
    _check_7_authority,
    _check_8_outputs,
    _check_9_capabilities,
    _check_10_validators,
    _check_11_completion,
    _check_12_budgets,
    _check_13_termination,
    _check_14_operations,
    _check_15_forbidden_fields,
    _check_16_progress_mapping,
)


def compile_declared_stages(request: ProcedureCompilationRequest) -> ExecutableProcedure:
    request_hash = canonical_model_hash(request)
    producers = _producer_map(request.candidate.stages)
    required_inputs = tuple(
        sorted(
            {
                artifact_id
                for step in request.candidate.stages
                for artifact_id in step.input_artifact_ids
                if artifact_id not in producers
            }
        )
    )
    return ExecutableProcedure.build(
        procedure_id=f"procedure-{request_hash}",
        compiler_id=request.compiler_id,
        compiler_version=request.compiler_version,
        steps=request.candidate.stages,
        required_capability_ids=tuple(
            sorted(set(request.candidate.claimed_capability_requirement_ids))
        ),
        required_artifact_input_ids=required_inputs,
        declared_outputs=tuple(
            output for step in request.candidate.stages for output in step.outputs
        ),
        source_candidate=request.candidate,
        source_candidate_hash=request.candidate.content_hash,
        compilation_request_hash=request_hash,
    )


def validate_procedure(
    request: ProcedureCompilationRequest,
    procedure: ExecutableProcedure,
) -> tuple[ProcedureFinding, ...]:
    return _ordered(
        finding for check in _CHECKS for finding in check(request, procedure)
    )


def compile_method(request: ProcedureCompilationRequest) -> ProcedureCompilationResult:
    procedure = compile_declared_stages(request)
    findings = validate_procedure(request, procedure)
    if any(finding.severity is ProcedureFindingSeverity.ERROR for finding in findings):
        status = ProcedureValidationStatus.INVALID
    elif findings:
        status = ProcedureValidationStatus.INCONCLUSIVE
    else:
        status = ProcedureValidationStatus.VALID
    report = ProcedureValidationReport(
        status=status,
        findings=findings,
        checks_run=tuple(range(1, 17)),
    )
    return ProcedureCompilationResult.build(
        compiler_id=request.compiler_id,
        compiler_version=request.compiler_version,
        request_hash=canonical_model_hash(request),
        procedure=procedure,
        report=report,
    )


__all__ = ["compile_declared_stages", "compile_method", "validate_procedure"]
