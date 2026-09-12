"""Integration tests for Human Handoff (Scenario 6).

Verifies:
1. Automation encounters compliance review interstitial.
2. Automation safely halts, yields single-owner lease, and transitions to NEEDS_HUMAN.
3. Operator claims lease (enforcing single-owner mutual exclusion).
4. Operator acknowledges compliance interstitial in the browser.
5. Operator yields lease back to AUTOMATION.
6. Automation safely resumes, posts credit, sends notice, and settles without double execution.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from tandem.domain.errors import LeaseConflictError
from tandem.handoff.coordinator import HandoffCoordinator
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.workflow.reg_e import RegEWorkflow
from tests.server_utils import ensure_simulators_running, reset_all_simulators


@pytest.fixture(scope="module", autouse=True)
def setup_simulators():
    ensure_simulators_running()


@pytest.fixture
def temp_session(tmp_path: Path):
    db_file = tmp_path / "test_handoff.db"
    engine = get_engine(str(db_file))
    init_db(engine)
    session_maker = get_session_factory(engine)
    with session_maker() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def reset_all_states():
    reset_all_simulators()
    yield
    reset_all_simulators()


def test_compliance_interstitial_triggers_handoff_and_resumption(temp_session):
    clock = datetime(2026, 9, 2, 9, 0, 0, tzinfo=timezone.utc)
    case_id = "D-HANDOFF-101"
    member_id = "8830142"
    amount = 340.00

    # 1. Arm the core banking simulator with compliance review interstitial
    core_bank_state.require_compliance_interstitial = True
    core_bank_state.compliance_cleared = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        wf = RegEWorkflow(session=temp_session, page=page)

        # -------------------------------------------------------------------
        # Phase 1: Automation runs until Compliance Interstitial halts it
        # -------------------------------------------------------------------
        res_initial = wf.run_case(
            case_id=case_id,
            member_id=member_id,
            amount=amount,
            card_last4="4112",
            injected_clock=clock,
        )

        assert res_initial["status"] == "NEEDS_HUMAN"
        assert res_initial["code"] == "COMPLIANCE_INTERSTITIAL"

        # Verify state: Money did NOT move!
        assert core_bank_state.members[member_id].balance == 1240.50
        assert len(core_bank_state.credits) == 0

        # Verify ledger state
        snapshot = wf.service.reconstruct_case_state(case_id)
        assert snapshot.status == "NEEDS_HUMAN"
        assert snapshot.money_moved is False
        assert snapshot.requires_human is True
        assert "core.post_provisional_credit" not in snapshot.completed_capabilities
        # Lease must have been released by automation
        assert snapshot.lease_owner is None

        # -------------------------------------------------------------------
        # Phase 2: Operator Takeover & Mutual-Exclusion Lease Enforcement
        # -------------------------------------------------------------------
        coordinator = HandoffCoordinator(session=temp_session)

        # Operator 1 claims lease
        operator_sarah = "operator_sarah_compliance"
        lease = coordinator.claim_operator_lease(case_id=case_id, operator_id=operator_sarah)
        assert lease.owner == operator_sarah

        # Operator 2 attempts concurrent takeover -> MUST BE REJECTED!
        with pytest.raises(LeaseConflictError) as exc_info:
            coordinator.claim_operator_lease(case_id=case_id, operator_id="operator_bob")
        assert "already held by operator" in str(exc_info.value)

        # Operator 1 reviews the interstitial screen and performs sign-off
        signoff_res = coordinator.operator_clear_compliance(
            case_id=case_id,
            operator_id=operator_sarah,
            lease_token=lease.fencing_token,
            page=page,
        )
        assert signoff_res["status"] == "COMPLIANCE_CLEARED"
        assert signoff_res["next_owner"] == "AUTOMATION"
        assert core_bank_state.compliance_cleared is True

        # -------------------------------------------------------------------
        # Phase 3: Automation Resumption & Workflow Completion
        # -------------------------------------------------------------------
        res_resumed = wf.run_case(
            case_id=case_id,
            member_id=member_id,
            amount=amount,
            card_last4="4112",
            injected_clock=clock,
        )
        browser.close()

    assert res_resumed["status"] == "SUCCESS"
    assert res_resumed["state"] == "WAITING_RESOLUTION"
    assert res_resumed["money_moved"] is True
    assert res_resumed["latest_memo_ref"].startswith("MC-")

    # Verify money movement occurred exactly once
    assert core_bank_state.members[member_id].balance == 1580.50
    assert len(core_bank_state.credits) == 1

    # Verify notice dispatched and notice deadline met
    final_snapshot = wf.service.reconstruct_case_state(case_id)
    assert "core.post_provisional_credit" in final_snapshot.completed_capabilities
    assert "docs.send_notice" in final_snapshot.completed_capabilities
    notice_deadlines = [d for d in final_snapshot.pending_deadlines if d.deadline_type == "NOTICE_2_DAY"]
    assert len(notice_deadlines) == 0  # NOTICE_2_DAY was met!
