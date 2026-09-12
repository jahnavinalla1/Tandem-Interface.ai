"""Effect typing and safety metadata specifications for Tandem capabilities."""

from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tandem.domain.money import Money


class EffectClass(str, Enum):
    """Classification of external side-effects."""

    READ = "READ"  # No external mutation
    STAGE = "STAGE"  # Mutable, reversible operation
    COMMIT = "COMMIT"  # Irreversible or materially consequential (moves money, files legal claim)


class EffectClaimStatus(str, Enum):
    """Durable lifecycle for an irreversible external effect."""

    CLAIMED = "CLAIMED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    UNCERTAIN = "UNCERTAIN"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"


class PrecheckSpec(BaseModel):
    """Specification for inspecting whether a declared effect has already occurred."""

    model_config = ConfigDict(extra="forbid")

    capability: str = Field(description="Capability ID or method used to precheck")
    params: Dict[str, Any] = Field(
        default_factory=dict, description="Template parameters for precheck"
    )
    if_found: str = Field(
        default="ALREADY_APPLIED", description="Outcome code when effect is found"
    )


class PostcheckSpec(BaseModel):
    """Specification for confirming an effect succeeded when immediate confirmation is unclear."""

    model_config = ConfigDict(extra="forbid")

    capability: str = Field(description="Capability ID or method used to postcheck")
    params: Dict[str, Any] = Field(
        default_factory=dict, description="Template parameters for postcheck"
    )
    expected_status: str = Field(
        default="CONFIRMED", description="Expected status value on confirmation"
    )


class ReconciliationSpec(BaseModel):
    """Specification for reconciling ambiguous execution (e.g. dropped network connection)."""

    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(default="POSTCHECK_OR_UNCERTAIN")
    max_inquiry_attempts: int = Field(default=2)


class EffectIdentitySpec(BaseModel):
    """Templates used to bind a COMMIT to a complete immutable business identity."""

    model_config = ConfigDict(extra="forbid")

    institution_id: str
    procedure_id: str
    case_id: str
    capability_id: str
    member_id: str
    account_id: str
    amount: str
    currency: str
    business_reference: str


class BoundsSpec(BaseModel):
    """Boundary constraints required on monetary and quantitative inputs."""

    model_config = ConfigDict(extra="forbid")

    max_amount: Money = Field(
        default=Decimal("500.00"),
        ge=Decimal("0.01"),
        description="Upper bound for automated credit",
    )
    currency: str = Field(default="USD", description="Permitted currency code")


class EffectSpec(BaseModel):
    """Comprehensive effect contract attached to every capability.

    Safety Rule: Every COMMIT capability MUST declare:
    - idempotency_key
    - precheck
    - postcheck
    - bounds
    Reject definitions missing required safety metadata.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    effect_class: EffectClass = Field(alias="class", description="Effect classification")
    identity: Optional[EffectIdentitySpec] = Field(
        default=None, description="Complete immutable effect-identity template"
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Idempotency key template e.g. regE:{{case_id}}:provisional_credit",
    )
    precheck: Optional[PrecheckSpec] = Field(default=None, description="Precheck specification")
    postcheck: Optional[PostcheckSpec] = Field(default=None, description="Postcheck specification")
    reconciliation: Optional[ReconciliationSpec] = Field(
        default=None, description="Reconciliation behavior"
    )
    compensation: Optional[str] = Field(
        default=None, description="Optional compensation capability ID (e.g. reverse credit)"
    )
    bounds: Optional[BoundsSpec] = Field(default=None, description="Value and policy bounds")

    @model_validator(mode="after")
    def validate_commit_safety_contract(self) -> "EffectSpec":
        if self.effect_class == EffectClass.COMMIT:
            missing = []
            if not self.idempotency_key:
                missing.append("idempotency_key")
            if not self.identity:
                missing.append("identity")
            if not self.precheck:
                missing.append("precheck")
            if not self.postcheck:
                missing.append("postcheck")
            if not self.reconciliation:
                missing.append("reconciliation")
            if not self.bounds:
                missing.append("bounds")

            if missing:
                raise ValueError(
                    f"COMMIT capability rejected: missing mandatory safety metadata: {', '.join(missing)}. "
                    "All COMMIT operations require identity, precheck, postcheck, "
                    "reconciliation, bounds, and idempotency key."
                )
        return self
