from __future__ import annotations

from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal

MAX_PROGRESS_DECIMAL_COEFFICIENT_DIGITS = 128
MAX_PROGRESS_DECIMAL_EXPONENT_MAGNITUDE = 384
MAX_PROGRESS_DECIMAL_REPRESENTATION_BYTES = 160
MAX_PROGRESS_DECIMAL_CONTEXT_PRECISION = 1_024
_MAX_PROGRESS_DERIVED_ADJUSTED_EXPONENT = (
    MAX_PROGRESS_DECIMAL_CONTEXT_PRECISION + MAX_PROGRESS_DECIMAL_EXPONENT_MAGNITUDE - 1
)
MAX_PROGRESS_DERIVED_DECIMAL_REPRESENTATION_BYTES = (
    MAX_PROGRESS_DECIMAL_CONTEXT_PRECISION
    + len(str(_MAX_PROGRESS_DERIVED_ADJUSTED_EXPONENT))
    + 5  # sign, point, E marker, exponent sign, and one carry character
)
_DECIMAL_BOUNDS_ERROR = "progress decimal scalar exceeds deterministic arithmetic bounds"


class ProgressDecimalValidationError(ValueError):
    """Fixed failure for Decimal values outside deterministic arithmetic bounds."""


def _require_decimal_envelope(
    value: Decimal,
    *,
    coefficient_digits: int,
    representation_bytes: int,
) -> Decimal:
    if not value.is_finite():
        raise ProgressDecimalValidationError(_DECIMAL_BOUNDS_ERROR) from None
    decimal_tuple = value.as_tuple()
    exponent = decimal_tuple.exponent
    if (
        not isinstance(exponent, int)
        or len(decimal_tuple.digits) > coefficient_digits
        or abs(exponent) > MAX_PROGRESS_DECIMAL_EXPONENT_MAGNITUDE
    ):
        raise ProgressDecimalValidationError(_DECIMAL_BOUNDS_ERROR) from None
    if len(str(value).encode("ascii")) > representation_bytes:
        raise ProgressDecimalValidationError(_DECIMAL_BOUNDS_ERROR) from None
    return value


def _require_bounded_decimal(value: Decimal) -> Decimal:
    """Validate one untrusted scalar before it enters progress arithmetic."""

    return _require_decimal_envelope(
        value,
        coefficient_digits=MAX_PROGRESS_DECIMAL_COEFFICIENT_DIGITS,
        representation_bytes=MAX_PROGRESS_DECIMAL_REPRESENTATION_BYTES,
    )


def _require_derived_decimal(value: Decimal) -> Decimal:
    """Validate an exact value derived solely from bounded ingress scalars."""

    return _require_decimal_envelope(
        value,
        coefficient_digits=MAX_PROGRESS_DECIMAL_CONTEXT_PRECISION,
        representation_bytes=MAX_PROGRESS_DERIVED_DECIMAL_REPRESENTATION_BYTES,
    )


def _context_for_validated(values: tuple[Decimal, ...]) -> Context:
    """Return a private context after every operand envelope has been checked."""

    nonzero = tuple(value for value in values if value)
    if not nonzero:
        precision = 1
    else:
        least_exponent = min(int(value.as_tuple().exponent) for value in nonzero)
        greatest_adjusted = max(value.adjusted() for value in nonzero)
        carry_digits = len(str(len(nonzero)))
        precision = max(1, greatest_adjusted - least_exponent + 1 + carry_digits)
    if precision > MAX_PROGRESS_DECIMAL_CONTEXT_PRECISION:
        raise ProgressDecimalValidationError(_DECIMAL_BOUNDS_ERROR) from None
    return Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
    )


def _exact_context(values: tuple[Decimal, ...]) -> Context:
    """Return an exact context for untrusted bounded scalar ingress."""

    validated = tuple(_require_bounded_decimal(value) for value in values)
    return _context_for_validated(validated)


def _sum_decimals(values: tuple[Decimal, ...]) -> Decimal:
    context = _exact_context(values)
    total = Decimal("0")
    for value in values:
        total = context.add(total, value)
    return _require_derived_decimal(total)


def _subtract_decimals(left: Decimal, right: Decimal) -> Decimal:
    context = _exact_context((left, right))
    return _require_derived_decimal(context.subtract(left, right))


def _derived_decimal_greater_than(left: Decimal, right: Decimal) -> bool:
    """Compare a trusted derived value with one bounded ingress scalar."""

    _require_derived_decimal(left)
    _require_bounded_decimal(right)
    return left > right


__all__ = [
    "MAX_PROGRESS_DECIMAL_COEFFICIENT_DIGITS",
    "MAX_PROGRESS_DECIMAL_CONTEXT_PRECISION",
    "MAX_PROGRESS_DECIMAL_EXPONENT_MAGNITUDE",
    "MAX_PROGRESS_DECIMAL_REPRESENTATION_BYTES",
    "MAX_PROGRESS_DERIVED_DECIMAL_REPRESENTATION_BYTES",
    "ProgressDecimalValidationError",
]
