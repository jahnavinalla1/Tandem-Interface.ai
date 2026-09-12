"""Unit tests for surface abstraction and overlays."""

from tandem.surfaces.base import ObservedControl, ObservedRecord, SurfaceOverlay
from tandem.surfaces.overlays import get_overlay, register_overlay


def test_overlay_candidate_resolution():
    overlay = SurfaceOverlay(
        institution_id="test_inst",
        name="Test Institution",
        selector_overrides={
            "Submit Button": [".custom-btn", "#btn-custom"],
        },
    )

    # Overridden target uses overlay candidates
    candidates = overlay.get_candidates("Submit Button", default_candidates=["#btn_default"])
    assert candidates == [".custom-btn", "#btn-custom"]

    # Non-overridden target falls back to default candidates
    candidates_default = overlay.get_candidates("Search Input", default_candidates=["#search_box"])
    assert candidates_default == ["#search_box"]


def test_overlay_registry():
    alpha = get_overlay("core_bank_alpha")
    assert alpha is not None
    assert alpha.institution_id == "alpha"

    beta = get_overlay("core_bank_beta")
    assert beta is not None
    assert "Final external credit actuation" in beta.selector_overrides

    custom = SurfaceOverlay(institution_id="custom_bank", name="Custom Bank")
    register_overlay(custom)
    assert get_overlay("custom_bank") == custom


def test_observed_models():
    ctrl = ObservedControl(
        name="Submit",
        resolved_selector=".btn-submit",
        tag_name="button",
        text_content="Submit",
    )
    assert ctrl.name == "Submit"
    assert ctrl.is_enabled is True

    record = ObservedRecord(
        container_selector="#panel",
        observed_member_id="8830142",
        observed_amount=340.00,
    )
    assert record.observed_member_id == "8830142"
    assert record.observed_amount == 340.00
