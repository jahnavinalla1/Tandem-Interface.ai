"""Integration test executing the full multi-system Reg E dispute workflow."""

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
def temp_session(tmp_path: Path):
    db_file = tmp_path / "test_workflow_e2e.db"
    engine = get_engine(str(db_file))
    init_db(engine)
    session_maker = get_session_factory(engine)
    with session_maker() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def reset_all_states():
    core_bank_state.seed()
    processor_state.reset()
    document_state.reset()


def test_full_reg_e_dispute_workflow_execution(temp_session):
    clock = datetime(2026, 9, 2, 9, 0, 0, tzinfo=timezone.utc)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        wf = RegEWorkflow(session=temp_session, page=page)
        res = wf.run_case(
            case_id="D-8842",
            member_id="8830142",
            amount=340.00,
            card_last4="4112",
            injected_clock=clock,
        )
        browser.close()

    assert res["status"] == "SUCCESS"
    assert res["state"] == "WAITING_RESOLUTION"
    assert res["money_moved"] is True
    assert res["latest_memo_ref"].startswith("MC-")

    # Verify money movement in core bank
    assert core_bank_state.members["8830142"].balance == 1580.50

    # Verify processor received chargeback
    cb = processor_state.find_by_case("D-8842")
    assert cb is not None
    assert cb.amount == 340.00

    # Verify notice dispatched
    notice = document_state.find_by_case("D-8842")
    assert notice is not None
    assert notice.status == "SENT"
    assert notice.amount == 340.00

    # Verify ledger state snapshot
    snapshot = wf.service.reconstruct_case_state("D-8842")
    assert snapshot.money_moved is True
    assert "core.post_provisional_credit" in snapshot.completed_capabilities
    assert "docs.send_notice" in snapshot.completed_capabilities
