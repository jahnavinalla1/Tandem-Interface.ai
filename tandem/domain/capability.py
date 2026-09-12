"""Capability artifact schema and typed execution definitions."""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tandem.domain.effects import EffectClass, EffectSpec


class StepAction(str, Enum):
    """Supported deterministic browser interaction primitives."""

    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    FILL = "FILL"
    SELECT_FRAME = "SELECT_FRAME"
    WAIT_FOR = "WAIT_FOR"
    ASSERT_CONTAINER = "ASSERT_CONTAINER"
    READ_TEXT = "READ_TEXT"
    SUBMIT = "SUBMIT"
    HTTP_POST = "HTTP_POST"


class StepDefinition(BaseModel):
    """A single deterministic browser interaction step within a capability."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(description="Unique step identifier within the capability")
    action: StepAction = Field(description="Action primitive to execute")
    semantic_target: str = Field(description="Human/business description of the target element")
    locator_candidates: List[str] = Field(
        default_factory=list,
        description="Ordered list of robust locator strategies (CSS, text, XPath, role)",
    )
    input_value_template: Optional[str] = Field(
        default=None, description="Template string for input values, e.g. {{input.amount}}"
    )
    expected_text: Optional[str] = Field(
        default=None, description="Expected text content for verification steps"
    )
    frame_selector: Optional[str] = Field(
        default=None, description="Selector of parent iframe if nested inside a frame"
    )
    guard_ref: Optional[str] = Field(
        default=None,
        description="Required structural reference for an irreversible SUBMIT actuation",
    )


class ScopedGuardSpec(BaseModel):
    """Control-scoped guard specification ensuring action is bound to the correct entity."""

    model_config = ConfigDict(extra="forbid")

    guard_id: str = Field(
        default="primary_commit_guard",
        min_length=1,
        description="Stable identifier referenced by protected actuation steps",
    )
    container_selector: str = Field(
        description="Selector for the enclosing row, card, or panel holding the submit control"
    )
    expected_member_template: str = Field(
        default="{{input.member_id}}",
        description="Template for expected submitted member ID",
    )
    expected_account_template: str = Field(
        default="{{input.account_id}}",
        description="Template for expected submitted account ID",
    )
    expected_amount_template: str = Field(
        default="{{input.amount}}",
        description="Template for expected submitted monetary amount",
    )
    expected_currency_template: str = Field(
        default="{{input.currency}}",
        description="Template for expected submitted ISO currency",
    )
    expected_case_template: str = Field(
        default="{{input.case_id}}",
        description="Template for expected submitted case/dispute reference",
    )
    expected_institution_template: str = Field(
        default="{{input.institution_id}}",
        description="Template for expected submitted institution ID",
    )


class CapabilityDefinition(BaseModel):
    """Versioned, typed capability artifact compiled from discovery or manually specified."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(description="Unique capability identifier, e.g. core.post_provisional_credit")
    version: str = Field(default="1.0.0", description="Semantic version of capability artifact")
    schema_version: int = Field(default=1, description="Capability artifact schema version")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC creation time for this artifact",
    )
    name: str = Field(description="Descriptive name")
    description: str = Field(description="Purpose of capability")
    system: str = Field(description="Target system key: core_bank, processor, documents")
    effect: EffectSpec = Field(description="Effect contract and safety metadata")
    input_schema: Dict[str, Any] = Field(description="JSON schema for input arguments")
    output_schema: Dict[str, Any] = Field(
        default_factory=dict, description="JSON schema for outputs"
    )
    scoped_guard: Optional[ScopedGuardSpec] = Field(
        default=None, description="Container-scoped identity and amount guard"
    )
    steps: List[StepDefinition] = Field(
        default_factory=list, description="Deterministic browser interaction steps"
    )
    source_discovery_run_id: Optional[str] = Field(
        default=None, description="ID of discovery run that generated this artifact"
    )
    supported_surfaces: List[str] = Field(
        default_factory=list,
        description="Logical surface families compatible with this capability",
    )
    artifact_hash: Optional[str] = Field(
        default=None, description="SHA-256 hash of capability definition for integrity"
    )

    @model_validator(mode="after")
    def validate_capability_safety(self) -> "CapabilityDefinition":
        if self.schema_version != 1:
            raise ValueError(
                f"Unsupported capability schema version {self.schema_version}; expected 1"
            )
        if self.effect.effect_class == EffectClass.COMMIT:
            if not self.scoped_guard:
                raise ValueError(
                    f"Capability '{self.id}' has COMMIT effect but lacks a scoped_guard. "
                    f"All COMMIT operations require container-scoped identity verification."
                )
            if not self.steps:
                raise ValueError(f"COMMIT capability '{self.id}' must contain executable steps")
            actuations = [
                step
                for step in self.steps
                if step.action in {StepAction.SUBMIT, StepAction.HTTP_POST}
            ]
            if not actuations:
                raise ValueError(
                    f"COMMIT capability '{self.id}' must declare a SUBMIT or HTTP_POST actuation"
                )
            for actuation in actuations:
                if actuation.guard_ref != self.scoped_guard.guard_id:
                    raise ValueError(
                        f"COMMIT actuation '{actuation.step_id}' must reference guard "
                        f"'{self.scoped_guard.guard_id}'"
                    )
        return self

    def compute_hash(self) -> str:
        """Compute SHA-256 over every normalized field except the digest itself."""

        data = self.model_dump(by_alias=True, mode="json", exclude={"artifact_hash"})
        return compute_artifact_digest(data)


def canonical_artifact_bytes(data: Dict[str, Any]) -> bytes:
    """Serialize an artifact payload canonically for integrity verification."""

    normalized = dict(data)
    normalized.pop("artifact_hash", None)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def compute_artifact_digest(data: Dict[str, Any]) -> str:
    """Return the canonical artifact SHA-256 digest."""

    return hashlib.sha256(canonical_artifact_bytes(data)).hexdigest()


def load_capability_from_yaml(path: str) -> CapabilityDefinition:
    """Load a capability only after schema, target, and digest verification."""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Capability artifact must be a YAML mapping")
    declared_hash = data.get("artifact_hash")
    if not isinstance(declared_hash, str) or len(declared_hash) != 64:
        raise ValueError("Capability artifact integrity digest is missing or invalid")
    cap = CapabilityDefinition.model_validate(data)
    computed_hash = cap.compute_hash()
    if not hmac.compare_digest(declared_hash, computed_hash):
        raise ValueError(
            "Capability artifact integrity verification failed: digest mismatch"
        )
    if not cap.supported_surfaces or cap.system not in cap.supported_surfaces:
        raise ValueError(
            f"Capability surface '{cap.system}' is not present in supported_surfaces"
        )
    for step in cap.steps:
        if step.action == StepAction.NAVIGATE:
            expected = f"surface://{cap.system}/home"
            if step.semantic_target != expected:
                raise ValueError(
                    f"Navigation target is not an allowlisted logical surface: "
                    f"expected '{expected}'"
                )
    return cap
