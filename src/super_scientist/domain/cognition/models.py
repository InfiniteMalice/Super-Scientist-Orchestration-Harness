from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, NoReturn, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError
from pydantic.config import ExtraValues
from pydantic_core import to_jsonable_python

from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    canonical_json_bytes,
    sha256_hex,
)

MAX_COGNITION_ITEMS = 64
MAX_IDENTIFIER_LENGTH = 200
MAX_TEXT_LENGTH = 2_000
MAX_ERROR_CORRELATION_SAMPLES = 1_000_000
MAX_COHORT_PLAN_BYTES = 8 * 1024 * 1024
# A compact plan adds at most 4,096 fixed-size assessment hashes plus fewer than
# 520 repetitions of already-bounded identifiers. Even at the six-byte JSON escape
# width, their aggregate and all fixed structural metadata remain below 2 MiB.
MAX_COHORT_COMPACT_DERIVED_BYTES = 2 * 1024 * 1024
MAX_COHORT_GROUNDING_INPUT_BYTES = min(
    4 * 1024 * 1024,
    MAX_COHORT_PLAN_BYTES - MAX_COHORT_COMPACT_DERIVED_BYTES,
)
_COGNITION_INPUT_FAILURES = (
    MemoryError,
    OverflowError,
    RecursionError,
    TypeError,
    ValueError,
)


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


def _require_bounded_actor_identity(actor: ActorIdentity) -> ActorIdentity:
    identity_fields = (
        actor.actor_id,
        actor.provider_id,
        actor.model_id,
        actor.adapter_id,
    )
    if any(value is not None and len(value) > MAX_IDENTIFIER_LENGTH for value in identity_fields):
        raise ValueError("Phase A actor identity fields must be bounded identifiers")
    return actor


def _require_strict_actor_identity_input(
    value: object,
    info: ValidationInfo,
) -> object:
    raw = (
        value.model_dump(mode="python", warnings=False)
        if isinstance(value, ActorIdentity)
        else value
    )
    if not isinstance(raw, dict):
        raise ValueError("Phase A actor identity must be a strict object")
    string_fields = (
        "actor_id",
        "provider_id",
        "model_id",
        "adapter_id",
        "configuration_hash",
    )
    if any(
        field_name in raw and raw[field_name] is not None and not isinstance(raw[field_name], str)
        for field_name in string_fields
    ):
        raise ValueError("Phase A actor identity scalars must be strict")
    created_at = raw.get("created_at")
    kind = raw.get("kind")
    if info.mode == "python" and not isinstance(kind, ActorKind):
        raise ValueError("Phase A actor identity kind must be a strict ActorKind")
    if info.mode == "json" and not isinstance(kind, str):
        raise ValueError("Phase A actor identity JSON kind must be a string")
    if info.mode == "python" and not isinstance(created_at, datetime):
        raise ValueError("Phase A actor identity timestamp must be a strict datetime")
    if info.mode == "json" and not isinstance(created_at, str):
        raise ValueError("Phase A actor identity JSON timestamp must be a string")
    try:
        if info.mode == "json":
            parsed = ActorIdentity.model_validate(raw)
        else:
            parsed = ActorIdentity.model_validate(raw, strict=True)
    except (PydanticValidationError, TypeError, ValueError):
        raise ValueError("Phase A actor identity is invalid") from None
    return parsed


