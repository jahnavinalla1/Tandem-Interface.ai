"""Replay must never cross the discovery-provider boundary."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from tandem.discovery.provider import OpenAIResponsesProvider
from tandem.domain.capability import StepAction, load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.policy.telemetry import llm_tracker
from tandem.replay.executor import DeterministicExecutor
from tests.server_utils import ensure_simulators_running, reset_all_simulators


@pytest.fixture(scope="module", autouse=True)
def simulators() -> None:
    ensure_simulators_running()


@pytest.fixture(autouse=True)
def no_provider_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay attempted to invoke a discovery provider")

    monkeypatch.setattr(OpenAIResponsesProvider, "decide", forbidden)
    reset_all_simulators()
    llm_tracker.reset()


def _inputs(case_id: str) -> dict[str, object]:
    return {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": "CHK-8830142-01",
        "case_id": case_id,
        "amount": "340.00",
        "currency": "USD",
    }


def test_commit_replay_succeeds_with_provider_clients_forbidden() -> None:
    capability = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        outcome = DeterministicExecutor(browser.new_page()).execute(
            capability, _inputs("D-NO-MODEL-1")
        )
        browser.close()

    assert outcome.category == OutcomeCategory.SUCCESS
    assert outcome.code == OutcomeCode.COMPLETED
    assert llm_tracker.call_count == 0


def test_selector_drift_fails_without_provider_fallback() -> None:
    capability = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    drifted = capability.model_copy(deep=True)
    first_fill = next(step for step in drifted.steps if step.action == StepAction.FILL)
    first_fill.locator_candidates = ["#selector-that-does-not-exist"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        outcome = DeterministicExecutor(browser.new_page()).execute(
            drifted, _inputs("D-NO-MODEL-DRIFT")
        )
        browser.close()

    assert outcome.category == OutcomeCategory.RECOVERABLE_FAILURE
    assert outcome.code == OutcomeCode.PAGE_DRIFT
    assert llm_tracker.call_count == 0
