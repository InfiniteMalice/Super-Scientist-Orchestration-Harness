from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import to_jsonable_python

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.harness_eval.guidance import (
    GuidanceCondition,
    GuidanceEvaluationProtocol,
)
from super_scientist.domain.harness_eval.matrix import (
    HarnessIdentity,
    ModelHarnessCoordinate,
    ModelHarnessProtocol,
    ModelIdentity,
)
from super_scientist.domain.harness_eval.models import HarnessPartition
from super_scientist.domain.improvement.models import ResourceUsage
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)

MAX_TRACE_ITEMS = 256
MAX_TRACE_IDENTIFIER_LENGTH = 200
MAX_CATEGORICAL_REWARD_LENGTH = 200

BoundedTraceIdentifier = Annotated[
    StableIdentifier,
    Field(
        max_length=MAX_TRACE_IDENTIFIER_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]
BoundedCategoricalReward = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=MAX_CATEGORICAL_REWARD_LENGTH),
]

class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


def _canonical_record_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    if isinstance(record, BaseModel):
        payload = record.model_dump(mode="json")
    else:
        payload = to_jsonable_python(dict(record))
    for field_name in {"content_hash", *(exclude_fields or set())}:
        payload.pop(field_name, None)
    return sha256_hex(canonical_json_bytes(payload))


def _canonical_identifier_tuple(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)) or values != tuple(sorted(values)):
        raise ValueError(f"{field_name} must be unique and canonically ordered")
    return values


class MetadataAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AvailableValue[ValueT](_StrictFrozenModel):
    schema_version: Literal[1] = 1
    status: MetadataAvailability
    value: ValueT | None
    evidence_id: BoundedTraceIdentifier | None

    @model_validator(mode="after")
    def require_truthful_availability(self) -> Self:
        has_value = self.value is not None
        has_evidence = self.evidence_id is not None
        if has_value != has_evidence:
            raise ValueError("metadata value and evidence must be present together")
        if (self.status is MetadataAvailability.AVAILABLE) != has_value:
            raise ValueError("metadata value and evidence require AVAILABLE status")
        return self


class ContextTransformationKind(StrEnum):
    INPUT_FILTERING = "INPUT_FILTERING"
    CONTEXT_COMPACTION = "CONTEXT_COMPACTION"
    RESERIALIZATION = "RESERIALIZATION"


class ToolObservationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


