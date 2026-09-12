"""Provider-directed exploratory browser discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Page

from tandem.config import settings
from tandem.discovery.provider import (
    BrowserObservation,
    DiscoveryAction,
    DiscoveryContext,
    DiscoveryDecision,
    DiscoveryProvider,
    OpenAIResponsesProvider,
)
from tandem.discovery.recorder import ActionTrace, DiscoveryTrace, TraceRecorder, redact_secrets
from tandem.domain.money import parse_money


class DiscoveryAgent:
    """Observe, ask a configured model for one action, execute it, and record evidence."""

    def __init__(
        self,
        page: Page,
        provider: DiscoveryProvider | None = None,
        evidence_root: str | Path = "evidence/discovery",
        max_cycles: int = 20,
    ) -> None:
        self.page = page
        self.provider = provider or OpenAIResponsesProvider(
            api_key=settings.openai_api_key,
            model=settings.discovery_model,
        )
        self.evidence_root = Path(evidence_root)
        self.max_cycles = max_cycles

    def discover_provisional_credit(
        self,
        inputs: dict[str, Any],
        portal_url: str | None = None,
    ) -> DiscoveryTrace:
        """Discover the provisional-credit flow through provider-selected actions."""

        typed_inputs = self._typed_inputs(inputs)
        url = portal_url or settings.core_bank_url
        objective = (
            "Navigate the core banking portal, locate the member, open the provisional-credit "
            "flow, bind the case and amount, review the exact submitted values, commit once, "
            "and finish only after observing the receipt."
        )
        recorder = TraceRecorder(
            capability_id="core.post_provisional_credit",
            goal=objective,
            system="core_bank",
            provider=self.provider.provider_name,
            model=self.provider.model,
            evidence_root=self.evidence_root,
        )

        finished = False
        for _ in range(self.max_cycles):
            observation = self._observe()
            context = DiscoveryContext(
                objective=objective,
                inputs=redact_secrets(typed_inputs),
                observation=observation,
                prior_events=[
                    {
                        "cycle": event.cycle,
                        "decision": event.model_decision.model_dump(mode="json"),
                        "result": event.result,
                    }
                    for event in recorder.events[-8:]
                ],
                allowed_surfaces=[url],
            )
            decision = self.provider.decide(context)
            action, result = self._execute_decision(
                decision=decision,
                inputs=typed_inputs,
                portal_url=url,
                recorder=recorder,
            )
            screenshot = self._safe_screenshot()
            recorder.record_cycle(
                observation=observation,
                decision=decision,
                executed_action=action,
                result=result,
                screenshot=screenshot,
            )
            if decision.action == DiscoveryAction.FINISH:
                finished = True
                break

        if not finished:
            raise RuntimeError(f"Discovery provider did not finish within {self.max_cycles} cycles")

        memo, money_moved = self._read_receipt()
        return recorder.finalize(
            discovered_memo=memo,
            money_moved=money_moved,
            metadata={
                "source_member_id": typed_inputs["member_id"],
                "target_account_id": typed_inputs["account_id"],
                "amount": typed_inputs["amount"],
                "currency": typed_inputs["currency"],
                "final_receipt_reference": memo,
            },
        )

    @staticmethod
    def _typed_inputs(inputs: dict[str, Any]) -> dict[str, str]:
        required = {"member_id", "case_id", "amount"}
        missing = sorted(required - inputs.keys())
        if missing:
            raise ValueError(f"Missing discovery inputs: {', '.join(missing)}")
        amount = parse_money(inputs["amount"])
        return {
            "institution_id": str(inputs.get("institution_id", "alpha")),
            "member_id": str(inputs["member_id"]),
            "account_id": str(inputs.get("account_id", f"CHK-{inputs['member_id']}-01")),
            "case_id": str(inputs["case_id"]),
            "amount": f"{amount:.2f}",
            "currency": str(inputs.get("currency", "USD")),
        }

    def _observe(self) -> BrowserObservation:
        try:
            title = self.page.title()
        except Exception:
            title = ""
        body_parts: list[str] = []
        interactive: list[str] = []
        frame_summaries: list[str] = []
        for frame in self.page.frames:
            try:
                frame_text = frame.locator("body").inner_text(timeout=1000)[:6000]
                if frame_text:
                    body_parts.append(frame_text)
                elements = frame.locator("a, button, input, select, textarea")
                count = min(elements.count(), 100)
                for index in range(count):
                    summary = elements.nth(index).evaluate(
                        """el => ({
                            tag: el.tagName.toLowerCase(), id: el.id || null,
                            name: el.getAttribute('name'), type: el.getAttribute('type'),
                            text: (el.innerText || el.getAttribute('aria-label') || '').trim(),
                            value: el.tagName === 'INPUT' &&
                                !['password', 'hidden'].includes((el.type || '').toLowerCase())
                                ? el.value : null
                        })"""
                    )
                    interactive.append(str(summary)[:1000])
                if frame != self.page.main_frame:
                    frame_summaries.append(f"url={frame.url} elements={count}")
            except Exception:
                continue
        return BrowserObservation(
            url=self.page.url,
            title=title,
            body_text="\n".join(body_parts)[:12000],
            interactive_elements=interactive,
            frame_summaries=frame_summaries,
        )

    def _execute_decision(
        self,
        decision: DiscoveryDecision,
        inputs: dict[str, str],
        portal_url: str,
        recorder: TraceRecorder,
    ) -> tuple[ActionTrace | None, dict[str, Any]]:
        if decision.action == DiscoveryAction.FINISH:
            memo, money_moved = self._read_receipt()
            return None, {
                "status": "finished",
                "receipt_reference": memo,
                "money_moved": money_moved,
            }
        if decision.action == DiscoveryAction.NAVIGATE:
            target_url = decision.target_url or ""
            if self._origin(target_url) != self._origin(portal_url):
                raise ValueError("Discovery provider requested navigation outside allowed origin")
            self.page.goto(target_url, wait_until="networkidle")
            action = recorder.record_navigate(
                target_url,
                semantic_target=f"surface://{recorder.system}/home",
            )
            return action, {"status": "executed", "url": self.page.url}

        selector = decision.selector
        if selector is None:
            raise ValueError(f"{decision.action.value} decision omitted selector")
        context = (
            self.page.frame_locator(decision.frame_selector)
            if decision.frame_selector
            else self.page
        )
        locator = context.locator(selector).first
        if decision.action == DiscoveryAction.FILL:
            input_name = decision.input_name or ""
            if input_name not in inputs:
                raise ValueError(f"Provider requested undeclared input '{input_name}'")
            value = inputs[input_name]
            locator.fill(value)
            action = recorder.record_fill(
                selector=selector,
                value=value,
                input_name=input_name,
                semantic_target=decision.semantic_target,
                frame_selector=decision.frame_selector,
                locator_candidates=decision.locator_candidates,
            )
            return action, {"status": "executed", "input_name": input_name}

        observed_text = None
        if decision.container_selector:
            observed_text = context.locator(decision.container_selector).first.inner_text()
        locator.click()
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        action = recorder.record_click(
            selector=selector,
            semantic_target=decision.semantic_target,
            frame_selector=decision.frame_selector,
            locator_candidates=decision.locator_candidates,
            container_selector=decision.container_selector,
            observed_text=observed_text,
            is_mutating=decision.action == DiscoveryAction.SUBMIT,
            guard_ref=decision.guard_ref,
        )
        memo, money_moved = self._read_receipt()
        return action, {
            "status": "executed",
            "receipt_reference": memo,
            "money_moved": money_moved,
        }

    def _read_receipt(self) -> tuple[str | None, bool]:
        for frame in self.page.frames:
            try:
                memo_locator = frame.locator("#receipt_memo_code, .result-memo-code").first
                if memo_locator.count() and memo_locator.is_visible(timeout=500):
                    memo = (memo_locator.text_content() or "").strip() or None
                    moved_text = (
                        frame.locator("#receipt_money_moved").first.text_content(timeout=500) or ""
                    )
                    return memo, "MONEY_MOVED=TRUE" in moved_text
            except Exception:
                continue
        return None, False

    def _safe_screenshot(self) -> bytes | None:
        try:
            return self.page.screenshot(full_page=True)
        except Exception:
            return None

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parsed = urlsplit(url)
        return parsed.scheme, parsed.hostname or "", parsed.port
