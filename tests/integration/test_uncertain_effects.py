"""Integration test for Uncertain-Effect Handling and Postcheck Reconciliation (Scenario 8).

Verifies:
1. When a COMMIT operation encounters a dropped connection (e.g. 504 Gateway Timeout),
   Tandem does NOT blindly retry.
2. If postcheck inquiry confirms the mutation occurred, Tandem reconciles the state and advances.
3. If postcheck inquiry cannot confirm the effect, Tandem halts immediately, classifies as
   UNCERTAIN_EFFECT, records an escalation audit event, and routes to human oversight.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from simulators.processor.state import processor_state
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.replay.engine import EffectEngine
from tandem.workflow.reg_e import RegEWorkflow
from tests.server_utils import ensure_simulators_running, reset_all_simulators


@pytest.fixture(scope="module", autouse=True)
def setup_simulators():
    ensure_simulators_running()


@pytest.fixture
def temp_session(tmp_path: Path):
    db_file = tmp_path / "test_uncertain.db"
    engine = get_engine(str(db_file))
    init_db(engine)
    session_maker = get_session_factory(engine)
    with session_maker() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def reset_test_state():
    reset_all_simulators()
    yield
    reset_all_simulators()


def test_postcheck_reconciles_interrupted_chargeback_and_proceeds(temp_session):
    """Scenario 8A: 504 Gateway Timeout occurs on card processor, but postcheck confirms mutation landed."""
    clock = datetime(2026, 9, 2, 9, 0, 0, tzinfo=timezone.utc)
    case_id = "D-RECON-504"
    member_id = "8830142"
    amount = 340.00

    # Arm the card processor to drop connection AFTER saving the chargeback
    processor_state.timeout_after_submit = True

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        wf = RegEWorkflow(session=temp_session, page=page)
        res = wf.run_case(
            case_id=case_id,
            member_id=member_id,
            amount=amount,
            card_last4="4112",
            injected_clock=clock,
        )
        browser.close()

    # Workflow recognized 504, reconciled via postcheck inquiry, and completed!
    assert res["status"] == "SUCCESS"
    assert res["state"] == "WAITING_RESOLUTION"
    assert res["money_moved"] is True

    # Verify reconciliation event in ledger
    snapshot = wf.service.reconstruct_case_state(case_id)
    assert "processor.file_chargeback" in snapshot.completed_capabilities
    assert "core.post_provisional_credit" in snapshot.completed_capabilities

    events = wf.repo.get_events_for_case(case_id)
    event_types = [e.event_type for e in events]
    assert "EFFECT_RECONCILED" in event_types


def test_dropped_connection_without_confirmation_escalates_to_uncertain_effect(temp_session):
    """Scenario 8B: real browser timeout after commit plus failed inquiry is uncertain."""
    case_id = "D-UNCERTAIN-DROP"
    member_id = "8830142"
    amount = 340.00

    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    cap.steps = [cap.steps[-1]]
    core_bank_state.simulate_post_commit_delay_ms = 2000
    core_bank_state.fail_credit_lookup_when_present = True

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:8001/workspace/credit/entry?member_id={member_id}")
        page.locator("input[name='case_id']").fill(case_id)
        page.locator("input[name='amount']").fill(str(amount))
        page.locator("button.btn-proceed").click()
        page.set_default_timeout(500)
        page.set_default_navigation_timeout(500)

        engine = EffectEngine(session=temp_session, page=page)
        outcome = engine.execute_capability(
            capability=cap,
            inputs={"member_id": member_id, "case_id": case_id, "amount": amount},
        )
        browser.close()

    # INVARIANT: Must NOT blindly retry! Must classify as UNCERTAIN_EFFECT!
    assert outcome.category == OutcomeCategory.UNCERTAIN_EFFECT, outcome.model_dump()
    assert outcome.code == OutcomeCode.UNCERTAIN_EFFECT
    assert outcome.money_moved is False
    assert "Automatic retry is strictly forbidden" in outcome.message

    # Verify ledger state
    from tandem.ledger.service import LedgerService
    service = LedgerService(temp_session)
    snapshot = service.reconstruct_case_state(case_id)
    assert snapshot.status == "UNCERTAIN_EFFECT"
    assert snapshot.requires_human is True

    # Audit log records the escalation
    events = engine.repo.get_events_for_case(case_id)
    event_types = [e.event_type for e in events]
    assert "UNCERTAIN_EFFECT_ESCALATION" in event_types

    # The target did commit before its response became unavailable. A second run
    # remains fenced and cannot blindly repeat the COMMIT.
    assert core_bank_state.members[member_id].balance == 1580.50
    assert len(core_bank_state.credits) == 1
    core_bank_state.fail_credit_lookup_when_present = False
    second = engine.execute_capability(
        capability=cap,
        inputs={"member_id": member_id, "case_id": case_id, "amount": amount},
    )
    assert second.code == OutcomeCode.ALREADY_CLAIMED
    assert core_bank_state.members[member_id].balance == 1580.50
    assert len(core_bank_state.credits) == 1


def test_real_browser_timeout_after_commit_reconciles_exact_effect(temp_session):
    case_id = "D-REAL-BROWSER-RECON"
    member_id = "8830142"
    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    cap.steps = [cap.steps[-1]]
    core_bank_state.simulate_post_commit_delay_ms = 2000

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:8001/workspace/credit/entry?member_id={member_id}")
        page.locator("input[name='case_id']").fill(case_id)
        page.locator("input[name='amount']").fill("340.00")
        page.locator("button.btn-proceed").click()
        page.set_default_timeout(500)
        page.set_default_navigation_timeout(500)
        engine = EffectEngine(session=temp_session, page=page)
        outcome = engine.execute_capability(
            capability=cap,
            inputs={"member_id": member_id, "case_id": case_id, "amount": "340.00"},
        )
        browser.close()

    assert outcome.category == OutcomeCategory.SUCCESS, outcome.model_dump()
    assert outcome.code == OutcomeCode.CONFIRMED_APPLIED
    assert outcome.execution_phase.value == "SUBMIT_CONFIRMED"
    assert outcome.audit_ref is not None
    assert core_bank_state.members[member_id].balance == 1580.50
    assert len(core_bank_state.credits) == 1
