"""Money and quantity arithmetic conventions (plan 3.3, 5.1).

Quantities, prices, amounts, rates and costs are always
`decimal.Decimal`; floats are never used for them (the only exception
anywhere in the app is the web page's chart, which converts decimal
strings to numbers for pixel positions only, never for a displayed
figure). Computed values are quantised to 8 decimal places with
ROUND_HALF_EVEN; EUR amounts are rounded to 2 decimals with
ROUND_HALF_UP only when displayed or exported. Totals are always
computed from unrounded parts.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

MONEY_Q = Decimal("0.00000001")
EUR_Q = Decimal("0.01")

_PENCE_PER_POUND = Decimal(100)


def q8(x: Decimal) -> Decimal:
    """Quantise `x` to 8 decimal places, ROUND_HALF_EVEN (3.3). Use this for every stored value."""
    return x.quantize(MONEY_Q, rounding=ROUND_HALF_EVEN)


def eur2(x: Decimal) -> Decimal:
    """Round `x` to 2 decimals for display or export, ROUND_HALF_UP (3.3).

    Never use this for a value going into storage; only for what a
    person reads or a CSV export shows.
    """
    return x.quantize(EUR_Q, rounding=ROUND_HALF_UP)


def to_eur(amount: Decimal, currency: str, rate: Decimal | None) -> Decimal:
    """Convert `amount` in `currency` to EUR, using this app's one FX convention (3.3).

    `rate` is units of the foreign currency per 1 EUR (the ECB
    convention, also how Trade Republic prints it), so `EUR = amount /
    rate`. `GBX` (pence) is divided by 100 to pounds before the rate is
    applied. `currency == "EUR"` returns `amount` unchanged and ignores
    `rate` (which may then be `None`).

    Raises `ValueError` if `currency != "EUR"` and `rate` is `None` or
    not positive.
    """
    if currency == "EUR":
        return amount
    if rate is None:
        raise ValueError(f"to_eur: a rate is required to convert {currency} to EUR.")
    if rate <= 0:
        raise ValueError(f"to_eur: rate must be positive, got {rate}.")
    if currency == "GBX":
        amount = amount / _PENCE_PER_POUND
    return amount / rate


def allocate(total: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """Split `total` into `len(weights)` pieces proportional to `weights` (3.3: "Allocation").

    Each of the first `len(weights) - 1` pieces is its exact
    proportional share, quantised to 8 decimals; the LAST piece takes
    whatever is left over, so the pieces always sum to exactly `total`,
    even when `total` is not itself a multiple of 1e-8. Used to split a
    partial sell's cost, proceeds and fees across disposals (5.5).

    Raises `ValueError` if `weights` is empty, any weight is negative, or
    every weight is zero.
    """
    if not weights:
        raise ValueError("allocate: weights must not be empty.")
    if any(weight < 0 for weight in weights):
        raise ValueError("allocate: weights must not be negative.")
    weight_total = sum(weights, start=Decimal(0))
    if weight_total == 0:
        raise ValueError("allocate: weights must not all be zero.")

    pieces = [q8(total * weight / weight_total) for weight in weights[:-1]]
    pieces.append(q8(total - sum(pieces, start=Decimal(0))))
    return pieces
