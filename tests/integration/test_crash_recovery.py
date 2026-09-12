"""Integration tests for crash injection and durable workflow recovery."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from simulators.documents.state import document_state
from simulators.processor.state import processor_state
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.workflow.reg_e import RegEWorkflow
from tests.server_utils import ensure_simulators_running


@pytest.fixture(scope="module", autouse=True)
def setup_simulators():
    ensure_simulators_running()


@pytest.fixture
def shared_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "crash_recovery_test.db")


@pytest.fixture(autouse=True)
def reset_all_states():
    core_bank_state.seed()
    processor_state.reset()
    document_state.reset()


def test_crash_mid_procedure_and_safe_resume(shared_db_path: str):
    """Demonstrates:

    1. Workflow runs and posts provisional credit (money moves).
    2. Process crashes before notice is sent.
    3. New process boundary restarts and loads SQLite ledger.
    4. State is reconstructed: money_moved=True, credit NOT repeated.
    5. Pending notice and deadline detected.
    6. Workflow completes notice dispatch and settles in WAITING_RESOLUTION.
    """
    # Anchored to the real clock (not a fixed historical date) so the NOTICE_2_DAY
    # deadline this creates stays safely in the future no matter when the suite
    # actually runs -- a fixed past date would eventually become genuinely overdue
    # by real wall-clock time and the assertion below would flip from PENDING.
    clock = datetime.now(timezone.utc)

    # =======================================================================
    # LIFETIME 1: Initial process running until crash injection
    # =======================================================================
    engine_1 = get_engine(shared_db_path)
    init_db(engine_1)
    session_factory_1 = get_session_factory(engine_1)

    crashed = False
    with session_factory_1() as session_1:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Enable kill_after_credit injection
            wf_1 = RegEWorkflow(session=session_1, page=page, kill_after_credit=True)

            run_res = None
            run_err = None
            try:
                run_res = wf_1.run_case(
                    case_id="D-CRASH-8842",
                    member_id="8830142",
                    amount=340.00,
                    card_last4="4112",
                    injected_clock=clock,
                )
            except Exception as err:
                run_err = err
                if "PROCESS_KILL_AFTER" in str(err):
                    crashed = True
            finally:
                browser.close()

    # Dispose of engine_1 completely: all Python state in process 1 is erased
    engine_1.dispose()
    del session_factory_1

    assert (
        crashed is True
    ), f"Expected process crash injection after provisional credit, got res={run_res}, err={run_err}"

    # Verify state of the outside world after crash:
    # Money moved! Balance increased by $340.00
    assert core_bank_state.members["8830142"].balance == 1580.50
    # BUT Notice was NOT sent before the crash!
    assert document_state.find_by_case("D-CRASH-8842") is None

    # =======================================================================
    # LIFETIME 2: Restart from persistent SQLite ledger in fresh process
    # =======================================================================
    engine_2 = get_engine(shared_db_path)
    session_factory_2 = get_session_factory(engine_2)

    with session_factory_2() as session_2:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Normal workflow (no kill switch)
            wf_2 = RegEWorkflow(session=session_2, page=page, kill_after_credit=False)

            # Inspect state before resumption
            snapshot_before = wf_2.service.reconstruct_case_state("D-CRASH-8842")
            assert snapshot_before.money_moved is True
            assert snapshot_before.latest_memo_ref is not None
            assert snapshot_before.latest_memo_ref.startswith("MC-")
            assert "core.post_provisional_credit" in snapshot_before.completed_capabilities
            assert "docs.send_notice" not in snapshot_before.completed_capabilities

            # Verify pending notice deadline survived restart
            notice_deadlines = [
                d for d in snapshot_before.pending_deadlines if d.deadline_type == "NOTICE_2_DAY"
            ]
            assert len(notice_deadlines) == 1
            assert notice_deadlines[0].status == "PENDING"
            # In total there are 3 deadlines: NOTICE_2_DAY, INVESTIGATION_10_DAY, FINAL_RESOLUTION_45_DAY
            assert len(snapshot_before.pending_deadlines) == 3

            # Resume the procedure
            res = wf_2.run_case(
                case_id="D-CRASH-8842",
                member_id="8830142",
                amount=340.00,
                card_last4="4112",
                injected_clock=clock,
            )
            browser.close()

        # Verify resumption outcome
        assert res["status"] == "SUCCESS"
        assert res["state"] == "WAITING_RESOLUTION"

        # Verify MONEY WAS NOT POSTED TWICE: Balance must still be exactly 1580.50!
        assert core_bank_state.members["8830142"].balance == 1580.50
        assert len(core_bank_state.credits) == 1

        # Verify notice was dispatched upon recovery
        notice = document_state.find_by_case("D-CRASH-8842")
        assert notice is not None
        assert notice.status == "SENT"

        # Verify notice deadline is now resolved (MET) in ledger
        snapshot_after = wf_2.service.reconstruct_case_state("D-CRASH-8842")
        assert "docs.send_notice" in snapshot_after.completed_capabilities
        notice_deadlines_after = [
            d for d in snapshot_after.pending_deadlines if d.deadline_type == "NOTICE_2_DAY"
        ]
        assert len(notice_deadlines_after) == 0  # NOTICE_2_DAY was met!
        # Investigation and Final Resolution deadlines remain pending
        assert len(snapshot_after.pending_deadlines) == 2

    engine_2.dispose()
