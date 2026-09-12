"""End-to-end test verifying deterministic Playwright replay with zero LLM calls."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.policy.telemetry import llm_tracker
from tandem.replay.executor import DeterministicExecutor
from tests.server_utils import ensure_simulators_running


@pytest.fixture(scope="module", autouse=True)
def setup_simulators():
    ensure_simulators_running()


@pytest.fixture(autouse=True)
def reset_state():
    core_bank_state.seed()
    llm_tracker.reset()


def test_deterministic_replay_posts_credit_with_zero_llm_calls():
    # 1. Load compiled capability artifact
    yaml_path = Path("capabilities/core/post_provisional_credit.yaml")
    assert yaml_path.exists()
    capability = load_capability_from_yaml(str(yaml_path))

    # 2. Verify initial bank balance
    init_balance = core_bank_state.members["8830142"].balance
    assert init_balance == 1240.50

    # 3. Launch browser and execute deterministic replay
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        executor = DeterministicExecutor(page)
        outcome = executor.execute(
            capability=capability,
            inputs={
                "institution_id": "alpha",
                "member_id": "8830142",
                "account_id": "CHK-8830142-01",
                "case_id": "D-8842",
                "amount": 340.00,
                "currency": "USD",
            },
        )

        browser.close()

    # 4. Verify outcomes and strict invariants
    assert (
        outcome.category == OutcomeCategory.SUCCESS
    ), f"Replay failed: code={outcome.code}, message={outcome.message}, details={outcome.details}"
    assert outcome.code == OutcomeCode.COMPLETED
    assert outcome.money_moved is True
    assert outcome.audit_ref is not None
    assert outcome.audit_ref.startswith("MC-")

    # CRITICAL INVARIANT: Replay must execute with ZERO LLM calls!
    assert llm_tracker.call_count == 0, f"Expected 0 LLM calls, but got {llm_tracker.call_count}"

    # Verify money movement in bank backend
    new_balance = core_bank_state.members["8830142"].balance
    assert new_balance == 1580.50
    credit_record = core_bank_state.find_credit_by_case("D-8842")
    assert credit_record is not None
    assert credit_record.amount == 340.00
    assert credit_record.memo_code == outcome.audit_ref
