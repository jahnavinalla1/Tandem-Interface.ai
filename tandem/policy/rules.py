"""Declarative policy definitions and bounds validation."""

from decimal import Decimal
from typing import Any, Dict

from tandem.domain.capability import CapabilityDefinition
from tandem.domain.effects import EffectClass
from tandem.domain.errors import PolicyViolationError
from tandem.domain.money import parse_money

MAX_AUTOMATED_CREDIT_AMOUNT = Decimal("500.00")
PERMITTED_CURRENCIES = {"USD"}


def validate_policy(capability: CapabilityDefinition, inputs: Dict[str, Any]) -> None:
    """Evaluate business policy rules prior to executing an action.

    Fails with PolicyViolationError if bounds or constraints are exceeded.
    """
    if capability.effect.effect_class == EffectClass.COMMIT:
        # 1. Amount bound validation
        if "amount" in inputs:
            try:
                amt = parse_money(inputs["amount"])
            except (ValueError, TypeError) as exc:
                raise PolicyViolationError(
                    f"Invalid monetary amount format: {inputs['amount']}"
                ) from exc

            max_bound = (
                capability.effect.bounds.max_amount
                if capability.effect.bounds
                else MAX_AUTOMATED_CREDIT_AMOUNT
            )
            if amt > max_bound:
                raise PolicyViolationError(
                    f"Policy denied: Requested amount ${amt:.2f} exceeds maximum automated "
                    f"limit of ${max_bound:.2f}. Requires manual executive authorization."
                )

            if amt <= 0:
                raise PolicyViolationError(
                    f"Policy denied: Amount must be positive, got ${amt:.2f}"
                )

        # 2. Currency check
        currency = inputs.get("currency", "USD")
        if currency not in PERMITTED_CURRENCIES:
            raise PolicyViolationError(
                f"Policy denied: Currency '{currency}' is not supported. Permitted: {PERMITTED_CURRENCIES}"
            )

        # 3. Guard presence check
        if not capability.scoped_guard:
            raise PolicyViolationError(
                f"Policy denied: Capability '{capability.id}' has COMMIT effect but declares no scoped guard."
            )
