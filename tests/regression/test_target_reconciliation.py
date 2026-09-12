"""Resume must reconcile Tandem's applied claims against the external target."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCode
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.replay.engine import EffectEngine
from tandem.workflow.reg_e import RegEWorkflow


def _apply_credit(session, page, case_id: str) -> None:
    capability = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    outcome = EffectEngine(session, page).execute_capability(
        capability,
        {"member_id": "8830142", "case_id": case_id, "amount": "340.00"},
    )
    assert outcome.is_success, outcome.model_dump()


def test_applied_ledger_claim_with_absent_target_requires_human(tmp_path: Path) -> None:
    engine = get_engine(str(tmp_path / "absent-target.db"))
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _apply_credit(session, page, "D-TARGET-ABSENT")
        core_bank_state.seed()

        result = RegEWorkflow(session, page).run_case(
            "D-TARGET-ABSENT", "8830142", "340.00"
        )
        browser.close()

    assert result["status"] == "NEEDS_HUMAN"
    assert result["code"] == OutcomeCode.TARGET_LEDGER_DIVERGENCE.value
    engine.dispose()


def test_applied_ledger_claim_with_mismatched_target_requires_human(tmp_path: Path) -> None:
    engine = get_engine(str(tmp_path / "mismatched-target.db"))
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _apply_credit(session, page, "D-TARGET-MISMATCH")
        with sqlite3.connect(core_bank_state.db_path) as connection:
            connection.execute(
                "UPDATE credits SET amount = '34.00' WHERE institution_id = ? AND case_id = ?",
                (core_bank_state.institution_id, "D-TARGET-MISMATCH"),
            )

        result = RegEWorkflow(session, page).run_case(
            "D-TARGET-MISMATCH", "8830142", "340.00"
        )
        browser.close()

    assert result["status"] == "NEEDS_HUMAN"
    assert result["code"] == OutcomeCode.TARGET_LEDGER_DIVERGENCE.value
    engine.dispose()
