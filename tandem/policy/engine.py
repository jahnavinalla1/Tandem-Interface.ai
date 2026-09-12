"""High-level policy engine interface."""

from typing import Any, Dict, Optional

from tandem.domain.capability import CapabilityDefinition
from tandem.domain.errors import PolicyViolationError
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.policy.rules import validate_policy


class PolicyEngine:
    """Evaluates business rules and limits before capability invocation."""

    @staticmethod
    def evaluate(
        capability: CapabilityDefinition, inputs: Dict[str, Any]
    ) -> Optional[ExecutionOutcome]:
        try:
            validate_policy(capability, inputs)
            return None  # Policy passed
        except PolicyViolationError as e:
            return ExecutionOutcome(
                category=OutcomeCategory.BUSINESS_OUTCOME,
                code=OutcomeCode.POLICY_DENIED,
                message=str(e),
                details={"inputs": inputs, "capability_id": capability.id},
                money_moved=False,
            )
