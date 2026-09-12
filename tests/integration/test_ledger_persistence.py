"""Integration tests for the SQLite append-only procedure ledger and crash recovery."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tandem.ledger.service import LedgerService


@pytest.fixture
def temp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_ledger.db")


def test_ledger_persistence_and_state_reconstruction_after_process_restart(temp_db_path: str):
    # Phase 1: Simulate the initial process executing steps before a crash
    engine_1 = get_engine(temp_db_path)
    init_db(engine_1)
    session_factory_1 = get_session_factory(engine_1)

    with session_factory_1() as session:
        repo = LedgerRepository(session)

        # 1. Create case
        repo.create_or_get_case(case_id="D-8842", member_id="8830142", amount=340.00)
        repo.record_event("D-8842", "CASE_OPENED", "intake", actor="AUTOMATION")

        # 2. Step 1: Member / Transaction Lookup (READ)
        exec1 = repo.start_execution(
            case_id="D-8842",
            capability_id="core.lookup_transaction",
            capability_version="1.0.0",
            effect_class="READ",
        )
        repo.complete_execution(exec1.id, status="SUCCESS", observed_entity="8830142")

        # 3. Step 2: Processor Chargeback (COMMIT)
        exec2 = repo.start_execution(
            case_id="D-8842",
            capability_id="processor.file_chargeback",
            capability_version="1.0.0",
            effect_class="COMMIT",
            idempotency_key="regE:D-8842:chargeback",
        )
        repo.complete_execution(
            exec2.id,
            status="SUCCESS",
            audit_ref="VISA-DISP-89211",
            money_moved=False,
        )

        # 4. Step 3: Core Provisional Credit (COMMIT - MOVES MONEY)
        exec3 = repo.start_execution(
            case_id="D-8842",
            capability_id="core.post_provisional_credit",
            capability_version="1.0.0",
            effect_class="COMMIT",
            idempotency_key="regE:D-8842:provisional_credit",
            expected_entity="8830142",
            expected_amount=340.00,
        )
        repo.complete_execution(
            exec3.id,
            status="SUCCESS",
            observed_entity="8830142",
            observed_amount=340.00,
            audit_ref="MC-7741",
            money_moved=True,
        )
        repo.update_case_status("D-8842", status="PROVISIONAL_CREDIT_POSTED", money_moved=True)

        # 5. Create 2-day notice deadline
        due_at = datetime.now(timezone.utc) + timedelta(days=2)
        repo.create_deadline("D-8842", deadline_type="NOTICE_2_DAY", due_at=due_at)

        # Acquire lease for automation
        repo.acquire_lease("D-8842", owner="AUTOMATION")

        session.commit()

    # SIMULATE HARD PROCESS TERMINATION:
    # Dispose of engine_1 and all connection pools.
    engine_1.dispose()

    # Phase 2: Simulate restart of Tandem with a brand new engine reading the existing SQLite file
    engine_2 = get_engine(temp_db_path)
    session_factory_2 = get_session_factory(engine_2)

    with session_factory_2() as session_restart:
        service = LedgerService(session_restart)
        snapshot = service.reconstruct_case_state("D-8842")

        # Verify reconstructed state without ANY in-memory state
        assert snapshot.case_id == "D-8842"
        assert snapshot.member_id == "8830142"
        assert snapshot.amount == 340.00
        assert snapshot.money_moved is True  # CRITICAL: knows money already moved!
        assert snapshot.latest_memo_ref == "MC-7741"
        assert snapshot.processor_ref == "VISA-DISP-89211"
        assert "core.post_provisional_credit" in snapshot.completed_capabilities
        assert "processor.file_chargeback" in snapshot.completed_capabilities
        assert "core.lookup_transaction" in snapshot.completed_capabilities

        # Verify pending notice deadline survived restart
        assert len(snapshot.pending_deadlines) == 1
        assert snapshot.pending_deadlines[0].deadline_type == "NOTICE_2_DAY"
        assert snapshot.pending_deadlines[0].status == "PENDING"

        # Verify lease owner reconstructed
        assert snapshot.lease_owner == "AUTOMATION"

    engine_2.dispose()


def test_lapsed_deadline_is_reported_overdue_on_reconstruction(temp_db_path: str):
    """A PENDING deadline whose due date has already passed must surface as OVERDUE.

    Root cause (M-02): the deadline model never evaluated a persisted due date against
    the clock, so a lapsed statutory deadline stayed labelled PENDING indefinitely.
    """
    engine = get_engine(temp_db_path)
    init_db(engine)
    session_factory = get_session_factory(engine)

    with session_factory() as session:
        repo = LedgerRepository(session)
        repo.create_or_get_case(case_id="D-9910", member_id="8830142", amount=100.00)

        lapsed_due_at = datetime.now(timezone.utc) - timedelta(days=1)
        repo.create_deadline("D-9910", deadline_type="NOTICE_2_DAY", due_at=lapsed_due_at)

        future_due_at = datetime.now(timezone.utc) + timedelta(days=5)
        repo.create_deadline("D-9910", deadline_type="FINAL_RESOLUTION_45_DAY", due_at=future_due_at)
        session.commit()

        service = LedgerService(session)
        snapshot = service.reconstruct_case_state("D-9910")

        by_type = {d.deadline_type: d.status for d in snapshot.pending_deadlines}
        assert by_type["NOTICE_2_DAY"] == "OVERDUE"
        assert by_type["FINAL_RESOLUTION_45_DAY"] == "PENDING"

        # The transition is persisted, not just computed transiently for the read.
        persisted = repo.get_deadlines_for_case("D-9910")
        persisted_by_type = {d.deadline_type: d.status for d in persisted}
        assert persisted_by_type["NOTICE_2_DAY"] == "OVERDUE"

    engine.dispose()
