"""Playwright implementation of the Surface abstraction."""

from typing import Callable, List, Optional

from playwright.sync_api import Locator, Page

from tandem.domain.errors import PageDriftError, PolicyViolationError
from tandem.domain.money import parse_money
from tandem.surfaces.base import (
    ObservedControl,
    ObservedRecord,
    Surface,
    SurfaceOverlay,
)


class PlaywrightSurface(Surface):
    """Concrete browser surface driver using Playwright with container scoping and drift detection."""

    def __init__(self, page: Page):
        self.page = page
        self.drift_events: List[str] = []

    def _get_context(self, frame_selector: Optional[str] = None):
        """Return the target frame if present, or fallback to top-level page."""
        if frame_selector:
            try:
                if self.page.locator(frame_selector).count() > 0:
                    return self.page.frame_locator(frame_selector)
            except Exception:
                pass
        return self.page

    def _find_best_locator(
        self,
        context,
        candidates: List[str],
        semantic_target: str,
    ) -> tuple[Locator, str]:
        """Try locator candidates sequentially. Record drift if primary candidate fails."""
        for idx, selector in enumerate(candidates):
            try:
                matches = context.locator(selector)
                matches.first.wait_for(state="visible", timeout=5000 if idx == 0 else 2000)
                if matches.count() > 1:
                    raise PolicyViolationError(f"Ambiguous control for '{semantic_target}': {matches.count()} matches")
                loc = matches.first
                timeout = 5000 if idx == 0 else 2000
                if loc.is_visible(timeout=timeout):
                    if idx > 0:
                        drift_msg = (
                            f"Drift detected for '{semantic_target}': primary candidate '{candidates[0]}' "
                            f"failed; matched fallback candidate '{selector}' (rank {idx + 1})"
                        )
                        self.drift_events.append(drift_msg)
                    return loc, selector
            except PolicyViolationError:
                raise
            except Exception:
                continue

        raise PageDriftError(
            f"Surface failed to resolve control for '{semantic_target}'. "
            f"Tried candidates: {candidates}"
        )

    def navigate(self, url: str) -> None:
        self.page.goto(url, wait_until="networkidle")

    def resolve_and_click(
        self,
        semantic_target: str,
        candidates: List[str],
        frame_selector: Optional[str] = None,
        overlay: Optional[SurfaceOverlay] = None,
        before_click: Optional[Callable[[], None]] = None,
    ) -> ObservedControl:
        context = self._get_context(frame_selector)
        active_candidates = (
            overlay.get_candidates(semantic_target, candidates) if overlay else candidates
        )
        loc, selector = self._find_best_locator(context, active_candidates, semantic_target)

        text = (loc.text_content() or "").strip()
        tag = loc.evaluate("el => el.tagName.toLowerCase()") or "element"
        is_enabled = loc.is_enabled()

        if before_click:
            before_click()
        loc.click()
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

        return ObservedControl(
            name=semantic_target,
            resolved_selector=selector,
            tag_name=tag,
            text_content=text,
            is_enabled=is_enabled,
            is_visible=True,
        )

    def resolve_and_fill(
        self,
        semantic_target: str,
        candidates: List[str],
        value: str,
        frame_selector: Optional[str] = None,
        overlay: Optional[SurfaceOverlay] = None,
    ) -> ObservedControl:
        context = self._get_context(frame_selector)
        active_candidates = (
            overlay.get_candidates(semantic_target, candidates) if overlay else candidates
        )
        loc, selector = self._find_best_locator(context, active_candidates, semantic_target)

        loc.fill(value)

        return ObservedControl(
            name=semantic_target,
            resolved_selector=selector,
            tag_name="input",
            text_content=value,
            is_enabled=loc.is_enabled(),
            is_visible=True,
        )

    def observe_container(
        self,
        container_selector: str,
        frame_selector: Optional[str] = None,
        overlay: Optional[SurfaceOverlay] = None,
        control_candidates: Optional[List[str]] = None,
        semantic_target: str = "Mutating submit control",
    ) -> ObservedRecord:
        context = self._get_context(frame_selector)
        active_selector = (
            overlay.container_overrides.get(container_selector, container_selector)
            if overlay
            else container_selector
        )

        containers = context.locator(active_selector)
        if containers.count() > 1:
            raise PolicyViolationError('Ambiguous commit container')
        container_loc = containers.first
        try:
            container_loc.wait_for(state="visible", timeout=5000)
        except Exception:
            raise PageDriftError(f"Container element '{active_selector}' not visible on surface")

        raw_text = container_loc.inner_text() or ""
        candidates = control_candidates or ["button[type='submit']", "input[type='submit']"]
        active_candidates = (
            overlay.get_candidates(semantic_target, candidates) if overlay else candidates
        )
        control = None
        for candidate in active_candidates:
            matches = container_loc.locator(candidate)
            if matches.count() == 1 and matches.first.is_visible():
                control = matches.first
                break

        submission = None
        if control is not None:
            submission = control.evaluate(
                """control => {
                    const form = control.form;
                    if (!form) return null;
                    const values = {};
                    for (const element of form.elements) {
                        if (!element.name || element.disabled) continue;
                        const type = (element.type || '').toLowerCase();
                        if ((type === 'checkbox' || type === 'radio') && !element.checked) continue;
                        if (['button', 'reset', 'file'].includes(type)) continue;
                        if (type === 'submit' && element !== control) continue;
                        if (!values[element.name]) values[element.name] = [];
                        values[element.name].push(String(element.value));
                    }
                    return {
                        values,
                        action: form.action,
                        method: form.method.toUpperCase(),
                    };
                }"""
            )

        values = submission["values"] if submission else {}

        def one(name: str) -> Optional[str]:
            field_values = values.get(name, [])
            return field_values[0].strip() if len(field_values) == 1 else None

        amount_value = one("amount")
        observed_amount = None
        if amount_value is not None:
            try:
                observed_amount = parse_money(amount_value)
            except ValueError:
                observed_amount = None

        return ObservedRecord(
            container_selector=active_selector,
            observed_institution_id=one("institution_id"),
            observed_member_id=one("member_id"),
            observed_account_id=one("account_id"),
            observed_amount=observed_amount,
            observed_currency=one("currency"),
            observed_case_id=one("case_id"),
            submission_values=values,
            submission_action=submission["action"] if submission else None,
            submission_method=submission["method"] if submission else None,
            raw_text=raw_text,
        )
