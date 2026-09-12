"""Immutable, hash-chained procedure events are the authoritative history.

Audit finding H-09: the "append-only ledger" was a set of mutable rows with
cascade deletes. These contracts require sequenced, hash-chained events that
the database itself refuses to update or delete, and mutable projections that
can be destroyed and rebuilt from those events alone.
"""

from __future__ import annotations

import multiprocessing
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from sqlalchemy.exc import IntegrityError

from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.ledger.models import ProcedureEventRecord
from tandem.ledger.repository import GENESIS_HASH, LedgerRepository
from tandem.workflow.reg_e import RegEWorkflow


@pytest.fixture
def ledger_path(tmp_path: Path) -> str:
    path = str(tmp_path / "events.db")
    engine = get_engine(path)
    init_db(engine)
    engine.dispose()
    return path


def _session(path: str):
    engine = get_engine(path)
    return engine, get_session_factory(engine)()


def test_events_carry_sequence_identity_and_hash_chain(ledger_path: str) -> None:
    engine, session = _session(ledger_path)
    repo = LedgerRepository(session)
    repo.create_or_get_case("D-CHAIN", "8830142", Decimal("340.00"))
    repo.record_event("D-CHAIN", "CAPABILITY_REQUESTED", "core.post_provisional_credit")
    repo.record_event("D-CHAIN", "GUARD_VERIFIED", "core.post_provisional_credit", payload={"ok": 1})
    session.commit()

    events = repo.get_events_for_case("D-CHAIN")
    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[0].event_type == "CASE_CREATED"
    assert len({event.event_id for event in events}) == 3
    assert events[0].previous_event_hash == GENESIS_HASH
    assert events[1].previous_event_hash == events[0].event_hash
    assert events[2].previous_event_hash == events[1].event_hash
    assert all(len(event.event_hash) == 64 for event in events)
    assert all(event.created_at.endswith("+00:00") for event in events)
    verification = repo.verify_event_chain("D-CHAIN")
    assert verification.valid, verification
    session.close()
    engine.dispose()


