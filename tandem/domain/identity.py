"""Immutable external-effect identity and canonical idempotency keys."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tandem.domain.money import Money, format_money

if TYPE_CHECKING:
    from tandem.domain.capability import CapabilityDefinition


def _render(template: str, inputs: dict[str, object]) -> str:
    rendered = template
    for key, value in inputs.items():
        rendered = rendered.replace(f"{{{{input.{key}}}}}", str(value))
    if "{{" in rendered or "}}" in rendered:
        raise ValueError(f"Unresolved effect identity template: {template}")
    return rendered.strip()


class EffectIdentity(BaseModel):
    """Complete, immutable identity of one externally observable effect."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    institution_id: str
    procedure_id: str
    case_id: str
    capability_id: str
    member_id: str
    account_id: str
    amount: Money
    currency: str = Field(min_length=3, max_length=3)
    business_reference: str

    @field_validator(
        "institution_id",
        "procedure_id",
        "case_id",
        "capability_id",
        "member_id",
        "account_id",
        "business_reference",
    )
    @classmethod
    def require_identity_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Effect identity values must not be empty")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != value.strip() or not normalized.isalpha():
            raise ValueError("Currency must be a three-letter uppercase code")
        return normalized

    @property
    def canonical_payload(self) -> dict[str, str]:
        return {
            "account_id": self.account_id,
            "amount": format_money(self.amount),
            "business_reference": self.business_reference,
            "capability_id": self.capability_id,
            "case_id": self.case_id,
            "currency": self.currency,
            "institution_id": self.institution_id,
            "member_id": self.member_id,
            "procedure_id": self.procedure_id,
        }

    @property
    def idempotency_key(self) -> str:
        canonical = json.dumps(
            self.canonical_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_capability(
        cls,
        capability: "CapabilityDefinition",
        inputs: dict[str, object],
    ) -> "EffectIdentity":
        spec = capability.effect.identity
        if spec is None:
            raise ValueError(f"COMMIT capability '{capability.id}' has no effect identity")
        return cls(
            institution_id=_render(spec.institution_id, inputs),
            procedure_id=_render(spec.procedure_id, inputs),
            case_id=_render(spec.case_id, inputs),
            capability_id=_render(spec.capability_id, inputs),
            member_id=_render(spec.member_id, inputs),
            account_id=_render(spec.account_id, inputs),
            amount=Decimal(_render(spec.amount, inputs)),
            currency=_render(spec.currency, inputs),
            business_reference=_render(spec.business_reference, inputs),
        )