class EnvironmentEventKind(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    CRASHED = "CRASHED"
    ARTIFACT_CORRUPTION_DETECTED = "ARTIFACT_CORRUPTION_DETECTED"
    PROTECTED_BOUNDARY_CROSSED = "PROTECTED_BOUNDARY_CROSSED"
    EVALUATOR_FAILED = "EVALUATOR_FAILED"


class ExecutionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    INCOMPLETE = "INCOMPLETE"
    CRASHED = "CRASHED"


class CaptureRewardValidityStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    INCONCLUSIVE = "INCONCLUSIVE"


class GenerationStopReason(StrEnum):
    COMPLETED = "COMPLETED"
    LENGTH_LIMIT = "LENGTH_LIMIT"
    TOOL_REQUEST = "TOOL_REQUEST"
    CONTENT_FILTER = "CONTENT_FILTER"
    UNKNOWN = "UNKNOWN"


class TraceFreshnessStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class TraceBindingMismatch(StrEnum):
    PROTOCOL = "PROTOCOL"
    TASK = "TASK"
    MODEL = "MODEL"
    HARNESS = "HARNESS"
    PROCEDURE = "PROCEDURE"
    ENVIRONMENT = "ENVIRONMENT"
    CONTEXT = "CONTEXT"
    VALIDATOR = "VALIDATOR"
    ARTIFACTS = "ARTIFACTS"
    OUTPUT_SCHEMA = "OUTPUT_SCHEMA"


_FRESHNESS_ORDER = {item: index for index, item in enumerate(TraceBindingMismatch)}


class _ObservableArtifactRefPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    artifact_id: BoundedTraceIdentifier
    sha256: Sha256Hex
    size_bytes: int = Field(strict=True, ge=0)
    media_type: Annotated[str, Field(strict=True, min_length=1, max_length=200)]

    @field_validator("media_type")
    @classmethod
    def normalize_media_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("media_type must be nonblank")
        return normalized


class ObservableArtifactRef(_ObservableArtifactRefPayload):
    """Content-addressed artifact metadata with no reversible storage locator."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ObservableArtifactRefPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=observable_artifact_hash(payload),
        )

    @classmethod
    def from_artifact_ref(cls, artifact_id: str, artifact: ArtifactRef) -> Self:
        validated = ArtifactRef.model_validate(artifact)
        return cls.build(
            artifact_id=artifact_id,
            sha256=validated.sha256,
            size_bytes=validated.size_bytes,
            media_type=validated.media_type,
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != observable_artifact_hash(self):
            raise ValueError("content_hash must canonically address the observable artifact")
        return self


def observable_artifact_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


def artifact_collection_hash(artifacts: tuple[ObservableArtifactRef, ...]) -> str:
    validated = tuple(ObservableArtifactRef.model_validate(item) for item in artifacts)
    return _canonical_record_hash(
        {
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "sha256": item.sha256,
                    "content_hash": item.content_hash,
                }
                for item in validated
            ]
        }
    )


class _ContextTransformationPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    sequence: int = Field(strict=True, ge=0)
    kind: ContextTransformationKind
    input_context_hash: Sha256Hex
    output_context_hash: Sha256Hex
    evidence_id: BoundedTraceIdentifier


class ContextTransformation(_ContextTransformationPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ContextTransformationPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=context_transformation_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != context_transformation_hash(self):
            raise ValueError("content_hash must canonically address the context transformation")
        return self


def context_transformation_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _ToolObservationPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    sequence: int = Field(strict=True, ge=0)
    tool_id: BoundedTraceIdentifier
    tool_version: BoundedTraceIdentifier
    request_hash: Sha256Hex
    response_hash: AvailableValue[Sha256Hex]
    status: ToolObservationStatus
    evidence_id: BoundedTraceIdentifier

    @model_validator(mode="after")
    def require_status_consistent_response(self) -> Self:
        if (
            self.status is ToolObservationStatus.SUCCEEDED
            and self.response_hash.status is not MetadataAvailability.AVAILABLE
        ):
            raise ValueError("successful tool observation requires response hash evidence")
        if (
            self.response_hash.status is MetadataAvailability.AVAILABLE
            and self.response_hash.evidence_id != self.evidence_id
        ):
            raise ValueError("response hash evidence must match the tool observation")
        if (
            self.status is ToolObservationStatus.NOT_RUN
            and self.response_hash.status is MetadataAvailability.AVAILABLE
        ):
            raise ValueError("a tool that was not run cannot have a response hash")
        return self


class ToolObservation(_ToolObservationPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ToolObservationPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=tool_observation_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != tool_observation_hash(self):
            raise ValueError("content_hash must canonically address the tool observation")
        return self


def tool_observation_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _EnvironmentEventPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    sequence: int = Field(strict=True, ge=0)
    environment_id: BoundedTraceIdentifier
    environment_version: BoundedTraceIdentifier
    kind: EnvironmentEventKind
    evidence_id: BoundedTraceIdentifier


class EnvironmentEvent(_EnvironmentEventPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _EnvironmentEventPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=environment_event_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != environment_event_hash(self):
            raise ValueError("content_hash must canonically address the environment event")
        return self


def environment_event_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _GenerationMetadataPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    token_ids: AvailableValue[tuple[int, ...]]
    token_count: AvailableValue[int]
    log_probabilities: AvailableValue[tuple[Decimal, ...]]
    sampling_parameters_hash: AvailableValue[Sha256Hex]
    stop_reason: AvailableValue[GenerationStopReason]
    provider_request_id: AvailableValue[BoundedTraceIdentifier]

    @model_validator(mode="after")
    def require_consistent_generation_evidence(self) -> Self:
        if self.token_count.value is not None and self.token_count.value < 0:
            raise ValueError("token count must be a non-negative integer")
        if self.token_ids.value is not None:
            if len(self.token_ids.value) > MAX_TRACE_ITEMS or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 0
                for item in self.token_ids.value
            ):
                raise ValueError("token IDs must be bounded non-negative integers")
            if self.token_count.value != len(self.token_ids.value):
                raise ValueError("available token IDs must match the observed token count")
        if self.log_probabilities.value is not None:
            if any(not item.is_finite() for item in self.log_probabilities.value):
                raise ValueError("log probabilities must be finite")
            if (
                self.token_ids.value is None
                or len(self.log_probabilities.value) != len(self.token_ids.value)
            ):
                raise ValueError("log probabilities require aligned observed token IDs")
        return self


class GenerationMetadata(_GenerationMetadataPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _GenerationMetadataPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=generation_metadata_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != generation_metadata_hash(self):
            raise ValueError("content_hash must canonically address generation metadata")
        return self


def generation_metadata_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _RewardObservationPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    observation_id: BoundedTraceIdentifier
    task_id: BoundedTraceIdentifier
    task_input_hash: Sha256Hex
    verifier_id: BoundedTraceIdentifier
    verifier_version: BoundedTraceIdentifier
    checker_id: BoundedTraceIdentifier
    checker_version: BoundedTraceIdentifier
    checker_result_id: BoundedTraceIdentifier
    checker_result_hash: Sha256Hex
    evaluator_id: BoundedTraceIdentifier
    evaluator_version: BoundedTraceIdentifier
    value: Decimal | BoundedCategoricalReward | None
    evidence_id: BoundedTraceIdentifier | None
    observed_at: UtcTimestamp

    @field_validator("value")
    @classmethod
    def require_finite_numeric_reward(
        cls,
        value: Decimal | str | None,
    ) -> Decimal | str | None:
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("numeric reward must be finite")
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("categorical reward must be nonblank")
            return normalized
        return value

    @model_validator(mode="after")
    def require_reward_evidence(self) -> Self:
        if (self.value is None) != (self.evidence_id is None):
            raise ValueError("reward value and evidence must be present together")
        return self


class RewardObservation(_RewardObservationPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _RewardObservationPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=reward_observation_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != reward_observation_hash(self):
            raise ValueError("content_hash must canonically address the reward observation")
        return self


def reward_observation_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _TraceBindingPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: BoundedTraceIdentifier
    protocol_version: int = Field(strict=True, ge=1)
    protocol_hash: Sha256Hex
    guidance_protocol: GuidanceEvaluationProtocol | None
    model_harness_protocol: ModelHarnessProtocol | None
    guidance_condition: GuidanceCondition | None
    task_id: BoundedTraceIdentifier
    task_input_hash: Sha256Hex
    partition: HarnessPartition | None
    model: ModelIdentity
    model_hash: Sha256Hex
    harness: HarnessIdentity
    harness_hash: Sha256Hex
    procedure_id: BoundedTraceIdentifier
    procedure_version: BoundedTraceIdentifier
    procedure_hash: Sha256Hex
    environment_id: BoundedTraceIdentifier
    environment_version: BoundedTraceIdentifier
    environment_hash: Sha256Hex
    context_hash: Sha256Hex
    validator_id: BoundedTraceIdentifier
    validator_version: BoundedTraceIdentifier
    validator_hash: Sha256Hex
    checker_id: BoundedTraceIdentifier
    checker_version: BoundedTraceIdentifier
    checker_hash: Sha256Hex
    authorized_artifact_ids: tuple[BoundedTraceIdentifier, ...] = Field(
        max_length=MAX_TRACE_ITEMS
    )
    artifact_ids: tuple[BoundedTraceIdentifier, ...] = Field(max_length=MAX_TRACE_ITEMS)
    artifact_hashes: tuple[Sha256Hex, ...] = Field(max_length=MAX_TRACE_ITEMS)
    output_schema_hash: Sha256Hex

    @field_validator("authorized_artifact_ids", "artifact_ids")
    @classmethod
    def require_canonical_artifact_ids(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _canonical_identifier_tuple(values, info.field_name)

    @model_validator(mode="after")
    def require_exact_protocol_artifact_authorization(self) -> Self:
        if len(self.artifact_ids) != len(self.artifact_hashes):
            raise ValueError("artifact identities and hashes must be exactly aligned")
        if self.guidance_protocol is not None:
            if self.model_harness_protocol is not None or self.guidance_condition is None:
                raise ValueError("guidance trace binding requires one exact guidance protocol")
            guidance_protocol = self.guidance_protocol
            authorized = guidance_protocol.artifact_ids
            if self.guidance_condition is GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS:
                authorized = tuple(
                    sorted(
                        (*authorized, *guidance_protocol.declared_distractor_artifact_ids)
                    )
                )
            if self.authorized_artifact_ids != authorized:
                raise ValueError("trace binding must declare exact authorized guidance artifacts")
            guidance_fields_match = (
                self.protocol_id == guidance_protocol.protocol_id,
                self.protocol_version == guidance_protocol.version,
                self.protocol_hash == guidance_protocol.content_hash,
                self.task_id == guidance_protocol.task_id,
                self.task_input_hash == guidance_protocol.task_input_hash,
                self.partition is None,
                self.model
                == ModelIdentity(
                    model_id=guidance_protocol.model_id,
                    model_version=guidance_protocol.model_version,
                ),
                self.harness
                == HarnessIdentity(
                    harness_id=guidance_protocol.harness_id,
                    harness_version=guidance_protocol.harness_version,
                ),
                self.validator_id == guidance_protocol.verifier_id,
                self.validator_version == guidance_protocol.verifier_version,
                self.checker_id == guidance_protocol.checker_id,
                self.checker_version == guidance_protocol.checker_version,
                self.output_schema_hash == guidance_protocol.output_schema_hash,
            )
            if not all(guidance_fields_match):
                raise ValueError("trace binding must derive exact guidance protocol fields")
        elif self.model_harness_protocol is not None:
            if self.guidance_condition is not None:
                raise ValueError("matrix trace binding cannot declare a guidance condition")
            matrix_protocol = self.model_harness_protocol
            if self.authorized_artifact_ids != matrix_protocol.artifact_ids:
                raise ValueError("trace binding must declare exact authorized matrix artifacts")
            if self.partition is None:
                raise ValueError("matrix trace binding requires an exact partition")
            coordinate = ModelHarnessCoordinate(
                model=self.model,
                harness=self.harness,
                partition=self.partition,
            )
            matrix_fields_match = (
                coordinate in matrix_protocol.expected_grid,
                self.protocol_id == matrix_protocol.protocol_id,
                self.protocol_version == matrix_protocol.version,
                self.protocol_hash == matrix_protocol.content_hash,
                self.task_id == matrix_protocol.task_set_id,
                self.task_input_hash == matrix_protocol.task_set_hash,
                self.validator_id == matrix_protocol.verifier_id,
                self.validator_version == matrix_protocol.verifier_version,
                self.checker_id == matrix_protocol.checker_id,
                self.checker_version == matrix_protocol.checker_version,
                self.output_schema_hash == matrix_protocol.output_schema_hash,
            )
            if not all(matrix_fields_match):
                raise ValueError("trace binding must derive exact matrix protocol fields")
        elif self.guidance_condition is not None:
            raise ValueError("guidance condition requires an exact guidance protocol")
        return self


class TraceBinding(_TraceBindingPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _TraceBindingPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=trace_binding_hash(payload),
        )

    @classmethod
    def from_guidance_protocol(
        cls,
        protocol: GuidanceEvaluationProtocol,
        *,
        condition: GuidanceCondition,
        artifacts: tuple[ObservableArtifactRef, ...],
        **values: Any,
    ) -> Self:
        validated = GuidanceEvaluationProtocol.model_validate(protocol)
        validated_condition = GuidanceCondition(condition)
        validated_artifacts = tuple(
            ObservableArtifactRef.model_validate(item) for item in artifacts
        )
        artifact_ids = tuple(item.artifact_id for item in validated_artifacts)
        authorized_artifact_ids = validated.artifact_ids
        if validated_condition is GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS:
            authorized_artifact_ids = tuple(
                sorted(
                    (
                        *authorized_artifact_ids,
                        *validated.declared_distractor_artifact_ids,
                    )
                )
            )
        if artifact_ids != authorized_artifact_ids:
            raise ValueError("artifacts must match the exact authorized guidance artifacts")
        supplied = dict(values)
        derived_fields: dict[str, object] = {
            "protocol_id": validated.protocol_id,
            "protocol_version": validated.version,
            "protocol_hash": validated.content_hash,
            "guidance_protocol": validated,
            "model_harness_protocol": None,
            "guidance_condition": validated_condition,
            "task_id": validated.task_id,
            "task_input_hash": validated.task_input_hash,
            "partition": None,
            "model": ModelIdentity(
                model_id=validated.model_id,
                model_version=validated.model_version,
            ),
            "harness": HarnessIdentity(
                harness_id=validated.harness_id,
                harness_version=validated.harness_version,
            ),
            "validator_id": validated.verifier_id,
            "validator_version": validated.verifier_version,
            "checker_id": validated.checker_id,
            "checker_version": validated.checker_version,
            "authorized_artifact_ids": authorized_artifact_ids,
            "artifact_ids": artifact_ids,
            "artifact_hashes": tuple(item.sha256 for item in validated_artifacts),
            "output_schema_hash": validated.output_schema_hash,
        }
        if set(supplied) & set(derived_fields):
            raise TypeError("protocol-derived trace binding fields cannot be overridden")
        return cls.build(**derived_fields, **supplied)

    @classmethod
    def from_model_harness_protocol(
        cls,
        protocol: ModelHarnessProtocol,
        coordinate: ModelHarnessCoordinate,
        *,
        artifacts: tuple[ObservableArtifactRef, ...],
        **values: Any,
    ) -> Self:
        validated = ModelHarnessProtocol.model_validate(protocol)
        selected = ModelHarnessCoordinate.model_validate(coordinate)
        if selected not in validated.expected_grid:
            raise ValueError("matrix trace coordinate must belong to the exact protocol grid")
        validated_artifacts = tuple(
            ObservableArtifactRef.model_validate(item) for item in artifacts
        )
        artifact_ids = tuple(item.artifact_id for item in validated_artifacts)
        if artifact_ids != validated.artifact_ids:
            raise ValueError("artifacts must match the exact authorized matrix artifacts")
        supplied = dict(values)
        derived_fields: dict[str, object] = {
            "protocol_id": validated.protocol_id,
            "protocol_version": validated.version,
            "protocol_hash": validated.content_hash,
            "guidance_protocol": None,
            "model_harness_protocol": validated,
            "guidance_condition": None,
            "task_id": validated.task_set_id,
            "task_input_hash": validated.task_set_hash,
            "partition": selected.partition,
            "model": selected.model,
            "harness": selected.harness,
            "validator_id": validated.verifier_id,
            "validator_version": validated.verifier_version,
            "checker_id": validated.checker_id,
            "checker_version": validated.checker_version,
            "authorized_artifact_ids": validated.artifact_ids,
            "artifact_ids": artifact_ids,
            "artifact_hashes": tuple(item.sha256 for item in validated_artifacts),
            "output_schema_hash": validated.output_schema_hash,
        }
        if set(supplied) & set(derived_fields):
            raise TypeError("protocol-derived trace binding fields cannot be overridden")
        return cls.build(**derived_fields, **supplied)

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != trace_binding_hash(self):
            raise ValueError("content_hash must canonically address the trace binding")
        return self


def trace_binding_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _TraceFreshnessPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    trace_id: BoundedTraceIdentifier
    trace_hash: Sha256Hex
    expected_binding_hash: Sha256Hex
    observed_binding_hash: Sha256Hex
    status: TraceFreshnessStatus
    mismatches: tuple[TraceBindingMismatch, ...] = Field(max_length=len(TraceBindingMismatch))

    @field_validator("mismatches")
    @classmethod
    def require_canonical_mismatches(
        cls,
        values: tuple[TraceBindingMismatch, ...],
    ) -> tuple[TraceBindingMismatch, ...]:
        if len(values) != len(set(values)) or values != tuple(
            sorted(values, key=_FRESHNESS_ORDER.__getitem__)
        ):
            raise ValueError("freshness mismatches must be unique and canonically ordered")
        return values

    @model_validator(mode="after")
    def require_exact_status(self) -> Self:
        if (self.status is TraceFreshnessStatus.CURRENT) != (not self.mismatches):
            raise ValueError("trace freshness status must exactly match hash mismatches")
        return self


class TraceFreshness(_TraceFreshnessPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _TraceFreshnessPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=trace_freshness_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != trace_freshness_hash(self):
            raise ValueError("content_hash must canonically address trace freshness")
        return self


def trace_freshness_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _HarnessExecutionTracePayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    trace_id: BoundedTraceIdentifier
    expected_binding: TraceBinding
    observed_binding: TraceBinding
    context_artifacts: tuple[ObservableArtifactRef, ...] = Field(max_length=MAX_TRACE_ITEMS)
    initial_context_hash: Sha256Hex
    context_transformations: tuple[ContextTransformation, ...] = Field(
        max_length=MAX_TRACE_ITEMS
    )
    context_transformations_hash: Sha256Hex
    final_context_hash: Sha256Hex
    tool_observations: tuple[ToolObservation, ...] = Field(max_length=MAX_TRACE_ITEMS)
    tool_observations_hash: Sha256Hex
    environment_events: tuple[EnvironmentEvent, ...] = Field(
        min_length=1,
        max_length=MAX_TRACE_ITEMS,
    )
    environment_events_hash: Sha256Hex
    output_artifacts: tuple[ObservableArtifactRef, ...] = Field(
        min_length=1,
        max_length=MAX_TRACE_ITEMS,
    )
    output_artifacts_hash: Sha256Hex
    output_hash: Sha256Hex
    checker_result_id: BoundedTraceIdentifier
    checker_result_hash: Sha256Hex
    reward_observation: RewardObservation | None
    reward_observation_hash: AvailableValue[Sha256Hex]
    capture_reward_validity: AvailableValue[CaptureRewardValidityStatus]
    generation_metadata: GenerationMetadata
    resource_usage: ResourceUsage
    resource_usage_hash: Sha256Hex
    execution_status: ExecutionStatus
    artifact_integrity: AvailableValue[bool]
    protected_boundary_crossed: AvailableValue[bool]
    evaluator_succeeded: AvailableValue[bool]
    provenance_evidence_ids: tuple[BoundedTraceIdentifier, ...] = Field(
        min_length=1,
        max_length=MAX_TRACE_ITEMS,
    )
    provenance_hash: Sha256Hex
    observed_at: UtcTimestamp

    @field_validator("context_artifacts", "output_artifacts")
    @classmethod
    def require_canonical_artifacts(
        cls,
        values: tuple[ObservableArtifactRef, ...],
    ) -> tuple[ObservableArtifactRef, ...]:
        keys = tuple((item.artifact_id, item.content_hash) for item in values)
        artifact_ids = tuple(item.artifact_id for item in values)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("trace artifact identifiers must be unique")
        if keys != tuple(sorted(keys)):
            raise ValueError("trace artifacts must be canonically ordered")
        return values

    @field_validator("provenance_evidence_ids")
    @classmethod
    def require_canonical_provenance(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_identifier_tuple(values, "provenance_evidence_ids")

    @model_validator(mode="after")
    def require_exact_observable_state(self) -> Self:
        if (
            self.expected_binding.artifact_ids
            != self.expected_binding.authorized_artifact_ids
        ):
            raise ValueError(
                "expected binding must use exact authorized artifact identities"
            )
        if self.initial_context_hash != artifact_collection_hash(self.context_artifacts):
            raise ValueError("initial context hash must address declared context artifacts")
        expected_sequence = tuple(range(len(self.context_transformations)))
        if tuple(item.sequence for item in self.context_transformations) != expected_sequence:
            raise ValueError("context transformations must have contiguous sequence numbers")
        current_hash = self.initial_context_hash
        for transformation in self.context_transformations:
            if transformation.input_context_hash != current_hash:
                raise ValueError("context transformation chain must be contiguous")
            current_hash = transformation.output_context_hash
        if self.final_context_hash != current_hash:
            raise ValueError("final context hash must match the transformation chain")
        if self.observed_binding.context_hash != self.final_context_hash:
            raise ValueError("observed binding must use the exact final context hash")
        context_ids = tuple(item.artifact_id for item in self.context_artifacts)
        context_hashes = tuple(item.sha256 for item in self.context_artifacts)
        if (
            self.observed_binding.artifact_ids,
            self.observed_binding.artifact_hashes,
        ) != (context_ids, context_hashes):
            raise ValueError(
                "observed binding must use exact context artifact identities and hashes"
            )
        if self.context_transformations_hash != _canonical_record_hash(
            {"transformations": [item.content_hash for item in self.context_transformations]}
        ):
            raise ValueError("context transformations hash mismatch")
        if tuple(item.sequence for item in self.tool_observations) != tuple(
            range(len(self.tool_observations))
        ):
            raise ValueError("tool observations must have contiguous sequence numbers")
        if self.tool_observations_hash != _canonical_record_hash(
            {"observations": [item.content_hash for item in self.tool_observations]}
        ):
            raise ValueError("tool observations hash mismatch")
        if tuple(item.sequence for item in self.environment_events) != tuple(
            range(len(self.environment_events))
        ):
            raise ValueError("environment events must have contiguous sequence numbers")
        if any(
            item.environment_id != self.observed_binding.environment_id
            or item.environment_version != self.observed_binding.environment_version
            for item in self.environment_events
        ):
            raise ValueError("environment events must bind the observed environment identity")
        if self.environment_events_hash != _canonical_record_hash(
            {"events": [item.content_hash for item in self.environment_events]}
        ):
            raise ValueError("environment events hash mismatch")
        kinds = tuple(item.kind for item in self.environment_events)
        if (
            not kinds
            or kinds[0] is not EnvironmentEventKind.STARTED
            or kinds.count(EnvironmentEventKind.STARTED) != 1
        ):
            raise ValueError("environment history must start with STARTED")
        terminal_count = kinds.count(EnvironmentEventKind.COMPLETED) + kinds.count(
            EnvironmentEventKind.CRASHED
        )
        expected_terminal_count = 0 if self.execution_status is ExecutionStatus.INCOMPLETE else 1
        if terminal_count != expected_terminal_count:
            raise ValueError("environment history requires exactly one terminal event")
        if self.execution_status is ExecutionStatus.COMPLETED and (
            kinds[-1] is not EnvironmentEventKind.COMPLETED
            or EnvironmentEventKind.CRASHED in kinds
        ):
            raise ValueError("completed execution cannot contain a crash event")
        if self.execution_status is ExecutionStatus.CRASHED and (
            kinds[-1] is not EnvironmentEventKind.CRASHED
            or EnvironmentEventKind.COMPLETED in kinds
        ):
            raise ValueError("crashed execution requires a terminal crash event")
        if self.execution_status is ExecutionStatus.INCOMPLETE and (
            EnvironmentEventKind.COMPLETED in kinds or EnvironmentEventKind.CRASHED in kinds
        ):
            raise ValueError("incomplete execution cannot claim a terminal event")
        event_boolean_pairs = (
            (
                EnvironmentEventKind.ARTIFACT_CORRUPTION_DETECTED,
                self.artifact_integrity,
                False,
                "artifact integrity",
            ),
            (
                EnvironmentEventKind.PROTECTED_BOUNDARY_CROSSED,
                self.protected_boundary_crossed,
                True,
                "protected boundary",
            ),
            (
                EnvironmentEventKind.EVALUATOR_FAILED,
                self.evaluator_succeeded,
                False,
                "evaluator status",
            ),
        )
        for kind, metadata, event_value, name in event_boolean_pairs:
            if (kind in kinds) != (metadata.value is event_value):
                raise ValueError(f"{name} metadata must exactly match environment events")
        if self.output_artifacts_hash != artifact_collection_hash(self.output_artifacts):
            raise ValueError("output artifacts hash mismatch")
        if self.output_hash != self.output_artifacts_hash:
            raise ValueError("output hash must address the exact observable output artifacts")
        if self.reward_observation is None:
            if (
                self.reward_observation_hash.status is not MetadataAvailability.UNAVAILABLE
                or self.capture_reward_validity.status
                is not MetadataAvailability.NOT_APPLICABLE
            ):
                raise ValueError(
                    "absent reward requires UNAVAILABLE hash and inapplicable validity"
                )
        else:
            if (
                self.reward_observation_hash.status is not MetadataAvailability.AVAILABLE
                or self.reward_observation_hash.value != self.reward_observation.content_hash
                or self.reward_observation_hash.evidence_id
                != self.reward_observation.observation_id
            ):
                raise ValueError("reward observation hash must bind the embedded observation")
            if (
                self.reward_observation.task_id != self.observed_binding.task_id
                or self.reward_observation.task_input_hash
                != self.observed_binding.task_input_hash
                or self.reward_observation.checker_result_id != self.checker_result_id
                or self.reward_observation.checker_result_hash != self.checker_result_hash
            ):
                raise ValueError("reward observation must bind the exact observed task and checker")
        if self.resource_usage_hash != _canonical_record_hash(self.resource_usage):
            raise ValueError("resource usage hash mismatch")
        if self.provenance_hash != _canonical_record_hash(
            {"evidence_ids": list(self.provenance_evidence_ids)}
        ):
            raise ValueError("provenance hash mismatch")
        return self


class HarnessExecutionTrace(_HarnessExecutionTracePayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        supplied = dict(values)
        supplied.setdefault(
            "context_transformations_hash",
            _canonical_record_hash(
                {
                    "transformations": [
                        ContextTransformation.model_validate(item).content_hash
                        for item in supplied["context_transformations"]
                    ]
                }
            ),
        )
        supplied.setdefault(
            "tool_observations_hash",
            _canonical_record_hash(
                {
                    "observations": [
                        ToolObservation.model_validate(item).content_hash
                        for item in supplied["tool_observations"]
                    ]
                }
            ),
        )
        supplied.setdefault(
            "environment_events_hash",
            _canonical_record_hash(
                {
                    "events": [
                        EnvironmentEvent.model_validate(item).content_hash
                        for item in supplied["environment_events"]
                    ]
                }
            ),
        )
        supplied.setdefault(
            "output_artifacts_hash",
            artifact_collection_hash(tuple(supplied["output_artifacts"])),
        )
        supplied.setdefault(
            "resource_usage_hash",
            _canonical_record_hash(ResourceUsage.model_validate(supplied["resource_usage"])),
        )
        supplied.setdefault(
            "provenance_hash",
            _canonical_record_hash(
                {"evidence_ids": list(supplied["provenance_evidence_ids"])}
            ),
        )
        payload = _HarnessExecutionTracePayload(**supplied)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=trace_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != trace_hash(self):
            raise ValueError("content_hash must canonically address the harness execution trace")
        return self


def trace_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


def trace_freshness(trace: HarnessExecutionTrace) -> TraceFreshness:
    validated = HarnessExecutionTrace.model_validate(trace)
    expected = validated.expected_binding
    observed = validated.observed_binding
    comparisons: tuple[tuple[object, object, TraceBindingMismatch], ...] = (
        (
            (
                expected.protocol_id,
                expected.protocol_version,
                expected.protocol_hash,
                expected.guidance_protocol,
                expected.model_harness_protocol,
                expected.guidance_condition,
                expected.authorized_artifact_ids,
            ),
            (
                observed.protocol_id,
                observed.protocol_version,
                observed.protocol_hash,
                observed.guidance_protocol,
                observed.model_harness_protocol,
                observed.guidance_condition,
                observed.authorized_artifact_ids,
            ),
            TraceBindingMismatch.PROTOCOL,
        ),
        (
            (expected.task_id, expected.task_input_hash, expected.partition),
            (observed.task_id, observed.task_input_hash, observed.partition),
            TraceBindingMismatch.TASK,
        ),
        (
            (expected.model, expected.model_hash),
            (observed.model, observed.model_hash),
            TraceBindingMismatch.MODEL,
        ),
        (
            (expected.harness, expected.harness_hash),
            (observed.harness, observed.harness_hash),
            TraceBindingMismatch.HARNESS,
        ),
        (
            (expected.procedure_id, expected.procedure_version, expected.procedure_hash),
            (observed.procedure_id, observed.procedure_version, observed.procedure_hash),
            TraceBindingMismatch.PROCEDURE,
        ),
        (
            (
                expected.environment_id,
                expected.environment_version,
                expected.environment_hash,
            ),
            (
                observed.environment_id,
                observed.environment_version,
                observed.environment_hash,
            ),
            TraceBindingMismatch.ENVIRONMENT,
        ),
        (expected.context_hash, observed.context_hash, TraceBindingMismatch.CONTEXT),
        (
            (
                expected.validator_id,
                expected.validator_version,
                expected.validator_hash,
                expected.checker_id,
                expected.checker_version,
                expected.checker_hash,
            ),
            (
                observed.validator_id,
                observed.validator_version,
                observed.validator_hash,
                observed.checker_id,
                observed.checker_version,
                observed.checker_hash,
            ),
            TraceBindingMismatch.VALIDATOR,
        ),
        (
            (expected.artifact_ids, expected.artifact_hashes),
            (observed.artifact_ids, observed.artifact_hashes),
            TraceBindingMismatch.ARTIFACTS,
        ),
        (
            expected.output_schema_hash,
            observed.output_schema_hash,
            TraceBindingMismatch.OUTPUT_SCHEMA,
        ),
    )
    mismatches = tuple(item for left, right, item in comparisons if left != right)
    return TraceFreshness.build(
        trace_id=validated.trace_id,
        trace_hash=validated.content_hash,
        expected_binding_hash=expected.content_hash,
        observed_binding_hash=observed.content_hash,
        status=(
            TraceFreshnessStatus.CURRENT
            if not mismatches
            else TraceFreshnessStatus.STALE
        ),
        mismatches=mismatches,
    )


__all__ = [
    "AvailableValue",
    "CaptureRewardValidityStatus",
    "ContextTransformation",
    "ContextTransformationKind",
    "EnvironmentEvent",
    "EnvironmentEventKind",
    "ExecutionStatus",
    "GenerationMetadata",
    "GenerationStopReason",
    "HarnessExecutionTrace",
    "MetadataAvailability",
    "ObservableArtifactRef",
    "RewardObservation",
    "ToolObservation",
    "ToolObservationStatus",
    "TraceBinding",
    "TraceBindingMismatch",
    "TraceFreshness",
    "TraceFreshnessStatus",
    "artifact_collection_hash",
    "context_transformation_hash",
    "environment_event_hash",
    "generation_metadata_hash",
    "observable_artifact_hash",
    "reward_observation_hash",
    "tool_observation_hash",
    "trace_binding_hash",
    "trace_freshness",
    "trace_freshness_hash",
    "trace_hash",
]