def test_audit_events_cannot_be_updated_or_deleted(ledger_path: str) -> None:
    engine, session = _session(ledger_path)
    repo = LedgerRepository(session)
    repo.create_or_get_case("D-IMMUTABLE", "8830142", Decimal("340.00"))
    session.commit()
    session.close()
    engine.dispose()

    with sqlite3.connect(ledger_path) as raw:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            raw.execute("UPDATE procedure_events SET payload = '{}' WHERE case_id = 'D-IMMUTABLE'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            raw.execute("DELETE FROM procedure_events WHERE case_id = 'D-IMMUTABLE'")

    engine, session = _session(ledger_path)
    event = session.query(ProcedureEventRecord).filter_by(case_id="D-IMMUTABLE").one()
    event.event_type = "FORGED"
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.delete(session.query(ProcedureEventRecord).filter_by(case_id="D-IMMUTABLE").one())
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.close()
    engine.dispose()


def test_case_deletion_does_not_cascade_audit_events(ledger_path: str) -> None:
    engine, session = _session(ledger_path)
    repo = LedgerRepository(session)
    case = repo.create_or_get_case("D-NO-CASCADE", "8830142", Decimal("340.00"))
    session.commit()
    session.delete(case)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    assert len(repo.get_events_for_case("D-NO-CASCADE")) == 1
    session.close()
    engine.dispose()


def test_tampered_event_is_detected_by_chain_verification(ledger_path: str) -> None:
    engine, session = _session(ledger_path)
    repo = LedgerRepository(session)
    repo.create_or_get_case("D-TAMPER", "8830142", Decimal("340.00"))
    repo.record_event("D-TAMPER", "MONEY_MOVED", "core", payload={"amount": "340.00"})
    repo.record_event("D-TAMPER", "NOTICE_SENT", "docs", payload={"ref": "NOT-1"})
    session.commit()
    session.close()
    engine.dispose()

    # A privileged attacker with raw file access can drop the guard triggers;
    # the hash chain must still expose the edit.
    with sqlite3.connect(ledger_path) as raw:
        raw.execute("DROP TRIGGER procedure_events_append_only_update")
        raw.execute(
            "UPDATE procedure_events SET payload = ? WHERE case_id = ? AND sequence = 2",
            ('{"amount":"34.00"}', "D-TAMPER"),
        )
        raw.commit()

    engine, session = _session(ledger_path)
    verification = LedgerRepository(session).verify_event_chain("D-TAMPER")
    assert verification.valid is False
    assert verification.broken_sequence == 2
    session.close()
    engine.dispose()


def _append_worker(path: str, case_id: str, barrier, count: int, owner: str) -> None:
    engine = get_engine(path)
    factory = get_session_factory(engine)
    try:
        barrier.wait(timeout=10)
        for index in range(count):
            with factory() as session:
                LedgerRepository(session).record_event(
                    case_id, "CONCURRENT_APPEND", owner, payload={"index": index}
                )
                session.commit()
    finally:
        engine.dispose()


def test_event_sequence_is_atomic_across_processes(ledger_path: str) -> None:
    engine, session = _session(ledger_path)
    LedgerRepository(session).create_or_get_case("D-APPEND-RACE", "8830142", Decimal("340.00"))
    session.commit()
    session.close()
    engine.dispose()

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    workers = [
        context.Process(
            target=_append_worker, args=(ledger_path, "D-APPEND-RACE", barrier, 15, f"worker-{i}")
        )
        for i in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)
        assert worker.exitcode == 0

    engine, session = _session(ledger_path)
    repo = LedgerRepository(session)
    events = repo.get_events_for_case("D-APPEND-RACE")
    assert [event.sequence for event in events] == list(range(1, 32))
    assert repo.verify_event_chain("D-APPEND-RACE").valid
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Phase 17: projections are derivable from events
# ---------------------------------------------------------------------------


def _run_full_procedure(path: str, case_id: str) -> None:
    engine, session = _session(path)
    with sync_playwright() as playwright:
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
    assert result["status"] == "SUCCESS", result
    session.close()
    engine.dispose()


def test_projections_rebuild_from_events_after_destruction(ledger_path: str) -> None:
    from tandem.ledger.rebuild import rebuild_case, snapshot_projections

    case_id = "D-REBUILD"
    _run_full_procedure(ledger_path, case_id)

    engine, session = _session(ledger_path)
    before = snapshot_projections(session, case_id)
    assert before["case"]["status"] == "WAITING_RESOLUTION"
    assert before["case"]["money_moved"] is True
    assert len(before["executions"]) >= 6
    assert {claim["capability_id"] for claim in before["claims"]} == {
        "processor.file_chargeback",
        "core.post_provisional_credit",
        "docs.send_notice",
    }
    assert before["obligations"][0]["status"] == "SATISFIED"
    assert {d["deadline_type"]: d["status"] for d in before["deadlines"]}["NOTICE_2_DAY"] == "MET"
    session.close()
    engine.dispose()

    # Destroy every mutable projection while leaving the immutable events alone.
    with sqlite3.connect(ledger_path) as raw:
        raw.execute("PRAGMA foreign_keys=ON")
        for table in ("capability_executions", "effect_intents", "effect_claims", "deadlines", "obligations"):
            raw.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,))
        raw.execute(
            "UPDATE procedure_cases SET status = 'RECEIVED', money_moved = 0 WHERE case_id = ?",
            (case_id,),
        )
        raw.commit()

    engine, session = _session(ledger_path)
    destroyed = snapshot_projections(session, case_id)
    assert destroyed["executions"] == [] and destroyed["claims"] == []
    assert destroyed["case"]["status"] == "RECEIVED"

    rebuild_case(session, case_id)
    session.commit()
    after = snapshot_projections(session, case_id)
    assert after == before
    assert LedgerRepository(session).verify_event_chain(case_id).valid
    session.close()
    engine.dispose()


def test_rebuild_is_idempotent_on_intact_case(ledger_path: str) -> None:
    from tandem.ledger.rebuild import rebuild_case, snapshot_projections

    case_id = "D-REBUILD-IDEMPOTENT"
    _run_full_procedure(ledger_path, case_id)
    engine, session = _session(ledger_path)
    before = snapshot_projections(session, case_id)
    events_before = len(LedgerRepository(session).get_events_for_case(case_id))
    rebuild_case(session, case_id)
    session.commit()
    assert snapshot_projections(session, case_id) == before
    # Rebuilding derives state; it must not append new history.
    assert len(LedgerRepository(session).get_events_for_case(case_id)) == events_before
    session.close()
    engine.dispose()


def test_rebuild_refuses_broken_chain(ledger_path: str) -> None:
    from tandem.domain.errors import LedgerIntegrityError
    from tandem.ledger.rebuild import rebuild_case

    engine, session = _session(ledger_path)
    repo = LedgerRepository(session)
    repo.create_or_get_case("D-REBUILD-BROKEN", "8830142", Decimal("340.00"))
    repo.update_case_status("D-REBUILD-BROKEN", "MEMBER_VERIFIED")
    session.commit()
    session.close()
    engine.dispose()
    with sqlite3.connect(ledger_path) as raw:
        raw.execute("DROP TRIGGER procedure_events_append_only_update")
        raw.execute(
            "UPDATE procedure_events SET payload = ? WHERE case_id = ? AND sequence = 2",
            ('{"status":"WAITING_RESOLUTION"}', "D-REBUILD-BROKEN"),
        )
        raw.commit()

    engine, session = _session(ledger_path)
    with pytest.raises(LedgerIntegrityError):
        rebuild_case(session, "D-REBUILD-BROKEN")
    session.close()
    engine.dispose()
