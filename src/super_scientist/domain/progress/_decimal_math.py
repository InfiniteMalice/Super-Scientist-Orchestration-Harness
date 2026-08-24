from __future__ import annotations

from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal


def _exact_context(values: tuple[Decimal, ...]) -> Context:
    """Return a private context large enough for exact finite scalar arithmetic."""

    if any(not value.is_finite() for value in values):
        raise ValueError("progress arithmetic requires finite decimal values")
    nonzero = tuple(value for value in values if value)
    if not nonzero:
        precision = 1
    else:
        least_exponent = min(int(value.as_tuple().exponent) for value in nonzero)
        greatest_adjusted = max(value.adjusted() for value in nonzero)
        carry_digits = len(str(len(nonzero)))
        precision = max(1, greatest_adjusted - least_exponent + 1 + carry_digits)
    return Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
    )


def _sum_decimals(values: tuple[Decimal, ...]) -> Decimal:
    context = _exact_context(values)
    total = Decimal("0")
    for value in values:
        total = context.add(total, value)
    return total


def _subtract_decimals(left: Decimal, right: Decimal) -> Decimal:
    context = _exact_context((left, right))
    return context.subtract(left, right)


def _decimal_greater_than(left: Decimal, right: Decimal) -> bool:
    context = _exact_context((left, right))
    return context.compare(left, right) == Decimal("1")


__all__: list[str] = []
