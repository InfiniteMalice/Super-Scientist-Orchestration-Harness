from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import to_jsonable_python

from super_scientist.domain.improvement.models import ResourceUsage
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex

MAX_PHASE_A_ITEMS = 256
MAX_PHASE_A_IDENTIFIER_LENGTH = 200
MAX_PHASE_A_INTEGER = (1 << 1_000) - 1
MAX_DECIMAL_COEFFICIENT_DIGITS = 256
MAX_DECIMAL_ABS_EXPONENT = 1_024
MAX_DECIMAL_CANONICAL_BYTES = 260
MAX_RESOURCE_USAGE_CANONICAL_BYTES = 4_096

_PHASE_A_DECIMAL_CONTEXT = Context(
    prec=MAX_DECIMAL_COEFFICIENT_DIGITS,
    rounding=ROUND_HALF_EVEN,
    Emin=-MAX_DECIMAL_ABS_EXPONENT,
    Emax=MAX_DECIMAL_ABS_EXPONENT,
)


def _raise_resource_usage_error() -> NoReturn:
    """Raise only after unsafe DTO serialization or validation has unwound."""
    raise ValueError("resource usage requires canonical validated resource usage")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


def require_bounded_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal value must be finite")
    decimal_tuple = value.as_tuple()
    if len(decimal_tuple.digits) > MAX_DECIMAL_COEFFICIENT_DIGITS:
        raise ValueError("decimal coefficient exceeds bound")
    if isinstance(decimal_tuple.exponent, str) or (
        abs(decimal_tuple.exponent) > MAX_DECIMAL_ABS_EXPONENT
    ):
        raise ValueError("decimal exponent exceeds bound")
    if len(str(value).encode("ascii")) > MAX_DECIMAL_CANONICAL_BYTES:
        raise ValueError("decimal canonical bytes exceed bound")
    return value


def _phase_a_decimal_difference(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_PHASE_A_DECIMAL_CONTEXT):
        return +(right - left)


def require_bounded_integer(value: int, *, error: str) -> int:
    if abs(value) > MAX_PHASE_A_INTEGER:
        raise ValueError(error)
    return value


def canonical_record_bytes(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> bytes:
    if isinstance(record, BaseModel):
        payload = record.model_dump(mode="json", warnings=False)
    else:
        payload = to_jsonable_python(dict(record))
    for field_name in {"content_hash", *(exclude_fields or set())}:
        payload.pop(field_name, None)
    return canonical_json_bytes(payload)


def require_canonical_byte_limit(
    record: BaseModel | Mapping[str, object],
    *,
    maximum: int,
    error: str,
    exclude_fields: set[str] | None = None,
) -> bytes:
    encoded = canonical_record_bytes(record, exclude_fields=exclude_fields)
    if len(encoded) > maximum:
        raise ValueError(error)
    return encoded


def bounded_canonical_record_hash(
    record: BaseModel | Mapping[str, object],
    *,
    maximum: int,
    error: str,
    exclude_fields: set[str] | None = None,
) -> str:
    return sha256_hex(
        require_canonical_byte_limit(
            record,
            maximum=maximum,
            error=error,
            exclude_fields=exclude_fields,
        )
    )


class PhaseAResourceUsage(_StrictFrozenModel):
    """Bounded Phase A view of the released improvement `ResourceUsage` DTO."""

    cost_usd: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    compute_units: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    tokens: int = Field(strict=True, ge=0)
    elapsed_seconds: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    tool_calls: int = Field(strict=True, ge=0)
    human_interventions: int = Field(strict=True, ge=0)

    @field_validator("tokens", "tool_calls", "human_interventions")
    @classmethod
    def require_bounded_resource_integer(cls, value: int) -> int:
        return require_bounded_integer(
            value,
            error="resource usage integers exceed bound (1000-bit maximum)",
        )

    @classmethod
    def from_resource_usage(cls, usage: ResourceUsage | Self) -> Self:
        if type(usage) not in (ResourceUsage, cls):
            _raise_resource_usage_error()
        try:
            return cls.model_validate(usage.model_dump(mode="python", warnings=False), strict=True)
        except (AttributeError, TypeError, ValueError):
            pass
        _raise_resource_usage_error()

    @model_validator(mode="after")
    def require_bounded_outer_record(self) -> Self:
        require_canonical_byte_limit(
            self,
            maximum=MAX_RESOURCE_USAGE_CANONICAL_BYTES,
            error="resource usage canonical bytes exceed bound",
        )
        return self


__all__ = [
    "MAX_DECIMAL_ABS_EXPONENT",
    "MAX_DECIMAL_CANONICAL_BYTES",
    "MAX_DECIMAL_COEFFICIENT_DIGITS",
    "MAX_PHASE_A_IDENTIFIER_LENGTH",
    "MAX_PHASE_A_INTEGER",
    "MAX_PHASE_A_ITEMS",
    "PhaseAResourceUsage",
    "bounded_canonical_record_hash",
    "canonical_record_bytes",
    "require_bounded_decimal",
    "require_bounded_integer",
    "require_canonical_byte_limit",
]
