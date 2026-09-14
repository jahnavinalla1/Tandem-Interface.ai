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

from copy import deepcopy
from pathlib import Path
from typing import Optional, Tuple

import yaml

from tandem.discovery.recorder import DiscoveryTrace
from tandem.domain.capability import (
    ArtifactDerivation,
    CapabilityDefinition,
    ScopedGuardSpec,
    StepAction,
    StepDefinition,
    load_capability_from_yaml,
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
        if not trace.money_moved or not trace.discovered_memo:
            raise ValueError(
                "Only a successful, receipt-confirmed discovery run can be compiled"
            )

        policy_profile_path = "capabilities/core/post_provisional_credit.yaml"
        policy_profile = load_capability_from_yaml(policy_profile_path)
        if (
            trace.capability_id != policy_profile.id
            or trace.system != policy_profile.system
        ):
            raise ValueError(
                "Discovery trace does not match the approved capability policy profile"
            )

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

        mutating_actions = [action for action in trace.actions if action.is_mutating]
        guard_container = (
            mutating_actions[-1].container_selector
            if mutating_actions and mutating_actions[-1].container_selector
            else "#credit_action_container, .confirm-panel"
        )
        approved_guard = policy_profile.scoped_guard
        assert approved_guard is not None
        scoped_guard = ScopedGuardSpec(
            guard_id=approved_guard.guard_id,
            container_selector=guard_container,
            expected_institution_template=approved_guard.expected_institution_template,
            expected_member_template=approved_guard.expected_member_template,
            expected_account_template=approved_guard.expected_account_template,
            expected_amount_template=approved_guard.expected_amount_template,
            expected_currency_template=approved_guard.expected_currency_template,
            expected_case_template=approved_guard.expected_case_template,
        )

        input_schema = deepcopy(policy_profile.input_schema)
        output_schema = deepcopy(policy_profile.output_schema)
        output_schema["properties"]["receipt_reference"]["x-selector"] = (
            "#receipt_memo_code, .result-memo-code"
        )
        output_schema["properties"]["money_moved"].update(
            {"x-selector": "#receipt_money_moved", "x-equals": "MONEY_MOVED=TRUE"}
        )
        output_schema["x-business-outcomes"] = [
            "MEMBER_NOT_FOUND",
            "ALREADY_APPLIED",
            "POLICY_DENIED",
        ]
        output_schema["x-interventions"] = [
            "COMPLIANCE_INTERSTITIAL",
            "POSTCHECK_UNCERTAIN",
        ]

        # Build definition
        capability = CapabilityDefinition(
            id=trace.capability_id,
            version=trace.version,
            created_at=trace.completed_at or trace.started_at,
            name=policy_profile.name,
            description=trace.goal,
            system=trace.system,
            effect=policy_profile.effect.model_copy(deep=True),
            input_schema=input_schema,
            output_schema=output_schema,
            scoped_guard=scoped_guard,
            steps=steps,
            source_discovery_run_id=trace.run_id,
            derivation=ArtifactDerivation(
                discovery_derived=[
                    "id",
                    "description",
                    "created_at",
                    "steps",
                    "effect.class",
                    "scoped_guard.container_selector",
                    "source_discovery_run_id",
                ],
                policy_profile=policy_profile_path,
                policy_derived=[
                    "effect.identity",
                    "effect.precheck",
                    "effect.postcheck",
                    "effect.reconciliation",
                    "effect.bounds",
                    "input_schema",
                    "output_schema",
                    "scoped_guard.expected_*",
                ],
                compiler_derived=["artifact_hash", "version", "schema_version"],
            ),
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
