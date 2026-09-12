"""Control-scoped identity, account, and monetary guards."""

from typing import Any, Dict, List, Optional

from tandem.domain.capability import CapabilityDefinition
from tandem.domain.errors import AmountMismatchError, EntityBindingMismatchError
from tandem.domain.money import parse_money
from tandem.surfaces.base import Surface, SurfaceOverlay


def verify_control_scoped_guard(
    capability: CapabilityDefinition,
    inputs: Dict[str, Any],
    surface: Surface,
    frame_selector: Optional[str] = None,
    overlay: Optional[SurfaceOverlay] = None,
    control_candidates: Optional[List[str]] = None,
    semantic_target: str = "Mutating submit control",
) -> None:
    """Verify that the immediate container/row holding the action control binds to expected inputs.

    Rejects whole-page assertions like 'member ID appears somewhere on this page'.
    Reads member ID, account ID, and amount directly from the control's enclosing parent container.
    """
    guard = capability.scoped_guard
    if not guard:
        return

    observed = surface.observe_container(
        container_selector=guard.container_selector,
        frame_selector=frame_selector,
        overlay=overlay,
        control_candidates=control_candidates,
        semantic_target=semantic_target,
    )

    def render(template: str) -> str:
        result = template
        for key, value in inputs.items():
            result = result.replace(f"{{{{input.{key}}}}}", str(value))
            result = result.replace(f"{{{{{key}}}}}", str(value))
        return result.strip()

    def require_identity(field: str, observed_value: Optional[str], template: str) -> None:
        expected = render(template)
        values = observed.submission_values.get(field, [])
        label = "member" if field == "member_id" else field
        if len(values) != 1 or not observed_value:
            raise EntityBindingMismatchError(
                f"Control-scoped guard failed closed: submitted {field} evidence is missing or ambiguous"
            )
        if observed_value.strip() != expected:
            raise EntityBindingMismatchError(
                f"Control-scoped guard failed: expected {label} '{expected}' "
                f"but observed '{observed_value}' in the submitted {field} control"
            )

    require_identity("case_id", observed.observed_case_id, guard.expected_case_template)
    require_identity("member_id", observed.observed_member_id, guard.expected_member_template)

    expected_amount = parse_money(render(guard.expected_amount_template))
    amount_values = observed.submission_values.get("amount", [])
    if len(amount_values) != 1 or observed.observed_amount is None:
        raise AmountMismatchError(
            "Control-scoped guard failed closed: submitted amount evidence is missing, ambiguous, or invalid"
        )
    if observed.observed_amount != expected_amount:
        raise AmountMismatchError(
            f"Control-scoped guard failed: expected amount {expected_amount:.2f}, "
            f"but observed submitted amount {observed.observed_amount:.2f}"
        )

    require_identity("account_id", observed.observed_account_id, guard.expected_account_template)
    require_identity("currency", observed.observed_currency, guard.expected_currency_template)
    require_identity(
        "institution_id",
        observed.observed_institution_id,
        guard.expected_institution_template,
    )
