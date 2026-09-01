from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from types import UnionType
from typing import Annotated, Any, Literal, Self, Union, cast, get_args, get_origin

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from super_scientist.domain.cognition.models import CapabilityAssessment, CapabilityProfile
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
from super_scientist.domain.progress._decimal_math import _require_bounded_decimal
from super_scientist.domain.progress.models import BudgetReserves, ProgressPlan

MAX_PROCEDURE_ITEMS = 64
MAX_PROCEDURE_FINDINGS = 1_024
MAX_IDENTIFIER_LENGTH = 200
MAX_TEXT_LENGTH = 2_000
MAX_PROCEDURE_RESOURCE_VALUE = 1_000_000_000
# Keeps accepted requests practical to retain and deterministically recompile.
MAX_PROCEDURE_REQUEST_BYTES = 65_536
# Covers the retained request, derived procedure, and bounded validation findings.
MAX_PROCEDURE_RESULT_BYTES = 4 * 1_024 * 1_024
MAX_PROCEDURE_RESULT_BASE64_CHARACTERS = 4 * ((MAX_PROCEDURE_RESULT_BYTES + 2) // 3)
MAX_PROCEDURE_JSON_DEPTH = 128


class ProcedureBoundaryValidationError(ValueError):
    """Fixed safe failure for untrusted procedure parsing and validation."""


def _strip_text(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _reject_nul_identifier(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Phase A identifier must not contain NUL")
    return value


BoundedIdentifier = Annotated[
    StableIdentifier,
    Field(max_length=MAX_IDENTIFIER_LENGTH),
    AfterValidator(_reject_nul_identifier),
]
BoundedText = Annotated[
    str,
    BeforeValidator(_strip_text),
    Field(strict=True, min_length=1, max_length=MAX_TEXT_LENGTH),
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


def canonical_model_hash(
    model: BaseModel,
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    payload = model.model_dump(
        mode="json",
        exclude=exclude_fields or set(),
        warnings=False,
    )
    return sha256_hex(canonical_json_bytes(payload))


def catalog_snapshot_content_hash(
    catalog_kind: str,
    entries: tuple[BaseModel, ...],
    complete: bool,
) -> str:
    return sha256_hex(
        canonical_json_bytes(
            {
                "catalog_kind": catalog_kind,
                "entries": tuple(item.model_dump(mode="json", warnings=False) for item in entries),
                "complete": complete,
            }
        )
    )


def procedure_request_json_is_bounded(request_json: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for character in request_json:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_PROCEDURE_JSON_DEPTH:
                return False
        elif character in "]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string and not escaped


def _decode_opaque_result_json(
    result_json_base64: str,
    expected_hash: str,
) -> bytes:
    if len(result_json_base64) > MAX_PROCEDURE_RESULT_BASE64_CHARACTERS:
        raise ValueError("procedure compilation envelope exceeds canonical byte limit")
    try:
        encoded = result_json_base64.encode("ascii")
        result_json = base64.b64decode(encoded, validate=True)
    except (
        binascii.Error,
        MemoryError,
        OverflowError,
        RecursionError,
        UnicodeEncodeError,
        ValueError,
    ):
        raise ValueError("procedure compilation envelope requires canonical base64") from None
    if base64.b64encode(result_json) != encoded:
        raise ValueError("procedure compilation envelope requires canonical base64")
    if len(result_json) > MAX_PROCEDURE_RESULT_BYTES:
        raise ValueError("procedure compilation envelope exceeds canonical byte limit")
    if sha256_hex(result_json) != expected_hash:
        raise ValueError("procedure compilation envelope result JSON hash must match")
    try:
        result_json_text = result_json.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("procedure compilation envelope requires canonical JSON") from None
    if not procedure_request_json_is_bounded(result_json_text):
        raise ValueError("procedure compilation envelope exceeds JSON depth limit")
    try:
        decoded = json.loads(result_json_text)
        canonical_result_json = canonical_json_bytes(decoded)
    except (MemoryError, OverflowError, RecursionError, TypeError, ValueError):
        raise ValueError("procedure compilation envelope requires canonical JSON") from None
    if canonical_result_json != result_json:
        raise ValueError("procedure compilation envelope requires canonical JSON")
    return result_json


def _require_canonical_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must contain unique values")
    if values != tuple(sorted(values)):
        raise ValueError(f"{field_name} must be sorted in canonical order")
    return values


def _require_canonical_unique_enum(
    values: tuple[StrEnum, ...],
    field_name: str,
) -> tuple[StrEnum, ...]:
    raw_values = tuple(item.value for item in values)
    _require_canonical_unique(raw_values, field_name)
    return values


def _require_canonical_unique_artifacts(
    values: tuple[DeclaredProcedureArtifact, ...],
    field_name: str,
) -> tuple[DeclaredProcedureArtifact, ...]:
    artifact_ids = tuple(item.artifact_id for item in values)
    _require_canonical_unique(artifact_ids, field_name)
    return values


def _require_bounded_resource_budget(value: ResourceBudget, field_name: str) -> ResourceBudget:
    values = (
        value.cost_usd,
        value.compute_units,
        value.tokens,
        value.elapsed_seconds,
        value.tool_calls,
        value.human_interventions,
    )
    if any(item > MAX_PROCEDURE_RESOURCE_VALUE for item in values):
        raise ValueError(f"{field_name} values must be finitely bounded")
    return value


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


class ProcedureEvidenceSourceKind(StrEnum):
    CAPABILITY_PROFILE = "CAPABILITY_PROFILE"
    ARTIFACT_CATALOG = "ARTIFACT_CATALOG"
    TOOL_CATALOG = "TOOL_CATALOG"
    VALIDATOR_CATALOG = "VALIDATOR_CATALOG"


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
    SPOOFED_EVIDENCE_BINDING = "SPOOFED_EVIDENCE_BINDING"
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
            **payload.model_dump(mode="python", warnings=False),
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
    required_authorities: tuple[ProcedureAuthority, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
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
    progress_weight: Decimal = Field(
        strict=True,
        gt=Decimal("0"),
        le=Decimal("1"),
        allow_inf_nan=False,
    )

    @field_validator("progress_weight")
    @classmethod
    def require_bounded_progress_weight(cls, value: Decimal) -> Decimal:
        return _require_bounded_decimal(value)

    @field_validator(
        "input_artifact_ids",
        "dependency_ids",
        "allowed_tool_ids",
        "failure_signals",
        "capability_requirement_ids",
    )
    @classmethod
    def require_canonical_identifier_sets(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, info.field_name)

    @field_validator(
        "preconditions",
        "completion_criteria",
        "evidence_requirements",
    )
    @classmethod
    def require_canonical_text_sets(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, info.field_name)

    @field_validator("required_authorities")
    @classmethod
    def require_canonical_authorities(
        cls,
        values: tuple[ProcedureAuthority, ...],
    ) -> tuple[ProcedureAuthority, ...]:
        _require_canonical_unique_enum(values, "required_authorities")
        return values

    @field_validator("outputs")
    @classmethod
    def require_canonical_outputs(
        cls,
        values: tuple[DeclaredProcedureArtifact, ...],
    ) -> tuple[DeclaredProcedureArtifact, ...]:
        return _require_canonical_unique_artifacts(values, "outputs")

    @field_validator("resource_budget")
    @classmethod
    def require_bounded_resource_budget(cls, value: ResourceBudget) -> ResourceBudget:
        return _require_bounded_resource_budget(value, "resource_budget")


class ProcedureStep(_ProcedureStepPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ProcedureStepPayload(**values)
        return cls(
            **payload.model_dump(mode="python", warnings=False),
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
    verifier_requirement_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    resource_estimate: ResourceBudget
    termination_conditions: tuple[BoundedText, ...] = Field(
        min_length=1, max_length=MAX_PROCEDURE_ITEMS
    )
    provenance_contribution_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )

    @field_validator(
        "claimed_capability_requirement_ids",
        "expected_output_ids",
        "verifier_requirement_ids",
        "provenance_contribution_ids",
    )
    @classmethod
    def require_canonical_identifier_sets(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, info.field_name)

    @field_validator("resource_estimate")
    @classmethod
    def require_bounded_resource_estimate(cls, value: ResourceBudget) -> ResourceBudget:
        return _require_bounded_resource_budget(value, "resource_estimate")


class CandidateMethod(_CandidateMethodPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _CandidateMethodPayload(**values)
        return cls(
            **payload.model_dump(mode="python", warnings=False),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the candidate method")
        return self


class _AcceptedSourceReceiptPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: BoundedIdentifier
    source_kind: ProcedureEvidenceSourceKind
    source_record_id: BoundedIdentifier
    source_schema_version: int = Field(strict=True, ge=1, le=MAX_PROCEDURE_ITEMS)
    source_content_hash: Sha256Hex
    source_snapshot_id: BoundedIdentifier
    source_snapshot_hash: Sha256Hex
    proposal_id: BoundedIdentifier
    proposal_hash: Sha256Hex
    audit_event_id: BoundedIdentifier
    audit_event_hash: Sha256Hex


class AcceptedSourceReceiptRef(_AcceptedSourceReceiptPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _AcceptedSourceReceiptPayload(**values)
        return cls(
            **payload.model_dump(mode="python", warnings=False),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the accepted source receipt")
        return self


class _GroundedCapabilityAssessmentPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    profile: CapabilityProfile
    assessment: CapabilityAssessment
    profile_receipt: AcceptedSourceReceiptRef

    @model_validator(mode="after")
    def require_exact_profile_and_receipt_binding(self) -> Self:
        receipt = self.profile_receipt
        if (
            receipt.source_kind is not ProcedureEvidenceSourceKind.CAPABILITY_PROFILE
            or receipt.source_record_id != self.profile.profile_id
            or receipt.source_schema_version != self.profile.schema_version
            or receipt.source_content_hash != self.profile.content_hash
            or receipt.source_snapshot_hash != self.assessment.requirement.evidence_snapshot_hash
        ):
            raise ValueError(
                "capability evidence receipt must bind the retained profile and evidence snapshot"
            )
        from super_scientist.domain.cognition.grounding import assess_capability

        if self.assessment != assess_capability(
            self.profile,
            self.assessment.requirement,
        ):
            raise ValueError("capability assessment must be recomputed from the retained profile")
        return self


class GroundedCapabilityAssessment(_GroundedCapabilityAssessmentPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _GroundedCapabilityAssessmentPayload(**values)
        return cls(
            **payload.model_dump(mode="python", warnings=False),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError(
                "content_hash must canonically address the grounded capability assessment"
            )
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
    capability_assessments: tuple[GroundedCapabilityAssessment, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    artifact_catalog: tuple[ArtifactCatalogEntry, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    artifact_catalog_complete: bool
    artifact_catalog_receipt: AcceptedSourceReceiptRef
    tool_catalog: tuple[RegisteredTool, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    tool_catalog_complete: bool
    tool_catalog_receipt: AcceptedSourceReceiptRef
    validator_catalog: tuple[RegisteredValidator, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    validator_catalog_complete: bool
    validator_catalog_receipt: AcceptedSourceReceiptRef
    budget_envelope: BudgetReserves

    @field_validator("capability_assessments")
    @classmethod
    def require_canonical_capability_snapshot(
        cls,
        values: tuple[GroundedCapabilityAssessment, ...],
    ) -> tuple[GroundedCapabilityAssessment, ...]:
        keys = tuple(item.assessment.requirement.requirement_id for item in values)
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
        keys = tuple((item.validator.actor_id, item.validator_version) for item in values)
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

    @field_validator("budget_envelope")
    @classmethod
    def require_bounded_budget_envelope(cls, value: BudgetReserves) -> BudgetReserves:
        for field_name in ProgressBudgetCategory:
            _require_bounded_resource_budget(
                getattr(value, field_name.value),
                f"budget_envelope.{field_name.value}",
            )
        return value

    @model_validator(mode="after")
    def require_exact_fixed_catalog_snapshot_bindings(self) -> Self:
        bindings = (
            (
                ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
                self.artifact_catalog,
                self.artifact_catalog_complete,
                self.artifact_catalog_receipt,
            ),
            (
                ProcedureEvidenceSourceKind.TOOL_CATALOG,
                self.tool_catalog,
                self.tool_catalog_complete,
                self.tool_catalog_receipt,
            ),
            (
                ProcedureEvidenceSourceKind.VALIDATOR_CATALOG,
                self.validator_catalog,
                self.validator_catalog_complete,
                self.validator_catalog_receipt,
            ),
        )
        for source_kind, entries, complete, receipt in bindings:
            if (
                receipt.source_kind is not source_kind
                or receipt.source_schema_version != 1
                or receipt.source_content_hash
                != catalog_snapshot_content_hash(
                    source_kind.value,
                    entries,
                    complete,
                )
            ):
                raise ValueError(
                    f"{source_kind.value.lower()} receipt must bind exact catalog contents"
                )
        snapshots = {
            (receipt.source_snapshot_id, receipt.source_snapshot_hash)
            for _kind, _entries, _complete, receipt in bindings
        }
        if len(snapshots) != 1:
            raise ValueError("catalog receipts must share one fixed source snapshot")
        return self

    @model_validator(mode="after")
    def require_bounded_canonical_request(self) -> Self:
        request_bytes = canonical_json_bytes(self.model_dump(mode="json", warnings=False))
        if len(request_bytes) > MAX_PROCEDURE_REQUEST_BYTES:
            raise ValueError("procedure compilation request exceeds canonical byte limit")
        return self


class _ExecutableProcedurePayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    procedure_id: BoundedIdentifier
    compiler_id: BoundedIdentifier
    compiler_version: BoundedIdentifier
    steps: tuple[ProcedureStep, ...] = Field(min_length=1, max_length=MAX_PROCEDURE_ITEMS)
    required_capability_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    required_artifact_input_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_PROCEDURE_ITEMS
    )
    declared_outputs: tuple[DeclaredProcedureArtifact, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    source_candidate: CandidateMethod
    source_candidate_hash: Sha256Hex
    compilation_request_hash: Sha256Hex

    @field_validator("required_capability_ids", "required_artifact_input_ids")
    @classmethod
    def require_canonical_identifier_sets(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, info.field_name)

    @field_validator("declared_outputs")
    @classmethod
    def require_canonical_declared_outputs(
        cls,
        values: tuple[DeclaredProcedureArtifact, ...],
    ) -> tuple[DeclaredProcedureArtifact, ...]:
        return _require_canonical_unique_artifacts(values, "declared_outputs")

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
            **payload.model_dump(mode="python", warnings=False),
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
    request_json: str = Field(
        strict=True,
        min_length=2,
        max_length=MAX_PROCEDURE_REQUEST_BYTES,
    )
    procedure: ExecutableProcedure
    report: ProcedureValidationReport

    @model_validator(mode="after")
    def require_procedure_identity_alignment(self) -> Self:
        request_bytes = self.request_json.encode("utf-8")
        if len(request_bytes) > MAX_PROCEDURE_REQUEST_BYTES:
            raise ValueError("compilation result request exceeds canonical byte limit")
        request_json_is_valid = procedure_request_json_is_bounded(self.request_json)
        canonical_request = b""
        if request_json_is_valid:
            try:
                request_payload = json.loads(self.request_json)
                canonical_request = canonical_json_bytes(request_payload)
            except (MemoryError, OverflowError, RecursionError, TypeError, ValueError):
                request_json_is_valid = False
        if not request_json_is_valid:
            raise ValueError("compilation result must retain canonical request JSON") from None
        if request_bytes != canonical_request:
            raise ValueError("compilation result must retain canonical request JSON")
        if self.request_hash != sha256_hex(canonical_request):
            raise ValueError("compilation result request hash must match the retained request")
        if self.request_hash != self.procedure.compilation_request_hash:
            raise ValueError("compilation result request hash must match the procedure")
        if (
            self.compiler_id != self.procedure.compiler_id
            or self.compiler_version != self.procedure.compiler_version
        ):
            raise ValueError("compilation result compiler identity must match the procedure")
        return self

    def parse_request(self) -> ProcedureCompilationRequest:
        validated_result: ProcedureCompilationResult | None = None
        with suppress(MemoryError, OverflowError, RecursionError, TypeError, ValueError):
            validated_result = parse_untrusted_procedure_compilation_result(self)
        if validated_result is None:
            raise ProcedureBoundaryValidationError(
                "compilation result request failed validation"
            ) from None
        request_json = validated_result.request_json
        if not procedure_request_json_is_bounded(request_json):
            raise ProcedureBoundaryValidationError(
                "compilation result request failed validation"
            ) from None
        request: ProcedureCompilationRequest | None = None
        with suppress(MemoryError, OverflowError, RecursionError, TypeError, ValueError):
            request = ProcedureCompilationRequest.model_validate_json(
                request_json,
                strict=True,
            )
        if request is None:
            raise ProcedureBoundaryValidationError(
                "compilation result request failed validation"
            ) from None
        return request


class ProcedureCompilationResult(_ProcedureCompilationResultPayload):
    """Trusted internal result model; use the safe parser for untrusted input."""

    result_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ProcedureCompilationResultPayload(**values)
        return cls(
            **payload.model_dump(mode="python", warnings=False),
            result_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_result_hash(self) -> Self:
        if self.result_hash != canonical_model_hash(self, exclude_fields={"result_hash"}):
            raise ValueError("result_hash must canonically address the compilation result")
        return self


class OpaqueProcedureCompilationEnvelope(_StrictFrozenModel):
    """Bounded transport that deliberately does not parse its nested result schema."""

    schema_version: Literal[1] = 1
    compilation_id: BoundedIdentifier
    result_json_base64: str = Field(
        strict=True,
        min_length=4,
        max_length=MAX_PROCEDURE_RESULT_BASE64_CHARACTERS,
        repr=False,
    )
    result_json_hash: Sha256Hex
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("result_json_base64", mode="before")
    @classmethod
    def require_bounded_encoded_result(cls, value: object) -> object:
        if isinstance(value, str) and len(value) > MAX_PROCEDURE_RESULT_BASE64_CHARACTERS:
            raise ValueError("procedure compilation envelope exceeds canonical byte limit")
        return value

    @classmethod
    def build(
        cls,
        *,
        compilation_id: str,
        result: ProcedureCompilationResult,
        created_at: UtcTimestamp,
        governing_policy_hash: str,
    ) -> Self:
        result = parse_untrusted_procedure_compilation_result(result)
        result_json = canonical_json_bytes(result.model_dump(mode="json", warnings=False))
        return cls(
            compilation_id=compilation_id,
            result_json_base64=base64.b64encode(result_json).decode("ascii"),
            result_json_hash=sha256_hex(result_json),
            created_at=created_at,
            governing_policy_hash=governing_policy_hash,
        )

    @model_validator(mode="after")
    def require_bounded_canonical_result_json(self) -> Self:
        _decode_opaque_result_json(self.result_json_base64, self.result_json_hash)
        return self

    def result_json_bytes(self) -> bytes:
        result_json: bytes | None = None
        with suppress(MemoryError, OverflowError, RecursionError, TypeError, ValueError):
            result_json = _decode_opaque_result_json(
                self.result_json_base64,
                self.result_json_hash,
            )
        if result_json is None:
            raise ProcedureBoundaryValidationError(
                "procedure compilation envelope failed validation"
            ) from None
        return result_json


def parse_untrusted_procedure_compilation_envelope(
    value: object,
) -> OpaqueProcedureCompilationEnvelope:
    """Fresh-validate a complete envelope without retaining input diagnostics."""

    supplied_envelope = value if type(value) is OpaqueProcedureCompilationEnvelope else None
    supplied_payload: dict[str, object] | None = None
    envelope: OpaqueProcedureCompilationEnvelope | None = None
    with suppress(MemoryError, OverflowError, RecursionError, TypeError, ValueError):
        if supplied_envelope is not None or type(value) is dict:
            value = _trusted_exact_model_payload(value, OpaqueProcedureCompilationEnvelope)
            if supplied_envelope is not None:
                supplied_payload = value
        elif type(value) not in (str, bytes):
            raise TypeError("unsupported procedure compilation envelope input")
        if type(value) in (str, bytes):
            envelope = OpaqueProcedureCompilationEnvelope.model_validate_json(
                cast(str | bytes, value),
                strict=True,
            )
        else:
            envelope = OpaqueProcedureCompilationEnvelope.model_validate(
                value,
                strict=True,
            )
        if (
            supplied_payload is not None
            and _trusted_exact_model_payload(envelope, OpaqueProcedureCompilationEnvelope)
            != supplied_payload
        ):
            envelope = None
    if envelope is None:
        raise ProcedureBoundaryValidationError(
            "procedure compilation envelope failed validation"
        ) from None
    return envelope


def parse_untrusted_procedure_compilation_result(
    value: object,
) -> ProcedureCompilationResult:
    """Parse an untrusted result without exposing Pydantic input diagnostics."""

    supplied_result = value if type(value) is ProcedureCompilationResult else None
    supplied_payload: dict[str, object] | None = None
    result: ProcedureCompilationResult | None = None
    with suppress(MemoryError, OverflowError, RecursionError, TypeError, ValueError):
        if type(value) is OpaqueProcedureCompilationEnvelope:
            value = parse_untrusted_procedure_compilation_envelope(value).result_json_bytes()
        elif supplied_result is not None or type(value) is dict:
            value = _trusted_exact_model_payload(value, ProcedureCompilationResult)
            if supplied_result is not None:
                supplied_payload = value
        elif type(value) not in (str, bytes):
            raise TypeError("unsupported procedure compilation result input")
        if type(value) in (str, bytes):
            result = ProcedureCompilationResult.model_validate_json(
                cast(str | bytes, value), strict=True
            )
        else:
            result = ProcedureCompilationResult.model_validate(value, strict=True)
        if (
            supplied_payload is not None
            and _trusted_exact_model_payload(result, ProcedureCompilationResult) != supplied_payload
        ):
            result = None
    if result is None:
        raise ProcedureBoundaryValidationError(
            "procedure compilation result failed validation"
        ) from None
    return result


def _trusted_exact_model_payload[ModelT: BaseModel](
    value: object,
    model_type: type[ModelT],
) -> dict[str, object]:
    """Rebuild a bounded plain payload without consulting caller-owned hooks."""

    rebuilt = _rebuild_declared_procedure_value(
        value,
        model_type,
        depth=0,
        active_container_ids=set(),
        remaining_nodes=[100_000],
    )
    if type(rebuilt) is not dict:
        raise TypeError("trusted boundary reconstruction returned an invalid payload")
    return rebuilt


def _rebuild_declared_procedure_value(
    value: object,
    annotation: object,
    *,
    depth: int,
    active_container_ids: set[int],
    remaining_nodes: list[int],
) -> object:
    if depth > 64 or remaining_nodes[0] <= 0:
        raise TypeError("trusted boundary model graph exceeds reconstruction bounds")
    remaining_nodes[0] -= 1

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Annotated:
        return _rebuild_declared_procedure_value(
            value,
            arguments[0],
            depth=depth,
            active_container_ids=active_container_ids,
            remaining_nodes=remaining_nodes,
        )
    if origin is Literal:
        if not any(type(value) is type(expected) and value == expected for expected in arguments):
            raise TypeError("trusted boundary literal does not match declared schema")
        return value
    if origin in (Union, UnionType):
        if value is None and type(None) in arguments:
            return None
        non_none = tuple(candidate for candidate in arguments if candidate is not type(None))
        if len(non_none) != 1:
            raise TypeError("trusted boundary union is not an exact optional field")
        return _rebuild_declared_procedure_value(
            value,
            non_none[0],
            depth=depth,
            active_container_ids=active_container_ids,
            remaining_nodes=remaining_nodes,
        )
    if origin is tuple:
        if type(value) is not tuple or len(value) > remaining_nodes[0]:
            raise TypeError("trusted boundary tuple does not match declared schema")
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            item_annotations = (arguments[0],) * len(value)
        elif len(arguments) == len(value):
            item_annotations = arguments
        else:
            raise TypeError("trusted boundary tuple arity does not match declared schema")
        return _rebuild_declared_procedure_container(
            value,
            lambda: tuple(
                _rebuild_declared_procedure_value(
                    item,
                    item_annotation,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    remaining_nodes=remaining_nodes,
                )
                for item, item_annotation in zip(value, item_annotations, strict=True)
            ),
            active_container_ids,
        )
    if origin is dict:
        if type(value) is not dict or len(value) > remaining_nodes[0] or len(arguments) != 2:
            raise TypeError("trusted boundary mapping does not match declared schema")

        def rebuild_mapping() -> dict[object, object]:
            rebuilt_mapping: dict[object, object] = {}
            for key, item in dict.items(value):
                rebuilt_key = _rebuild_declared_procedure_value(
                    key,
                    arguments[0],
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    remaining_nodes=remaining_nodes,
                )
                rebuilt_item = _rebuild_declared_procedure_value(
                    item,
                    arguments[1],
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    remaining_nodes=remaining_nodes,
                )
                rebuilt_mapping[rebuilt_key] = rebuilt_item
            return rebuilt_mapping

        return _rebuild_declared_procedure_container(
            value,
            rebuild_mapping,
            active_container_ids,
        )
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _rebuild_declared_procedure_model(
            value,
            annotation,
            depth=depth,
            active_container_ids=active_container_ids,
            remaining_nodes=remaining_nodes,
        )
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not annotation or not any(value is member for member in annotation):
            raise TypeError("trusted boundary enum does not match declared schema")
        return value
    if annotation is datetime:
        if type(value) is not datetime or value.tzinfo is not UTC:
            raise TypeError("trusted boundary timestamp does not match declared schema")
        return value
    if annotation in (str, int, float, bool, Decimal, type(None)):
        if type(value) is not annotation:
            raise TypeError("trusted boundary primitive does not match declared schema")
        return value
    raise TypeError("trusted boundary annotation is outside the declared model graph")


def _rebuild_declared_procedure_model(
    value: object,
    model_type: type[BaseModel],
    *,
    depth: int,
    active_container_ids: set[int],
    remaining_nodes: list[int],
) -> dict[str, object]:
    if type(value) is model_type:
        state = object.__getattribute__(value, "__dict__")
        if (
            object.__getattribute__(value, "__pydantic_extra__") is not None
            or object.__getattribute__(value, "__pydantic_private__") is not None
        ):
            raise TypeError("trusted boundary model contains unexpected state")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        if type(fields_set) is not set:
            raise TypeError("trusted boundary model field state is invalid")
        supplied_field_names = tuple(fields_set)
        if any(type(field_name) is not str for field_name in supplied_field_names):
            raise TypeError("trusted boundary model field state is invalid")
    elif type(value) is dict:
        state = value
        supplied_field_names = ()
    else:
        raise TypeError("model type does not match trusted boundary type")
    if type(state) is not dict:
        raise TypeError("trusted boundary model state is invalid")
    fields = type.__getattribute__(model_type, "model_fields")
    if type(fields) is not dict:
        raise TypeError("trusted boundary model schema is invalid")
    state_keys = tuple(dict.keys(state))
    field_names = tuple(dict.keys(fields))
    if any(type(key) is not str for key in state_keys) or any(
        type(key) is not str for key in field_names
    ):
        raise TypeError("trusted boundary model state contains an invalid key")
    if len(state_keys) != len(field_names):
        raise TypeError("trusted boundary model state does not match declared schema")
    if any(not dict.__contains__(fields, key) for key in state_keys) or any(
        not dict.__contains__(state, field_name) for field_name in field_names
    ):
        raise TypeError("trusted boundary model state does not match declared schema")
    if any(not dict.__contains__(fields, field_name) for field_name in supplied_field_names):
        raise TypeError("trusted boundary model field state does not match declared schema")

    def rebuild_model() -> dict[str, object]:
        return {
            field_name: _rebuild_declared_procedure_value(
                dict.__getitem__(state, field_name),
                dict.__getitem__(fields, field_name).annotation,
                depth=depth + 1,
                active_container_ids=active_container_ids,
                remaining_nodes=remaining_nodes,
            )
            for field_name in field_names
        }

    return _rebuild_declared_procedure_container(
        value,
        rebuild_model,
        active_container_ids,
    )


def _rebuild_declared_procedure_container[ValueT](
    value: object,
    rebuild: Callable[[], ValueT],
    active_container_ids: set[int],
) -> ValueT:
    container_id = id(value)
    if container_id in active_container_ids:
        raise TypeError("trusted boundary model graph contains a cycle")
    active_container_ids.add(container_id)
    try:
        return rebuild()
    finally:
        active_container_ids.remove(container_id)


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
            **payload.model_dump(mode="python", warnings=False),
            content_hash=canonical_model_hash(payload),
        )

    @classmethod
    def build_from_untrusted_envelope(
        cls,
        envelope: OpaqueProcedureCompilationEnvelope,
    ) -> Self:
        """Normalize an opaque proposal envelope through the safe result parser."""

        envelope = parse_untrusted_procedure_compilation_envelope(envelope)
        return cls.build(
            compilation_id=envelope.compilation_id,
            result=parse_untrusted_procedure_compilation_result(envelope),
            created_at=envelope.created_at,
            governing_policy_hash=envelope.governing_policy_hash,
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
            **payload.model_dump(mode="python", warnings=False),
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
    rejected_procedure_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
    budget_reference_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PROCEDURE_ITEMS)
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
            **payload.model_dump(mode="python", warnings=False),
            content_hash=canonical_model_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != canonical_model_hash(self, exclude_fields={"content_hash"}):
            raise ValueError("content_hash must canonically address the method direction outcome")
        return self


__all__ = [
    "MAX_PROCEDURE_REQUEST_BYTES",
    "MAX_PROCEDURE_RESOURCE_VALUE",
    "MAX_PROCEDURE_RESULT_BYTES",
    "AcceptedSourceReceiptRef",
    "ArtifactCatalogEntry",
    "CandidateMethod",
    "CatalogFactStatus",
    "CompiledProgressPlanBinding",
    "DeclaredProcedureArtifact",
    "ExecutableProcedure",
    "GroundedCapabilityAssessment",
    "MethodDirectionOutcome",
    "MethodDirectionStatus",
    "OpaqueProcedureCompilationEnvelope",
    "ProcedureAuthority",
    "ProcedureBoundaryValidationError",
    "ProcedureCompilationReceiptRef",
    "ProcedureCompilationRecord",
    "ProcedureCompilationRequest",
    "ProcedureCompilationResult",
    "ProcedureEvidenceSourceKind",
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
    "catalog_snapshot_content_hash",
    "parse_untrusted_procedure_compilation_envelope",
    "parse_untrusted_procedure_compilation_result",
]
