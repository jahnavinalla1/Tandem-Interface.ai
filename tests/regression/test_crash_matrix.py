"""Hard process-death matrix around every COMMIT in the Reg E procedure.

Each row kills the worker with ``os._exit(86)`` at a named durable-write
boundary, inspects the three external simulators and the ledger from the
parent process, then resumes in a fresh process and requires exactly one
target effect per capability plus a satisfied notice obligation.
"""

from __future__ import annotations

import multiprocessing
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from simulators.core_bank.state import core_bank_state
from simulators.documents.state import document_state
from simulators.processor.state import processor_state
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tandem.workflow.reg_e import RegEWorkflow

CORE = "core.post_provisional_credit"
PROCESSOR = "processor.file_chargeback"
NOTICE = "docs.send_notice"


@dataclass(frozen=True)
class CrashRow:
    label: str
    point: str
    capability: str | None
    obligation_expected: bool
    chargeback_expected: bool
    credit_expected: bool
    notice_expected: bool


CRASH_ROWS = [
    CrashRow("A_BEFORE_OBLIGATION", "A_BEFORE_OBLIGATION", None, False, False, False, False),
    CrashRow("B_AFTER_OBLIGATION", "B_AFTER_OBLIGATION", None, True, False, False, False),
    CrashRow("C_AFTER_CLAIM", "C_AFTER_CLAIM", CORE, True, True, False, False),
    CrashRow("D_AFTER_PRECHECK", "D_AFTER_PRECHECK", CORE, True, True, False, False),
    CrashRow("E_AFTER_GUARD", "E_AFTER_GUARD", CORE, True, True, False, False),
    CrashRow("F_BEFORE_SUBMIT", "F_BEFORE_SUBMIT", CORE, True, True, False, False),
    CrashRow("G_AFTER_TARGET_ACCEPTS", "G_AFTER_TARGET_ACCEPTS", CORE, True, True, True, False),
    CrashRow("H_BEFORE_POSTCHECK", "H_BEFORE_POSTCHECK", CORE, True, True, True, False),
    CrashRow("I_AFTER_POSTCHECK", "I_AFTER_POSTCHECK", CORE, True, True, True, False),
    CrashRow(
        "J_BEFORE_FINAL_LEDGER_EVENT", "J_BEFORE_FINAL_LEDGER_EVENT", CORE, True, True, True, False
    ),
    CrashRow("K_BEFORE_NOTICE", "K_BEFORE_NOTICE", None, True, True, True, False),
    CrashRow("L_AFTER_NOTICE_ACCEPTED", "G_AFTER_TARGET_ACCEPTS", NOTICE, True, True, True, True),
    CrashRow("M_AFTER_NOTICE_POSTCHECK", "I_AFTER_POSTCHECK", NOTICE, True, True, True, True),
    CrashRow(
        "N_BEFORE_NOTICE_LEDGER_EVENT", "J_BEFORE_FINAL_LEDGER_EVENT", NOTICE, True, True, True, True
    ),
    CrashRow("P_AFTER_PROCESSOR_CLAIM", "C_AFTER_CLAIM", PROCESSOR, True, False, False, False),
    CrashRow(
        "Q_AFTER_PROCESSOR_ACCEPTS", "G_AFTER_TARGET_ACCEPTS", PROCESSOR, True, True, False, False
    ),
    CrashRow(
        "R_BEFORE_PROCESSOR_LEDGER_EVENT",
        "J_BEFORE_FINAL_LEDGER_EVENT",
        PROCESSOR,
        True,
        True,
        False,
        False,
    ),
]


def _crashing_workflow(db_path: str, case_id: str, point: str, capability: str | None) -> None:
    os.environ["TANDEM_CRASH_POINT"] = point
    if capability:
        os.environ["TANDEM_CRASH_CAPABILITY"] = capability
    else:
        os.environ.pop("TANDEM_CRASH_CAPABILITY", None)
    engine = get_engine(db_path)
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        RegEWorkflow(session, browser.new_page()).run_case(
            case_id,
            "8830142",
            "340.00",
            # Real clock, not a fixed historical date: keeps NOTICE_2_DAY safely in
            # the future regardless of when the suite actually runs.
            injected_clock=datetime.now(timezone.utc),
        )
        browser.close()


def _recover_workflow(db_path: str, case_id: str, queue) -> None:
    os.environ.pop("TANDEM_CRASH_POINT", None)
    os.environ.pop("TANDEM_CRASH_CAPABILITY", None)
    engine = get_engine(db_path)
    init_db(engine)
    factory = get_session_factory(engine)
    try:
        with factory() as session, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            result = RegEWorkflow(session, browser.new_page()).run_case(
                case_id,
                "8830142",
                "340.00",
                # Real clock, not a fixed historical date: keeps NOTICE_2_DAY safely in
            # the future regardless of when the suite actually runs.
            injected_clock=datetime.now(timezone.utc),
            )
            browser.close()
            queue.put(result)
    except Exception as exc:
        queue.put({"status": "EXCEPTION", "error": repr(exc)})
    finally:
        engine.dispose()


@pytest.mark.parametrize("row", CRASH_ROWS, ids=[row.label for row in CRASH_ROWS])
def test_hard_process_death_preserves_safety_and_recovers(row: CrashRow, tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    case_id = f"D-CRASH-{row.label[0]}"
    db_path = str(tmp_path / f"{row.label}.db")
    process = context.Process(
        target=_crashing_workflow, args=(db_path, case_id, row.point, row.capability)
    )
    process.start()
    process.join(timeout=90)
    assert process.exitcode == 86

    engine = get_engine(db_path)
    factory = get_session_factory(engine)
    with factory() as session:
        repo = LedgerRepository(session)
        obligations = repo.get_obligations_for_case(case_id)
    engine.dispose()

    target_chargeback = processor_state.find_by_case(case_id)
    target_credit = core_bank_state.find_credit_by_case(case_id)
    target_notice = document_state.find_by_case(case_id)
    assert (len(obligations) == 1) is row.obligation_expected, row
    assert (target_chargeback is not None) is row.chargeback_expected, row
    assert (target_credit is not None) is row.credit_expected, row
    assert (target_notice is not None) is row.notice_expected, row
    if target_credit is not None:
        assert obligations, "money moved without a durable notice obligation"

    queue = context.Queue()
    recovery = context.Process(target=_recover_workflow, args=(db_path, case_id, queue))
    recovery.start()
    recovery.join(timeout=90)
    assert recovery.exitcode == 0
    result = queue.get(timeout=5)
    assert result["status"] == "SUCCESS", result
    assert processor_state.effect_count(case_id) == 1
    assert core_bank_state.effect_count(case_id) == 1
    assert document_state.effect_count(case_id) == 1

    reopened = get_engine(db_path)
    reopened_factory = get_session_factory(reopened)
    with reopened_factory() as session:
        repo = LedgerRepository(session)
        final_obligations = repo.get_obligations_for_case(case_id)
        final_claims = {
            claim.capability_id: claim.status for claim in repo.get_effect_claims_for_case(case_id)
        }
        deadlines = {d.deadline_type: d.status for d in repo.get_deadlines_for_case(case_id)}
        final_status = repo.get_case(case_id).status
    reopened.dispose()
    assert len(final_obligations) == 1
    assert final_obligations[0].status == "SATISFIED"
    assert final_claims == {PROCESSOR: "APPLIED", CORE: "APPLIED", NOTICE: "APPLIED"}
    assert deadlines["NOTICE_2_DAY"] == "MET"
    assert final_status == "WAITING_RESOLUTION"