BoundedActorIdentity = Annotated[
    ActorIdentity,
    BeforeValidator(_require_strict_actor_identity_input),
    AfterValidator(_require_bounded_actor_identity),
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


def _strict_revalidate_cognition_model[CognitionModelT: BaseModel](
    value: object,
    expected_type: type[CognitionModelT],
    *,
    label: str,
) -> CognitionModelT:
    if type(value) is not expected_type:
        raise TypeError(f"{label} must be an exact {expected_type.__name__}")
    validated: CognitionModelT | None = None
    try:
        serialized = value.model_dump_json(warnings=False)
        validated = expected_type.model_validate_json(serialized)
    except _COGNITION_INPUT_FAILURES:
        pass
    if validated is None:
        raise ValueError(f"{label} is invalid")
    return validated


def _require_canonical_unique(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must contain unique values")
    if values != tuple(sorted(values)):
        raise ValueError(f"{field_name} must be sorted in canonical order")
    return values


def _content_hash(model: BaseModel, *, exclude: str = "content_hash") -> str:
    payload = model.model_dump(mode="json", exclude={exclude}, warnings=False)
    return sha256_hex(canonical_json_bytes(payload))


def _reject_cohort_plan(message: str) -> NoReturn:
    error = PydanticValidationError.from_exception_data(
        "CohortPlan",
        [
            {
                "type": "value_error",
                "loc": (),
                "input": "[REDACTED]",
                "ctx": {"error": ValueError(message)},
            }
        ],
    )
    raise error from None


def _require_cohort_plan_byte_bound(value: object) -> None:
    serialization_failed = False
    try:
        serialized = canonical_json_bytes(to_jsonable_python(value))
    except _COGNITION_INPUT_FAILURES:
        serialization_failed = True
    if serialization_failed:
        _reject_cohort_plan("cohort plan canonical serialization failed")
    if len(serialized) > MAX_COHORT_PLAN_BYTES:
        _reject_cohort_plan("cohort serialized plan exceeds the Phase A byte limit")


class CapabilityEvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    SELF_REPORTED = "SELF_REPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class CapabilityDisposition(StrEnum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    UNKNOWN = "UNKNOWN"


class DiversityAxisStatus(StrEnum):
    DIFFERENT = "DIFFERENT"
    SAME = "SAME"
    UNKNOWN = "UNKNOWN"


class ErrorCorrelationStatus(StrEnum):
    KNOWN = "KNOWN"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class CapabilityAssertion(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    assertion_id: BoundedIdentifier
    capability_id: BoundedIdentifier
    task_family_id: BoundedIdentifier
    status: CapabilityEvidenceStatus
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    validator_id: BoundedIdentifier | None
    validator_version: BoundedIdentifier | None
    evidence_snapshot_hash: Sha256Hex

    @field_validator("evidence_ids")
    @classmethod
    def require_canonical_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name="evidence_ids")

    @model_validator(mode="after")
    def require_grounded_verification(self) -> Self:
        validator_fields = (self.validator_id, self.validator_version)
        if self.status is CapabilityEvidenceStatus.VERIFIED and (
            not self.evidence_ids or any(value is None for value in validator_fields)
        ):
            raise ValueError(
                "verified assertions require evidence IDs and validator identity/version"
            )
        if self.status is not CapabilityEvidenceStatus.VERIFIED and any(
            value is not None for value in validator_fields
        ):
            raise ValueError("only verified assertions may declare validator identity/version")
        return self


class DiversityFingerprint(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    fingerprint_id: BoundedIdentifier
    model_family: BoundedIdentifier | None
    model_version: BoundedIdentifier | None
    scale_class: BoundedIdentifier | None
    provider: BoundedIdentifier | None
    adapter_hash: Sha256Hex | None
    configuration_hash: Sha256Hex | None
    prompt_strategy: BoundedIdentifier | None
    methodological_prior: BoundedIdentifier | None
    tools: tuple[BoundedIdentifier, ...] | None = Field(max_length=MAX_COGNITION_ITEMS)
    evidence_partitions: tuple[BoundedIdentifier, ...] | None = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    modalities: tuple[BoundedIdentifier, ...] | None = Field(max_length=MAX_COGNITION_ITEMS)
    previous_error_clusters: tuple[BoundedIdentifier, ...] | None = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    prior_task_specializations: tuple[BoundedIdentifier, ...] | None = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    assigned_role: BoundedIdentifier | None = None
    procedure_family: BoundedIdentifier | None = None

    @field_validator(
        "tools",
        "evidence_partitions",
        "modalities",
        "previous_error_clusters",
        "prior_task_specializations",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...] | None,
        info: Any,
    ) -> tuple[str, ...] | None:
        if values is None:
            return None
        return _require_canonical_unique(values, field_name=info.field_name)


class _CapabilityProfilePayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    profile_id: BoundedIdentifier
    actor: BoundedActorIdentity
    diversity_fingerprint: DiversityFingerprint
    allowed_tools: tuple[BoundedIdentifier, ...] = Field(default=(), max_length=MAX_COGNITION_ITEMS)
    modalities: tuple[BoundedIdentifier, ...] = Field(default=(), max_length=MAX_COGNITION_ITEMS)
    supported_schemas: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    execution_constraints: tuple[BoundedText, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    known_failure_categories: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    assertions: tuple[CapabilityAssertion, ...] = Field(default=(), max_length=MAX_COGNITION_ITEMS)
    governing_policy_hash: Sha256Hex

    @field_validator(
        "allowed_tools",
        "modalities",
        "supported_schemas",
        "execution_constraints",
        "known_failure_categories",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name=info.field_name)

    @field_validator("assertions")
    @classmethod
    def require_sorted_assertions(
        cls,
        assertions: tuple[CapabilityAssertion, ...],
    ) -> tuple[CapabilityAssertion, ...]:
        if len({item.assertion_id for item in assertions}) != len(assertions):
            raise ValueError("assertion IDs must be unique")
        logical_keys = tuple((item.capability_id, item.task_family_id) for item in assertions)
        if len(set(logical_keys)) != len(logical_keys):
            raise ValueError("assertions must contain one entry per logical capability/task key")
        expected = tuple(
            sorted(
                assertions,
                key=lambda item: (item.capability_id, item.task_family_id, item.assertion_id),
            )
        )
        if assertions != expected:
            raise ValueError("assertions must be sorted in canonical order")
        return assertions

    @model_validator(mode="after")
    def require_fingerprint_identity_alignment(self) -> Self:
        fingerprint = self.diversity_fingerprint
        actor = self.actor
        if fingerprint.provider is not None and fingerprint.provider != actor.provider_id:
            raise ValueError("fingerprint provider must match actor provider identity")
        if (
            fingerprint.configuration_hash is not None
            and fingerprint.configuration_hash != actor.configuration_hash
        ):
            raise ValueError("fingerprint configuration hash must match actor identity")
        return self


class CapabilityProfile(_CapabilityProfilePayload):
    content_hash: Sha256Hex

    @property
    def actor_id(self) -> str:
        return self.actor.actor_id

    @property
    def actor_kind(self) -> ActorKind:
        return self.actor.kind

    @property
    def model_identity(self) -> str | None:
        return self.actor.model_id

    @property
    def provider_identity(self) -> str | None:
        return self.actor.provider_id

    @property
    def adapter_identity(self) -> str | None:
        return self.actor.adapter_id

    @property
    def configuration_hash(self) -> str | None:
        return self.actor.configuration_hash

    @classmethod
    def build(cls, **values: Any) -> CapabilityProfile:
        payload = _CapabilityProfilePayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json", warnings=False)))
        return cls(
            **payload.model_dump(mode="python", warnings=False),
            content_hash=digest,
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the capability profile")
        return self


class CapabilityRequirement(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    requirement_id: BoundedIdentifier
    capability_id: BoundedIdentifier
    task_family_id: BoundedIdentifier
    evidence_snapshot_hash: Sha256Hex
    required_tools: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    required_modalities: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    required_schema_ids: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    required_execution_constraints: tuple[BoundedText, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    disqualifying_failure_categories: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )

    @field_validator(
        "required_tools",
        "required_modalities",
        "required_schema_ids",
        "required_execution_constraints",
        "disqualifying_failure_categories",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name=info.field_name)


class CapabilityAssessment(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    profile_id: BoundedIdentifier
    actor_id: BoundedIdentifier
    requirement: CapabilityRequirement
    matched_assertion_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    verified_assertion_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    disposition: CapabilityDisposition
    evidence_status: CapabilityEvidenceStatus
    missing_dimensions: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    failed_dimensions: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)

    @field_validator(
        "matched_assertion_ids",
        "verified_assertion_ids",
        "missing_dimensions",
        "failed_dimensions",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name=info.field_name)

    @model_validator(mode="after")
    def require_consistent_semantics(self) -> Self:
        matched = set(self.matched_assertion_ids)
        verified = set(self.verified_assertion_ids)
        if not verified.issubset(matched):
            raise ValueError(
                "capability assessment verified assertion IDs must be matched assertion IDs"
            )
        if verified and self.evidence_status is not CapabilityEvidenceStatus.VERIFIED:
            raise ValueError(
                "capability assessment verified assertions require VERIFIED evidence status"
            )
        if not matched and self.evidence_status is not CapabilityEvidenceStatus.UNKNOWN:
            raise ValueError(
                "capability assessment without matches must retain UNKNOWN evidence status"
            )
        if self.disposition is CapabilityDisposition.SATISFIED:
            if (
                self.evidence_status is not CapabilityEvidenceStatus.VERIFIED
                or not verified
                or self.missing_dimensions
                or self.failed_dimensions
            ):
                raise ValueError(
                    "capability assessment SATISFIED disposition requires verified evidence "
                    "without missing or failed dimensions"
                )
        elif self.disposition is CapabilityDisposition.UNSATISFIED:
            if (
                not self.failed_dimensions
                or self.evidence_status
                in {
                    CapabilityEvidenceStatus.SELF_REPORTED,
                    CapabilityEvidenceStatus.UNKNOWN,
                }
                or (self.evidence_status is CapabilityEvidenceStatus.VERIFIED and not verified)
            ):
                raise ValueError(
                    "capability assessment UNSATISFIED disposition requires a grounded failure"
                )
        elif not self.missing_dimensions and not self.failed_dimensions:
            raise ValueError(
                "capability assessment UNKNOWN disposition requires a missing or failed dimension"
            )
        elif self.evidence_status is CapabilityEvidenceStatus.UNSUPPORTED:
            raise ValueError(
                "capability assessment UNSUPPORTED evidence cannot have UNKNOWN disposition"
            )
        return self

    @classmethod
    def from_matches(
        cls,
        profile: CapabilityProfile,
        requirement: CapabilityRequirement,
        matching: tuple[CapabilityAssertion, ...],
        verified: tuple[CapabilityAssertion, ...],
    ) -> CapabilityAssessment:
        matched_ids = tuple(sorted(item.assertion_id for item in matching))
        verified_ids = tuple(sorted(item.assertion_id for item in verified))
        if not matching:
            return cls(
                profile_id=profile.profile_id,
                actor_id=profile.actor_id,
                requirement=requirement,
                matched_assertion_ids=(),
                verified_assertion_ids=(),
                disposition=CapabilityDisposition.UNKNOWN,
                evidence_status=CapabilityEvidenceStatus.UNKNOWN,
                missing_dimensions=("capability_evidence",),
                failed_dimensions=(),
            )

        statuses = {item.status for item in matching}
        evidence_status = next(
            status
            for status in (
                CapabilityEvidenceStatus.VERIFIED,
                CapabilityEvidenceStatus.UNSUPPORTED,
                CapabilityEvidenceStatus.SELF_REPORTED,
                CapabilityEvidenceStatus.UNKNOWN,
            )
            if status in statuses
        )
        missing: list[str] = []
        failed: list[str] = []
        dimensions = (
            ("required_tools", requirement.required_tools, profile.allowed_tools),
            ("required_modalities", requirement.required_modalities, profile.modalities),
            ("required_schema_ids", requirement.required_schema_ids, profile.supported_schemas),
            (
                "required_execution_constraints",
                requirement.required_execution_constraints,
                profile.execution_constraints,
            ),
        )
        missing.extend(
            name
            for name, required, available in dimensions
            if not set(required).issubset(available)
        )
        if set(requirement.disqualifying_failure_categories) & set(
            profile.known_failure_categories
        ):
            failed.append("known_failure_categories")

        if verified:
            if failed:
                disposition = CapabilityDisposition.UNSATISFIED
            elif missing:
                disposition = CapabilityDisposition.UNKNOWN
            else:
                disposition = CapabilityDisposition.SATISFIED
        elif CapabilityEvidenceStatus.UNSUPPORTED in statuses:
            disposition = CapabilityDisposition.UNSATISFIED
            failed.append("capability_support")
        else:
            disposition = CapabilityDisposition.UNKNOWN
            if CapabilityEvidenceStatus.VERIFIED in statuses:
                failed.append("evidence_snapshot_hash")
            else:
                missing.append("verified_evidence")

        return cls(
            profile_id=profile.profile_id,
            actor_id=profile.actor_id,
            requirement=requirement,
            matched_assertion_ids=matched_ids,
            verified_assertion_ids=verified_ids,
            disposition=disposition,
            evidence_status=evidence_status,
            missing_dimensions=tuple(sorted(set(missing))),
            failed_dimensions=tuple(sorted(set(failed))),
        )


class _CohortRequestPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    request_id: BoundedIdentifier
    task_id: BoundedIdentifier
    required_capabilities: tuple[CapabilityRequirement, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    preferred_capabilities: tuple[CapabilityRequirement, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    min_members: int = Field(strict=True, ge=0, le=MAX_COGNITION_ITEMS)
    max_members: int = Field(strict=True, ge=1, le=MAX_COGNITION_ITEMS)
    candidate_actor_ids: tuple[BoundedIdentifier, ...] = Field(
        min_length=1,
        max_length=MAX_COGNITION_ITEMS,
    )
    prohibited_combinations: tuple[tuple[BoundedIdentifier, BoundedIdentifier], ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    tie_break_policy: Literal["ACTOR_ID_ASC"] = "ACTOR_ID_ASC"
    governing_policy_hash: Sha256Hex

    @field_validator("required_capabilities", "preferred_capabilities")
    @classmethod
    def require_sorted_requirements(
        cls,
        requirements: tuple[CapabilityRequirement, ...],
        info: Any,
    ) -> tuple[CapabilityRequirement, ...]:
        ids = tuple(item.requirement_id for item in requirements)
        _require_canonical_unique(ids, field_name=info.field_name)
        return requirements

    @field_validator("candidate_actor_ids")
    @classmethod
    def require_canonical_candidates(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name="candidate_actor_ids")

    @field_validator("prohibited_combinations")
    @classmethod
    def require_canonical_prohibited_pairs(
        cls,
        pairs: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        if any(left >= right for left, right in pairs):
            raise ValueError("prohibited combinations must use canonical distinct actor order")
        if len(set(pairs)) != len(pairs) or pairs != tuple(sorted(pairs)):
            raise ValueError("prohibited combinations must be unique and canonically sorted")
        return pairs

    @model_validator(mode="after")
    def require_coherent_bounds_and_requirements(self) -> Self:
        if self.min_members > self.max_members:
            raise ValueError("min_members must not exceed max_members")
        required_ids = {item.requirement_id for item in self.required_capabilities}
        preferred_ids = {item.requirement_id for item in self.preferred_capabilities}
        if required_ids & preferred_ids:
            raise ValueError("required and preferred requirement IDs must be disjoint")
        if len(self.required_capabilities) + len(self.preferred_capabilities) > (
            MAX_COGNITION_ITEMS
        ):
            raise ValueError("cohort request permits at most 64 total requirements")
        return self


class CohortRequest(_CohortRequestPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> CohortRequest:
        payload = _CohortRequestPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json", warnings=False)))
        return cls(
            **payload.model_dump(mode="python", warnings=False),
            content_hash=digest,
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the cohort request")
        return self


class CohortMember(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    actor_id: BoundedIdentifier
    profile_id: BoundedIdentifier
    profile_content_hash: Sha256Hex
    required_satisfied: int = Field(strict=True, ge=0, le=MAX_COGNITION_ITEMS)
    preferred_satisfied: int = Field(strict=True, ge=0, le=MAX_COGNITION_ITEMS)


class CohortRankedCandidate(CohortMember):
    """Grounded score evidence for every resolved candidate, selected or excluded."""

    assessment_hashes: tuple[Sha256Hex, ...] = Field(max_length=MAX_COGNITION_ITEMS)

    @property
    def rank_key(self) -> tuple[int, int]:
        return (self.required_satisfied, self.preferred_satisfied)


class CapabilityCoverage(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    requirement_id: BoundedIdentifier
    satisfying_actor_indexes: tuple[
        Annotated[int, Field(strict=True, ge=0, lt=MAX_COGNITION_ITEMS)], ...
    ] = Field(max_length=MAX_COGNITION_ITEMS)

    @field_validator("satisfying_actor_indexes")
    @classmethod
    def require_canonical_actor_indexes(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(values)) != len(values) or values != tuple(sorted(values)):
            raise ValueError("satisfying_actor_indexes must be unique and canonically sorted")
        return values


class CohortTieRank(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    required_satisfied: int = Field(strict=True, ge=0, le=MAX_COGNITION_ITEMS)
    preferred_satisfied: int = Field(strict=True, ge=0, le=MAX_COGNITION_ITEMS)

    @property
    def key(self) -> tuple[int, int]:
        return (self.required_satisfied, self.preferred_satisfied)


class _CohortPlanPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    cohort_plan_id: BoundedIdentifier
    request_id: BoundedIdentifier
    request_content_hash: Sha256Hex
    request_snapshot: CohortRequest
    task_id: BoundedIdentifier
    resolved_candidate_profiles: tuple[CapabilityProfile, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    members: tuple[CohortMember, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    excluded_actor_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    coverage: tuple[CapabilityCoverage, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    unresolved_requirement_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    unresolved_candidate_actor_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    ranked_candidates: tuple[CohortRankedCandidate, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    tie_sets: tuple[
        Annotated[
            tuple[BoundedIdentifier, ...],
            Field(max_length=MAX_COGNITION_ITEMS),
        ],
        ...,
    ] = Field(max_length=MAX_COGNITION_ITEMS)
    tie_group_ranks: tuple[CohortTieRank, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    evidence_snapshot_hashes: tuple[Sha256Hex, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    profile_content_hashes: tuple[Sha256Hex, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    minimum_size_met: bool
    governing_policy_hash: Sha256Hex

    @field_validator(
        "excluded_actor_ids",
        "unresolved_requirement_ids",
        "unresolved_candidate_actor_ids",
        "evidence_snapshot_hashes",
        "profile_content_hashes",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name=info.field_name)

    @field_validator("tie_sets")
    @classmethod
    def require_complete_canonical_tie_sets(
        cls,
        tie_sets: tuple[tuple[str, ...], ...],
    ) -> tuple[tuple[str, ...], ...]:
        for tie_set in tie_sets:
            if len(tie_set) < 2:
                raise ValueError("tie sets must contain at least two actors")
            _require_canonical_unique(tie_set, field_name="tie set")
        actors = tuple(actor_id for tie_set in tie_sets for actor_id in tie_set)
        if len(set(actors)) != len(actors):
            raise ValueError("an actor may appear in only one ranked tie set")
        return tie_sets

    @model_validator(mode="after")
    def require_cross_field_integrity(self) -> Self:
        _require_cohort_plan_byte_bound(self)
        request = self.request_snapshot
        if (
            self.request_id != request.request_id
            or self.request_content_hash != request.content_hash
            or self.task_id != request.task_id
            or self.governing_policy_hash != request.governing_policy_hash
        ):
            raise ValueError("cohort plan must exactly match its retained request snapshot")

        profile_actor_ids = tuple(profile.actor_id for profile in self.resolved_candidate_profiles)
        if profile_actor_ids != tuple(sorted(profile_actor_ids)):
            raise ValueError("cohort candidate profile snapshots must use canonical actor order")
        if len(set(profile_actor_ids)) != len(profile_actor_ids):
            raise ValueError("cohort candidate profile snapshots must have unique actor IDs")
        if len({profile.profile_id for profile in self.resolved_candidate_profiles}) != len(
            self.resolved_candidate_profiles
        ):
            raise ValueError("cohort candidate profile snapshots must have unique profile IDs")
        if len({profile.content_hash for profile in self.resolved_candidate_profiles}) != len(
            self.resolved_candidate_profiles
        ):
            raise ValueError("cohort candidate profile snapshots must have unique content hashes")
        if not set(profile_actor_ids).issubset(request.candidate_actor_ids):
            raise ValueError(
                "cohort candidate profile snapshots must belong to the fixed candidate roster"
            )
        grounding_input_bytes = canonical_json_bytes(
            {
                "request_snapshot": request.model_dump(mode="json", warnings=False),
                "resolved_candidate_profiles": tuple(
                    profile.model_dump(mode="json", warnings=False)
                    for profile in self.resolved_candidate_profiles
                ),
            }
        )
        if len(grounding_input_bytes) > MAX_COHORT_GROUNDING_INPUT_BYTES:
            raise ValueError("cohort grounding inputs exceed the Phase A byte limit")

        member_actor_ids = tuple(member.actor_id for member in self.members)
        if len(set(member_actor_ids)) != len(member_actor_ids):
            raise ValueError("cohort plan members must have unique actor IDs")
        expected_members = tuple(
            sorted(
                self.members,
                key=lambda item: (
                    -item.required_satisfied,
                    -item.preferred_satisfied,
                    item.actor_id,
                ),
            )
        )
        if self.members != expected_members:
            raise ValueError("cohort plan members must use canonical score and actor order")
        if len({member.profile_id for member in self.members}) != len(self.members):
            raise ValueError("cohort plan member profile IDs must be unique")
        if len({member.profile_content_hash for member in self.members}) != len(self.members):
            raise ValueError("cohort plan member profile content hashes must be unique")

        coverage_ids = tuple(item.requirement_id for item in self.coverage)
        _require_canonical_unique(coverage_ids, field_name="cohort plan coverage")
        if coverage_ids != tuple(
            requirement.requirement_id for requirement in request.required_capabilities
        ):
            raise ValueError(
                "cohort plan coverage must exactly match the required capability catalog"
            )
        selected = set(member_actor_ids)
        excluded = set(self.excluded_actor_ids)
        if selected & excluded:
            raise ValueError("cohort plan selected and excluded actors must be disjoint")
        known_actors = selected | excluded
        unresolved_candidates = set(self.unresolved_candidate_actor_ids)
        if unresolved_candidates & selected:
            raise ValueError("unresolved candidate actor cannot be a selected cohort member")
        if unresolved_candidates & excluded:
            raise ValueError("cohort plan excluded and unresolved actors must be disjoint")
        roster = set(request.candidate_actor_ids)
        resolved_profiles = set(profile_actor_ids)
        if (
            selected | excluded != resolved_profiles
            or unresolved_candidates != roster - resolved_profiles
            or selected | excluded | unresolved_candidates != roster
        ):
            raise ValueError(
                "cohort selected, excluded, and unresolved sets must exactly match the "
                "candidate roster partition"
            )

        ranked_actor_ids = tuple(candidate.actor_id for candidate in self.ranked_candidates)
        if len(set(ranked_actor_ids)) != len(ranked_actor_ids):
            raise ValueError("cohort ranked candidates must have unique actor IDs")
        expected_ranked_candidates = tuple(
            sorted(
                self.ranked_candidates,
                key=lambda item: (
                    -item.required_satisfied,
                    -item.preferred_satisfied,
                    item.actor_id,
                ),
            )
        )
        if self.ranked_candidates != expected_ranked_candidates:
            raise ValueError("cohort ranked candidates must use canonical score and actor order")
        if set(ranked_actor_ids) != known_actors:
            raise ValueError(
                "cohort ranked candidates must exactly cover resolved candidate actors"
            )
        if len({candidate.profile_id for candidate in self.ranked_candidates}) != len(
            self.ranked_candidates
        ):
            raise ValueError("cohort ranked candidates must have unique profile IDs")
        if len({candidate.profile_content_hash for candidate in self.ranked_candidates}) != len(
            self.ranked_candidates
        ):
            raise ValueError("cohort ranked candidates must have unique profile content hashes")

        tied_actors = {actor_id for tie_set in self.tie_sets for actor_id in tie_set}
        if not tied_actors.issubset(known_actors):
            raise ValueError("cohort plan tie sets must reference declared cohort actors")
        if len(self.tie_sets) != len(self.tie_group_ranks):
            raise ValueError("cohort plan tie sets must retain one rank key per group")
        tie_rank_keys = tuple(rank.key for rank in self.tie_group_ranks)
        if tie_rank_keys != tuple(sorted(tie_rank_keys, reverse=True)):
            raise ValueError("cohort plan tie groups must use descending canonical rank order")
        if len(set(tie_rank_keys)) != len(tie_rank_keys):
            raise ValueError("cohort plan tie groups must retain one group per rank key")

        requirement_count = len(request.required_capabilities) + len(request.preferred_capabilities)
        for candidate in self.ranked_candidates:
            if len(candidate.assessment_hashes) != requirement_count:
                raise ValueError(
                    "cohort ranked candidate assessment hashes must exactly cover the "
                    "request requirement catalog"
                )

        ranked_by_actor = {candidate.actor_id: candidate for candidate in self.ranked_candidates}
        for member in self.members:
            ranked_candidate = ranked_by_actor[member.actor_id]
            if (
                member.actor_id != ranked_candidate.actor_id
                or member.profile_id != ranked_candidate.profile_id
                or member.profile_content_hash != ranked_candidate.profile_content_hash
                or member.required_satisfied != ranked_candidate.required_satisfied
                or member.preferred_satisfied != ranked_candidate.preferred_satisfied
            ):
                raise ValueError("cohort member must exactly match its grounded ranking evidence")

        score_groups: dict[tuple[int, int], list[str]] = {}
        for candidate in self.ranked_candidates:
            score_groups.setdefault(candidate.rank_key, []).append(candidate.actor_id)
        expected_tie_sets = tuple(
            tuple(actor_ids) for actor_ids in score_groups.values() if len(actor_ids) > 1
        )
        expected_tie_group_ranks = tuple(
            CohortTieRank(
                required_satisfied=score[0],
                preferred_satisfied=score[1],
            )
            for score, actor_ids in score_groups.items()
            if len(actor_ids) > 1
        )
        if self.tie_sets != expected_tie_sets or self.tie_group_ranks != expected_tie_group_ranks:
            raise ValueError("cohort plan tie groups must exactly match grounded ranking evidence")

        for coverage in self.coverage:
            if any(
                index >= len(request.candidate_actor_ids)
                for index in coverage.satisfying_actor_indexes
            ):
                raise ValueError(
                    "cohort coverage actor indexes must reference the candidate roster"
                )
            satisfying_actor_ids = tuple(
                request.candidate_actor_ids[index] for index in coverage.satisfying_actor_indexes
            )
            if not set(satisfying_actor_ids).issubset(selected):
                raise ValueError("cohort coverage must reference selected cohort actors")

        expected_unresolved = tuple(
            coverage.requirement_id
            for coverage in self.coverage
            if not coverage.satisfying_actor_indexes
        )
        if self.unresolved_requirement_ids != expected_unresolved:
            raise ValueError("unresolved requirements must exactly match uncovered requirements")
        expected_profile_hashes = tuple(
            sorted(candidate.profile_content_hash for candidate in self.ranked_candidates)
        )
        if self.profile_content_hashes != expected_profile_hashes:
            raise ValueError("profile content hashes must exactly match grounded ranking evidence")

        from super_scientist.domain.cognition.grounding import _derive_cohort

        derived = _derive_cohort(request, self.resolved_candidate_profiles)
        if (
            self.ranked_candidates != derived.ranked_candidates
            or self.profile_content_hashes != derived.profile_content_hashes
            or self.evidence_snapshot_hashes != derived.evidence_snapshot_hashes
        ):
            raise ValueError(
                "cohort plan must exactly match recomputed grounded candidate evidence"
            )
        if (
            self.members != derived.members
            or self.excluded_actor_ids != derived.excluded_actor_ids
            or self.coverage != derived.coverage
            or self.unresolved_requirement_ids != derived.unresolved_requirement_ids
            or self.unresolved_candidate_actor_ids != derived.unresolved_candidate_actor_ids
            or self.tie_sets != derived.tie_sets
            or self.tie_group_ranks != derived.tie_group_ranks
            or self.minimum_size_met != derived.minimum_size_met
        ):
            raise ValueError(
                "cohort selection, coverage, gaps, and ties must exactly match recomputed "
                "grounding inputs"
            )
        return self


class CohortPlan(_CohortPlanPayload):
    content_hash: Sha256Hex

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        _require_cohort_plan_byte_bound(obj)
        return super().model_validate(
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        json_type = type(json_data)
        if json_type is not str and json_type is not bytes:
            raise ValueError("cohort plan JSON input must be an exact str or bytes value")

        normalized_json: str | None = None
        normalization_failed = False
        if isinstance(json_data, str):
            if len(json_data) > MAX_COHORT_PLAN_BYTES:
                raise ValueError("cohort serialized plan exceeds the Phase A byte limit")
            try:
                encoded_json = str.encode(json_data, "utf-8")
            except _COGNITION_INPUT_FAILURES:
                normalization_failed = True
            if not normalization_failed:
                if len(encoded_json) > MAX_COHORT_PLAN_BYTES:
                    raise ValueError("cohort serialized plan exceeds the Phase A byte limit")
                normalized_json = json_data
        else:
            assert isinstance(json_data, bytes)
            if len(json_data) > MAX_COHORT_PLAN_BYTES:
                raise ValueError("cohort serialized plan exceeds the Phase A byte limit")
            try:
                normalized_json = bytes.decode(json_data, "utf-8")
            except _COGNITION_INPUT_FAILURES:
                normalization_failed = True
        if normalization_failed:
            raise ValueError("cohort plan JSON input is invalid")
        if normalized_json is None:
            raise ValueError("cohort plan JSON input is invalid")

        parsed: Self | None = None
        schema_error: PydanticValidationError | None = None
        json_failed = False
        try:
            parsed = super().model_validate_json(
                normalized_json,
                strict=strict,
                extra=extra,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except PydanticValidationError as error:
            error_details_failed = False
            try:
                json_failed = any(
                    detail.get("type") == "json_invalid"
                    for detail in error.errors(
                        include_url=False,
                        include_context=False,
                        include_input=False,
                    )
                )
            except _COGNITION_INPUT_FAILURES:
                error_details_failed = True
            if error_details_failed:
                json_failed = True
            elif not json_failed:
                schema_error = error
        except _COGNITION_INPUT_FAILURES:
            json_failed = True
        if json_failed:
            raise ValueError("cohort plan JSON input is invalid")
        if schema_error is not None:
            raise schema_error
        if parsed is None:
            raise ValueError("cohort plan JSON input is invalid")
        return parsed

    @classmethod
    def build(cls, **values: Any) -> CohortPlan:
        payload = _CohortPlanPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json", warnings=False)))
        return cls(
            **payload.model_dump(mode="python", warnings=False),
            content_hash=digest,
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the cohort plan")
        return self


class DiversityAxes(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    model_family: DiversityAxisStatus
    model_version: DiversityAxisStatus
    scale_class: DiversityAxisStatus
    provider: DiversityAxisStatus
    adapter_hash: DiversityAxisStatus
    configuration_hash: DiversityAxisStatus
    prompt_strategy: DiversityAxisStatus
    methodological_prior: DiversityAxisStatus
    tools: DiversityAxisStatus
    evidence_partitions: DiversityAxisStatus
    modalities: DiversityAxisStatus
    previous_error_clusters: DiversityAxisStatus
    prior_task_specializations: DiversityAxisStatus
    assigned_role: DiversityAxisStatus
    procedure_family: DiversityAxisStatus

    _AXES: ClassVar[tuple[str, ...]] = (
        "model_family",
        "model_version",
        "scale_class",
        "provider",
        "adapter_hash",
        "configuration_hash",
        "prompt_strategy",
        "methodological_prior",
        "tools",
        "evidence_partitions",
        "modalities",
        "previous_error_clusters",
        "prior_task_specializations",
        "assigned_role",
        "procedure_family",
    )

    def __getitem__(self, axis: str) -> DiversityAxisStatus:
        if axis not in self._AXES:
            raise KeyError(axis)
        value = getattr(self, axis)
        if not isinstance(value, DiversityAxisStatus):
            raise TypeError("diversity axis has an invalid status")
        return value

    def as_mapping(self) -> dict[str, DiversityAxisStatus]:
        return {axis: self[axis] for axis in self._AXES}


class ErrorCorrelationRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    correlation_id: BoundedIdentifier
    left_actor_id: BoundedIdentifier
    right_actor_id: BoundedIdentifier
    evaluation_set_id: BoundedIdentifier
    sample_count: int = Field(
        strict=True,
        ge=0,
        le=MAX_ERROR_CORRELATION_SAMPLES,
    )
    method: BoundedIdentifier
    status: ErrorCorrelationStatus
    value: float | None = Field(default=None, ge=-1.0, le=1.0, allow_inf_nan=False)
    governing_policy_hash: Sha256Hex

    @field_validator("value", mode="before")
    @classmethod
    def require_strict_finite_float(cls, value: object) -> object:
        if value is not None and (type(value) is not float or not math.isfinite(value)):
            raise ValueError("correlation value must be a finite strict float")
        return value

    @model_validator(mode="after")
    def require_status_consistent_value(self) -> Self:
        if self.left_actor_id >= self.right_actor_id:
            raise ValueError("correlation actor IDs must use canonical distinct order")
        if self.status is ErrorCorrelationStatus.KNOWN and self.value is None:
            raise ValueError("KNOWN error correlation requires a measured coefficient")
        if self.status is ErrorCorrelationStatus.KNOWN and self.sample_count == 0:
            raise ValueError("KNOWN error correlation requires an observed sample")
        if self.status is not ErrorCorrelationStatus.KNOWN and self.value is not None:
            raise ValueError("unknown error correlation must not store an invented coefficient")
        return self


class _DiversityAssessmentPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    diversity_assessment_id: BoundedIdentifier
    cohort_plan_id: BoundedIdentifier
    member_actor_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    axes: DiversityAxes
    error_correlations: tuple[ErrorCorrelationRecord, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    governing_policy_hash: Sha256Hex

    @field_validator("member_actor_ids")
    @classmethod
    def require_canonical_members(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name="member_actor_ids")

    @field_validator("error_correlations")
    @classmethod
    def require_canonical_correlations(
        cls,
        records: tuple[ErrorCorrelationRecord, ...],
    ) -> tuple[ErrorCorrelationRecord, ...]:
        ids = tuple(record.correlation_id for record in records)
        _require_canonical_unique(ids, field_name="error_correlations")
        return records

    @model_validator(mode="after")
    def require_correlation_policy_alignment(self) -> Self:
        if any(
            record.governing_policy_hash != self.governing_policy_hash
            for record in self.error_correlations
        ):
            raise ValueError(
                "error correlations and diversity assessment must share governing policy"
            )
        return self


class DiversityAssessment(_DiversityAssessmentPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> DiversityAssessment:
        payload = _DiversityAssessmentPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json", warnings=False)))
        return cls(
            **payload.model_dump(mode="python", warnings=False),
            content_hash=digest,
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the diversity assessment")
        return self


class AcceptedCognitiveReceiptRef(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    proposal_id: BoundedIdentifier
    proposal_hash: Sha256Hex
    audit_event_id: BoundedIdentifier
    audit_event_hash: Sha256Hex


class CapabilityProfileReceiptRef(AcceptedCognitiveReceiptRef):
    receipt_type: Literal["CAPABILITY_PROFILE"] = "CAPABILITY_PROFILE"


class CohortPlanReceiptRef(AcceptedCognitiveReceiptRef):
    receipt_type: Literal["COHORT_PLAN"] = "COHORT_PLAN"


__all__ = [
    "AcceptedCognitiveReceiptRef",
    "CapabilityAssertion",
    "CapabilityAssessment",
    "CapabilityCoverage",
    "CapabilityDisposition",
    "CapabilityEvidenceStatus",
    "CapabilityProfile",
    "CapabilityProfileReceiptRef",
    "CapabilityRequirement",
    "CohortMember",
    "CohortPlan",
    "CohortPlanReceiptRef",
    "CohortRankedCandidate",
    "CohortRequest",
    "CohortTieRank",
    "DiversityAssessment",
    "DiversityAxes",
    "DiversityAxisStatus",
    "DiversityFingerprint",
    "ErrorCorrelationRecord",
    "ErrorCorrelationStatus",
]
