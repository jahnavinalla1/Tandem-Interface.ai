"""Capability Artifact Compiler.

Transforms exploratory DiscoveryTrace sessions into deterministic, versioned,
typed CapabilityDefinition YAML artifacts equipped with:
- Abstracted input templates
- Typed effect classification (COMMIT/READ)
- Container-scoped identity guards
- Precheck and postcheck definitions
- Bounded safety constraints
- Cryptographic SHA-256 artifact verification
"""

from decimal import Decimal
from pathlib import Path
from typing import Optional, Tuple

import yaml

from tandem.discovery.recorder import DiscoveryTrace
from tandem.domain.capability import (
    CapabilityDefinition,
    ScopedGuardSpec,
    StepAction,
    StepDefinition,
)
from tandem.domain.effects import (
    BoundsSpec,
    EffectClass,
    EffectIdentitySpec,
    EffectSpec,
    PostcheckSpec,
    PrecheckSpec,
    ReconciliationSpec,
)


class CapabilityCompiler:
    """Compiles recorded discovery traces into executable capability artifacts."""

    def __init__(self, output_dir: str = "capabilities/compiled"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def compile(
        self,
        trace: DiscoveryTrace,
        target_filename: Optional[str] = None,
    ) -> Tuple[CapabilityDefinition, Path]:
        """Compile a DiscoveryTrace into a validated CapabilityDefinition YAML artifact."""
        event_actions = [
            event.executed_action for event in trace.events if event.executed_action is not None
        ]
        if trace.events and event_actions != trace.actions:
            raise ValueError(
                "Discovery trace actions do not match its durable provider decision events"
            )
        if trace.provider == "manual" and trace.events:
            raise ValueError("Provider event trace cannot identify its provider as manual")

        steps: list[StepDefinition] = []
        for action in trace.actions:
            input_template = (
                f"{{{{input.{action.input_name}}}}}"
                if action.action == "FILL" and action.input_name
                else None
            )
            steps.append(
                StepDefinition(
                    step_id=action.step_id,
                    action=StepAction(action.action),
                    semantic_target=action.semantic_target,
                    locator_candidates=action.locator_candidates,
                    input_value_template=input_template,
                    frame_selector=action.frame_selector,
                    guard_ref=action.guard_ref,
                )
            )

        # Effect metadata synthesis
        effect_spec = EffectSpec.model_validate(
            {
                "class": EffectClass.COMMIT if trace.money_moved else EffectClass.READ,
                "idempotency_key": "regE:{{input.case_id}}:provisional_credit",
                "identity": EffectIdentitySpec(
                    institution_id="{{input.institution_id}}",
                    procedure_id="reg_e_dispute",
                    case_id="{{input.case_id}}",
                    capability_id=trace.capability_id,
                    member_id="{{input.member_id}}",
                    account_id="{{input.account_id}}",
                    amount="{{input.amount}}",
                    currency="{{input.currency}}",
                    business_reference="{{input.case_id}}",
                ),
                "precheck": PrecheckSpec(
                    capability="core.find_memo_by_case",
                    params={"case_id": "{{input.case_id}}"},
                    if_found="ALREADY_APPLIED",
                ),
                "postcheck": PostcheckSpec(
                    capability="core.find_memo_by_case",
                    params={"case_id": "{{input.case_id}}"},
                    expected_status="CONFIRMED",
                ),
                "reconciliation": ReconciliationSpec(
                    strategy="POSTCHECK_OR_UNCERTAIN",
                    max_inquiry_attempts=2,
                ),
                "compensation": "core.reverse_provisional_credit",
                "bounds": BoundsSpec(max_amount=Decimal("500.00"), currency="USD"),
            }
        )

        mutating_actions = [action for action in trace.actions if action.is_mutating]
        guard_container = (
            mutating_actions[-1].container_selector
            if mutating_actions and mutating_actions[-1].container_selector
            else "#credit_action_container, .confirm-panel"
        )
        scoped_guard = ScopedGuardSpec(
            guard_id="primary_commit_guard",
            container_selector=guard_container,
            expected_institution_template="{{input.institution_id}}",
            expected_member_template="{{input.member_id}}",
            expected_account_template="{{input.account_id}}",
            expected_amount_template="{{input.amount}}",
            expected_currency_template="{{input.currency}}",
            expected_case_template="{{input.case_id}}",
        )

        input_schema = {
            "type": "object",
            "properties": {
                "member_id": {"type": "string"},
                "account_id": {"type": "string"},
                "case_id": {"type": "string"},
                "amount": {"type": "number"},
                "currency": {"type": "string", "const": "USD"},
                "institution_id": {"type": "string"},
            },
            "required": [
                "institution_id",
                "member_id",
                "account_id",
                "case_id",
                "amount",
                "currency",
            ],
        }

        # Build definition
        capability = CapabilityDefinition(
            id=trace.capability_id,
            version=trace.version,
            created_at=trace.completed_at or trace.started_at,
            name="Post Provisional Credit",
            description=trace.goal,
            system=trace.system,
            effect=effect_spec,
            input_schema=input_schema,
            output_schema={
                "type": "object",
                "properties": {
                    "receipt_reference": {"type": "string", "x-selector": "#receipt_memo_code, .result-memo-code"},
                    "money_moved": {"type": "boolean", "x-selector": "#receipt_money_moved", "x-equals": "MONEY_MOVED=TRUE"},
                },
                "required": ["receipt_reference", "money_moved"],
                "x-business-outcomes": ["MEMBER_NOT_FOUND", "ALREADY_APPLIED", "POLICY_DENIED"],
                "x-interventions": ["COMPLIANCE_INTERSTITIAL", "POSTCHECK_UNCERTAIN"],
            },
            scoped_guard=scoped_guard,
            steps=steps,
            source_discovery_run_id=trace.run_id,
            supported_surfaces=[trace.system],
        )

        # Serialize to YAML (using aliases and json mode so Enums serialize as strings)
        capability.artifact_hash = capability.compute_hash()
        raw_dict = capability.model_dump(by_alias=True, mode="json")
        yaml_content = yaml.safe_dump(raw_dict, sort_keys=False)

        filename = target_filename or f"{trace.capability_id.replace('.', '_')}.yaml"
        output_file = self.output_dir / filename

        # Add comment header with hash
        header = (
            "# Compiled by Tandem CapabilityCompiler\n"
            f"# Canonical SHA-256: {capability.artifact_hash}\n\n"
        )
        output_file.write_text(header + yaml_content, encoding="utf-8")

        return capability, output_file
