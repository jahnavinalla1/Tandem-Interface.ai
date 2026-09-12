"""Canonical monetary parsing and validation for Tandem effect paths."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_MONEY = Decimal("9999999999999999.99")


def parse_money(value: Any) -> Decimal:
    """Return a finite, cent-quantized Decimal without binary-float arithmetic."""
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid monetary amounts")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Monetary amount must be finite")
        value = str(value)

    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid monetary amount: {value!r}") from exc

    if not amount.is_finite():
        raise ValueError("Monetary amount must be finite")
    if abs(amount) > MAX_MONEY:
        raise ValueError(f"Monetary amount exceeds supported range: {amount}")
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN)


def format_money(value: Any) -> str:
    """Format a validated monetary amount with exactly two fractional digits."""
    return f"{parse_money(value):.2f}"


Money = Annotated[Decimal, BeforeValidator(parse_money)]

