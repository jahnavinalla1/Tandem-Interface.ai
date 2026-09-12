"""Registry and loader for institution-specific surface overlays."""

from typing import Dict, Optional

from tandem.surfaces.base import SurfaceOverlay

# Built-in institution overlays
OVERLAYS: Dict[str, SurfaceOverlay] = {
    "core_bank_alpha": SurfaceOverlay(
        institution_id="alpha",
        name="Symitar/Keystone Platform (Primary)",
        selector_overrides={},
        container_overrides={},
    ),
    "core_bank_beta": SurfaceOverlay(
        institution_id="beta",
        name="Symitar Legacy Platform (Older Skin / Second Institution)",
        selector_overrides={
            "Member Search Input": ["#legacy_search_box", "input[name='q']"],
            "Search Button": [".legacy-search-action"],
            "Post Provisional Credit Link": [".btn-action-legacy", "a.action-credit-btn", "text=Select & Adjust"],
            "Proceed to Confirmation": [".legacy-review-action"],
            "Final external credit actuation": [
                ".btn-commit-legacy",
                ".btn-commit-final",
                "button[type='submit']",
            ],
        },
        container_overrides={
            "#credit_action_container, .confirm-panel": ".legacy-confirm-box",
            "#commit_scope_container": ".legacy-confirm-box",
        },
    ),
}

OVERLAYS["alpha"] = OVERLAYS["core_bank_alpha"]
OVERLAYS["beta"] = OVERLAYS["core_bank_beta"]


def get_overlay(institution_id: str) -> Optional[SurfaceOverlay]:
    """Retrieve overlay by institution ID."""
    return OVERLAYS.get(institution_id)


def register_overlay(overlay: SurfaceOverlay) -> None:
    """Register custom surface overlay."""
    OVERLAYS[overlay.institution_id] = overlay
