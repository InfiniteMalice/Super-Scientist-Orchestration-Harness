from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from super_scientist.domain.cognition.models import CapabilityAssessment
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.models import ResourceBudget
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.domain.progress.models import BudgetReserves, ProgressPlan

MAX_PROCEDURE_ITEMS = 64
MAX_PROCEDURE_FINDINGS = 1_024
MAX_IDENTIFIER_LENGTH = 200
MAX_TEXT_LENGTH = 2_000


def _strip_text(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


BoundedIdentifier = Annotated[
    StableIdentifier,
    Field(max_length=MAX_IDENTIFIER_LENGTH),
]
BoundedText = Annotated[
    str,
    BeforeValidator(_strip_text),
    Field(strict=True, min_length=1, max_length=MAX_TEXT_LENGTH),
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


def canonical_model_hash(
    model: BaseModel,
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    payload = model.model_dump(mode="json", exclude=exclude_fields or set())
    return sha256_hex(canonical_json_bytes(payload))


def _require_canonical_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must contain unique values")
    if values != tuple(sorted(values)):
        raise ValueError(f"{field_name} must be sorted in canonical order")
    return values


class ProcedureOperation(StrEnum):
    INSPECT_DECLARED_ARTIFACT = "INSPECT_DECLARED_ARTIFACT"
    DERIVE_STRUCTURED_CANDIDATE = "DERIVE_STRUCTURED_CANDIDATE"
    RUN_REGISTERED_DETERMINISTIC_FIXTURE = "RUN_REGISTERED_DETERMINISTIC_FIXTURE"
    EVALUATE_WITH_REGISTERED_VALIDATOR = "EVALUATE_WITH_REGISTERED_VALIDATOR"
    RECORD_DECLARED_OUTPUT = "RECORD_DECLARED_OUTPUT"


class ProcedureAuthority(StrEnum):
    READ_DECLARED_ARTIFACT = "READ_DECLARED_ARTIFACT"
    DERIVE_PUBLIC_DATA = "DERIVE_PUBLIC_DATA"
    RUN_REGISTERED_TOOL = "RUN_REGISTERED_TOOL"
    RECORD_DECLARED_OUTPUT = "RECORD_DECLARED_OUTPUT"
    GOVERNANCE_WRITE = "GOVERNANCE_WRITE"
    TRANSACTION_WRITE = "TRANSACTION_WRITE"
    PROTECTED_EVALUATOR = "PROTECTED_EVALUATOR"
    PROTECTED_ANSWER_ACCESS = "PROTECTED_ANSWER_ACCESS"


class ProcedureTerminalOutcome(StrEnum):
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    ABANDONED = "ABANDONED"


class ProgressBudgetCategory(StrEnum):
    EXPLORATION = "exploration"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    RECOVERY = "recovery"
    FINALIZATION = "finalization"


class CatalogFactStatus(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class ProcedureValidationStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    INCONCLUSIVE = "INCONCLUSIVE"


class ProcedureFindingSeverity(StrEnum):
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class ProcedureFindingCode(StrEnum):
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    UNSUPPORTED_COMPILER_VERSION = "UNSUPPORTED_COMPILER_VERSION"
    DUPLICATE_STEP_ID = "DUPLICATE_STEP_ID"
    NONCANONICAL_STEP_ORDER = "NONCANONICAL_STEP_ORDER"
    UNKNOWN_DEPENDENCY = "UNKNOWN_DEPENDENCY"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    MISSING_ARTIFACT = "MISSING_ARTIFACT"
    ARTIFACT_CATALOG_UNKNOWN = "ARTIFACT_CATALOG_UNKNOWN"
    AMBIGUOUS_ARTIFACT_PRODUCER = "AMBIGUOUS_ARTIFACT_PRODUCER"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    TOOL_UNAUTHORIZED = "TOOL_UNAUTHORIZED"
    TOOL_CATALOG_UNKNOWN = "TOOL_CATALOG_UNKNOWN"
    IMPOSSIBLE_AUTHORITY = "IMPOSSIBLE_AUTHORITY"
    UNDEFINED_OUTPUT = "UNDEFINED_OUTPUT"
    MISSING_REFERENCED_OUTPUT = "MISSING_REFERENCED_OUTPUT"
    CAPABILITY_UNVERIFIED = "CAPABILITY_UNVERIFIED"
    CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
    INVALID_VALIDATOR_BINDING = "INVALID_VALIDATOR_BINDING"
    VALIDATOR_REGISTRATION_UNKNOWN = "VALIDATOR_REGISTRATION_UNKNOWN"
    MISSING_COMPLETION_CRITERIA = "MISSING_COMPLETION_CRITERIA"
    MISSING_EVIDENCE_REQUIREMENT = "MISSING_EVIDENCE_REQUIREMENT"
    BUDGET_CATEGORY_UNKNOWN = "BUDGET_CATEGORY_UNKNOWN"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    UNBOUNDED_RECOVERY = "UNBOUNDED_RECOVERY"
    UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
    FORBIDDEN_FIELD = "FORBIDDEN_FIELD"
    INVALID_PROGRESS_MAPPING = "INVALID_PROGRESS_MAPPING"


class RecoveryDirective(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    target_step_id: BoundedIdentifier | None = None
    terminal_outcome: ProcedureTerminalOutcome | None = None
    max_attempts: int = Field(default=0, strict=True, ge=0, le=MAX_PROCEDURE_ITEMS)

    @model_validator(mode="after")
    def require_one_recovery_target_or_terminal_outcome(self) -> Self:
        if (self.target_step_id is None) == (self.terminal_outcome is None):
            raise ValueError("recovery requires exactly one step target or terminal outcome")
        if self.target_step_id is not None and self.max_attempts < 1:
            raise ValueError("step-target recovery requires a positive bounded attempt limit")
        if self.terminal_outcome is not None and self.max_attempts != 0:
            raise ValueError("terminal recovery must not declare retry attempts")
        return self


class _DeclaredProcedureArtifactPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    artifact_id: BoundedIdentifier
    media_type: BoundedText
    integrity_sha256: Sha256Hex

    @field_validator("media_type")
    @classmethod
    def normalize_media_type(cls, value: str) -> str:
        return value.lower()


class DeclaredProcedureArtifact(_DeclaredProcedureArtifactPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _DeclaredProcedureArtifactPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the declared artifact")
        return self


class _ProcedureStepPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    step_id: BoundedIdentifier
    order: int = Field(strict=True, ge=1, le=MAX_PROCEDURE_ITEMS)
    operation: ProcedureOperation
    objective: BoundedText
    input_artifact_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    outputs: tuple[DeclaredProcedureArtifact, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    dependency_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    allowed_tool_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    required_authorities: tuple[ProcedureAuthority, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    preconditions: tuple[BoundedText, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    completion_criteria: tuple[BoundedText, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    evidence_requirements: tuple[BoundedText, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    validator: ActorIdentity
    validator_version: BoundedIdentifier
    failure_signals: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    recovery: RecoveryDirective
    capability_requirement_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    progress_budget_category: ProgressBudgetCategory
    resource_budget: ResourceBudget
    progress_weight: Decimal = Field(strict=True, gt=Decimal("0"), le=Decimal("1"))


class ProcedureStep(_ProcedureStepPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ProcedureStepPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the procedure step")
        return self


class _CandidateMethodPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    method_id: BoundedIdentifier
    objective: BoundedText
    assumptions: tuple[BoundedText, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    stages: tuple[ProcedureStep, ...] = Field(min_length=1, max_length=MAX_PROCEDURE_ITEMS)
    evidence_refs: tuple[ArtifactRef, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    claimed_capability_requirement_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    expected_output_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    verifier_requirement_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    resource_estimate: ResourceBudget
    termination_conditions: tuple[BoundedText, ...] = Field(
        min_length=1, max_length=MAX_PROCEDURE_ITEMS
    )
    provenance_contribution_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )


class CandidateMethod(_CandidateMethodPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _CandidateMethodPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the candidate method")
        return self


class ArtifactCatalogEntry(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    artifact_id: BoundedIdentifier
    artifact: ArtifactRef | None
    availability: CatalogFactStatus

    @model_validator(mode="after")
    def require_availability_consistent_artifact(self) -> Self:
        if self.availability is CatalogFactStatus.PRESENT and self.artifact is None:
            raise ValueError("present artifact catalog entries require an artifact reference")
        if self.availability is not CatalogFactStatus.PRESENT and self.artifact is not None:
            raise ValueError("absent or unknown artifact entries must not invent a reference")
        return self


class RegisteredTool(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    tool: ActorIdentity
    availability: CatalogFactStatus
    authorization: CatalogFactStatus

    @model_validator(mode="after")
    def require_tool_identity(self) -> Self:
        if self.tool.kind is not ActorKind.TOOL:
            raise ValueError("registered tools require TOOL actor identities")
        if self.availability is CatalogFactStatus.ABSENT and (
            self.authorization is CatalogFactStatus.PRESENT
        ):
            raise ValueError("an absent tool cannot be authorized")
        return self


class RegisteredValidator(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    validator: ActorIdentity
    validator_version: BoundedIdentifier
    registration: CatalogFactStatus


class ProcedureCompilationRequest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    request_id: BoundedIdentifier
    compiler_id: BoundedIdentifier
    compiler_version: BoundedIdentifier
    candidate: CandidateMethod
    capability_assessments: tuple[CapabilityAssessment, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    artifact_catalog: tuple[ArtifactCatalogEntry, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    artifact_catalog_complete: bool
    tool_catalog: tuple[RegisteredTool, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    tool_catalog_complete: bool
    validator_catalog: tuple[RegisteredValidator, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    validator_catalog_complete: bool
    budget_envelope: BudgetReserves

    @field_validator("capability_assessments")
    @classmethod
    def require_canonical_capability_snapshot(
        cls,
        values: tuple[CapabilityAssessment, ...],
    ) -> tuple[CapabilityAssessment, ...]:
        keys = tuple(item.requirement.requirement_id for item in values)
        cls._require_canonical_snapshot_keys(keys, "capability_assessments")
        return values

    @field_validator("artifact_catalog")
    @classmethod
    def require_canonical_artifact_snapshot(
        cls,
        values: tuple[ArtifactCatalogEntry, ...],
    ) -> tuple[ArtifactCatalogEntry, ...]:
        keys = tuple(item.artifact_id for item in values)
        cls._require_canonical_snapshot_keys(keys, "artifact_catalog")
        return values

    @field_validator("tool_catalog")
    @classmethod
    def require_canonical_tool_snapshot(
        cls,
        values: tuple[RegisteredTool, ...],
    ) -> tuple[RegisteredTool, ...]:
        keys = tuple(item.tool.actor_id for item in values)
        cls._require_canonical_snapshot_keys(keys, "tool_catalog")
        return values

    @field_validator("validator_catalog")
    @classmethod
    def require_canonical_validator_snapshot(
        cls,
        values: tuple[RegisteredValidator, ...],
    ) -> tuple[RegisteredValidator, ...]:
        keys = tuple(
            (item.validator.actor_id, item.validator_version) for item in values
        )
        cls._require_canonical_snapshot_keys(keys, "validator_catalog")
        return values

    @staticmethod
    def _require_canonical_snapshot_keys(
        keys: tuple[str, ...] | tuple[tuple[str, str], ...],
        field_name: str,
    ) -> None:
        if len(set(keys)) != len(keys):
            raise ValueError(f"{field_name} must contain unique logical keys")
        if keys != tuple(sorted(keys)):
            raise ValueError(f"{field_name} must use canonical order")


class _ExecutableProcedurePayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    procedure_id: BoundedIdentifier
    compiler_id: BoundedIdentifier
    compiler_version: BoundedIdentifier
    steps: tuple[ProcedureStep, ...] = Field(min_length=1, max_length=MAX_PROCEDURE_ITEMS)
    required_capability_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    required_artifact_input_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    declared_outputs: tuple[DeclaredProcedureArtifact, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    source_candidate: CandidateMethod
    source_candidate_hash: Sha256Hex
    compilation_request_hash: Sha256Hex

    @model_validator(mode="after")
    def require_source_candidate_hash(self) -> Self:
        if self.source_candidate_hash != self.source_candidate.content_hash:
            raise ValueError("source candidate hash must match the retained candidate")
        return self


class ExecutableProcedure(_ExecutableProcedurePayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ExecutableProcedurePayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the executable procedure")
        return self


class ProcedureFinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    check_number: int = Field(strict=True, ge=1, le=16)
    code: ProcedureFindingCode
    severity: ProcedureFindingSeverity
    subject_id: BoundedIdentifier | None = None
    message: BoundedText


class ProcedureValidationReport(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    status: ProcedureValidationStatus
    findings: tuple[ProcedureFinding, ...] = Field(max_length=MAX_PROCEDURE_FINDINGS)
    checks_run: tuple[int, ...] = Field(min_length=16, max_length=16)

    @model_validator(mode="after")
    def require_complete_checks_and_consistent_status(self) -> Self:
        if self.checks_run != tuple(range(1, 17)):
            raise ValueError("procedure validation must run all sixteen checks in order")
        expected_findings = tuple(
            sorted(
                self.findings,
                key=lambda item: (item.check_number, item.subject_id or "", item.code.value),
            )
        )
        if self.findings != expected_findings:
            raise ValueError("procedure findings must use deterministic check order")
        if any(item.severity is ProcedureFindingSeverity.ERROR for item in self.findings):
            expected_status = ProcedureValidationStatus.INVALID
        elif any(item.severity is ProcedureFindingSeverity.UNKNOWN for item in self.findings):
            expected_status = ProcedureValidationStatus.INCONCLUSIVE
        else:
            expected_status = ProcedureValidationStatus.VALID
        if self.status is not expected_status:
            raise ValueError("procedure validation status must match its findings")
        return self


class _ProcedureCompilationResultPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    compiler_id: BoundedIdentifier
    compiler_version: BoundedIdentifier
    request_hash: Sha256Hex
    procedure: ExecutableProcedure
    report: ProcedureValidationReport

    @model_validator(mode="after")
    def require_procedure_identity_alignment(self) -> Self:
        if self.request_hash != self.procedure.compilation_request_hash:
            raise ValueError("compilation result request hash must match the procedure")
        if (
            self.compiler_id != self.procedure.compiler_id
            or self.compiler_version != self.procedure.compiler_version
        ):
            raise ValueError("compilation result compiler identity must match the procedure")
        return self


class ProcedureCompilationResult(_ProcedureCompilationResultPayload):
    result_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ProcedureCompilationResultPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            result_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_result_hash(self) -> Self:
        if self.result_hash != canonical_model_hash(self, exclude_fields={"result_hash"}):
            raise ValueError("result_hash must canonically address the compilation result")
        return self


class _ProcedureCompilationRecordPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    compilation_id: BoundedIdentifier
    result: ProcedureCompilationResult
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class ProcedureCompilationRecord(_ProcedureCompilationRecordPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ProcedureCompilationRecordPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the compilation record")
        return self


class ProcedureCompilationReceiptRef(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_type: Literal["PROCEDURE_COMPILATION"] = "PROCEDURE_COMPILATION"
    proposal_id: BoundedIdentifier
    proposal_hash: Sha256Hex
    audit_event_id: BoundedIdentifier
    audit_event_hash: Sha256Hex


class _CompiledProgressPlanBindingPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    binding_id: BoundedIdentifier
    compilation_receipt: ProcedureCompilationReceiptRef
    compilation_id: BoundedIdentifier
    compilation_hash: Sha256Hex
    procedure_id: BoundedIdentifier
    procedure_hash: Sha256Hex
    plan: ProgressPlan
    plan_hash: Sha256Hex
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_exact_plan_hash_and_policy(self) -> Self:
        if self.plan_hash != canonical_model_hash(self.plan):
            raise ValueError("plan hash must canonically address the bound progress plan")
        if self.plan.governing_policy_hash != self.governing_policy_hash:
            raise ValueError("binding and progress plan must share governing policy")
        return self


class CompiledProgressPlanBinding(_CompiledProgressPlanBindingPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _CompiledProgressPlanBindingPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the progress plan binding")
        return self


class MethodDirectionStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    ABANDONED = "ABANDONED"


class _MethodDirectionOutcomePayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    outcome_id: BoundedIdentifier
    status: MethodDirectionStatus
    evidence_refs: tuple[ArtifactRef, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    failed_method_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    rejected_procedure_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    budget_reference_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    terminal_rule: BoundedText
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator(
        "failed_method_ids",
        "rejected_procedure_ids",
        "budget_reference_ids",
    )
    @classmethod
    def require_canonical_identifiers(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, info.field_name)

    @model_validator(mode="after")
    def require_evidence_and_negative_history(self) -> Self:
        if self.status is MethodDirectionStatus.SUPPORTED and not self.evidence_refs:
            raise ValueError("supported method direction requires retained evidence")
        if self.status is MethodDirectionStatus.UNSUPPORTED and not (
            self.failed_method_ids or self.rejected_procedure_ids
        ):
            raise ValueError("unsupported method direction requires failed method history")
        return self


class MethodDirectionOutcome(_MethodDirectionOutcomePayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _MethodDirectionOutcomePayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the method direction outcome")
        return self


__all__ = [
    "ArtifactCatalogEntry",
    "CandidateMethod",
    "CatalogFactStatus",
    "CompiledProgressPlanBinding",
    "DeclaredProcedureArtifact",
    "ExecutableProcedure",
    "MethodDirectionOutcome",
    "MethodDirectionStatus",
    "ProcedureAuthority",
    "ProcedureCompilationReceiptRef",
    "ProcedureCompilationRecord",
    "ProcedureCompilationRequest",
    "ProcedureCompilationResult",
    "ProcedureFinding",
    "ProcedureFindingCode",
    "ProcedureFindingSeverity",
    "ProcedureOperation",
    "ProcedureStep",
    "ProcedureTerminalOutcome",
    "ProcedureValidationReport",
    "ProcedureValidationStatus",
    "ProgressBudgetCategory",
    "RecoveryDirective",
    "RegisteredTool",
    "RegisteredValidator",
    "canonical_model_hash",
]
