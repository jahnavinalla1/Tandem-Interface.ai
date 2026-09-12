"""Process-boundary contracts for claims, ownership, and obligations."""

from __future__ import annotations

import multiprocessing
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import sync_playwright

from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tandem.replay.executor import DeterministicExecutor
from tests.server_utils import ensure_simulators_running, reset_all_simulators


def _identity(case_id: str):
    from tandem.domain.identity import EffectIdentity

    return EffectIdentity(
        institution_id="alpha",
        procedure_id="reg_e_dispute",
        case_id=case_id,
        capability_id="core.post_provisional_credit",
        member_id="8830142",
        account_id="CHK-8830142-01",
        amount=Decimal("340.00"),
        currency="USD",
        business_reference=case_id,
    )


def _claim_worker(db_path: str, barrier, queue, owner_id: str) -> None:
    engine = get_engine(db_path)
    factory = get_session_factory(engine)
    try:
        barrier.wait(timeout=10)
        with factory() as session:
            claim = LedgerRepository(session).claim_effect(_identity("D-PROCESS-RACE"), owner_id)
            session.commit()
            queue.put((claim.acquired, claim.status, claim.fencing_token))
    finally:
        engine.dispose()


def _human_claim_worker(db_path: str, barrier, queue, owner_id: str) -> None:
    engine = get_engine(db_path)
    factory = get_session_factory(engine)
    try:
        barrier.wait(timeout=10)
        with factory() as session:
            lease = LedgerRepository(session).acquire_lease(
                "D-HUMAN-RACE", owner_id, owner_type="HUMAN"
            )
            session.commit()
            queue.put((True, lease.owner_id, lease.fencing_token))
    except Exception as exc:  # Result is asserted by the parent process.
        queue.put((False, type(exc).__name__, 0))
    finally:
        engine.dispose()


def _target_effect_worker(
    ledger_path: str,
    target_path: str,
    case_id: str,
    barrier,
    queue,
    owner_id: str,
) -> None:
    from tandem.domain.effects import EffectClaimStatus

    engine = get_engine(ledger_path)
    factory = get_session_factory(engine)
    try:
        barrier.wait(timeout=10)
        with factory() as session:
            repo = LedgerRepository(session)
            identity = _identity(case_id)
            claim = repo.claim_effect(identity, owner_id)
            session.commit()
            if not claim.acquired:
                queue.put(claim.status)
                return

            assert repo.transition_effect_claim(
                identity.idempotency_key,
                owner_id,
                claim.fencing_token,
                {EffectClaimStatus.CLAIMED},
                EffectClaimStatus.APPLYING,
            )
            session.commit()
            with sqlite3.connect(target_path, timeout=30) as target:
                target.execute(
                    "INSERT INTO effects(case_id, idempotency_key, owner_id) VALUES (?, ?, ?)",
                    (case_id, identity.idempotency_key, owner_id),
                )
                target.commit()
            assert repo.transition_effect_claim(
                identity.idempotency_key,
                owner_id,
                claim.fencing_token,
                {EffectClaimStatus.APPLYING},
                EffectClaimStatus.APPLIED,
            )
            session.commit()
            queue.put("SUCCESS")
    finally:
        engine.dispose()


def test_atomic_effect_claim_across_processes(tmp_path: Path) -> None:
    db_path = str(tmp_path / "effect-claim.db")
    engine = get_engine(db_path)
    init_db(engine)
    engine.dispose()

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    workers = [
        context.Process(target=_claim_worker, args=(db_path, barrier, queue, f"worker-{i}"))
        for i in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0

    results = [queue.get(timeout=2) for _ in workers]
    assert sum(acquired for acquired, _status, _token in results) == 1
    assert len({token for _acquired, _status, token in results if token}) == 1


def test_two_processes_produce_one_target_effect_repeated(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "repeated-ledger.db")
    target_path = str(tmp_path / "external-target.db")
    engine = get_engine(ledger_path)
    init_db(engine)
    engine.dispose()
    with sqlite3.connect(target_path) as target:
        target.execute(
            "CREATE TABLE effects (id INTEGER PRIMARY KEY, case_id TEXT, "
            "idempotency_key TEXT, owner_id TEXT)"
        )

    context = multiprocessing.get_context("spawn")
    for attempt in range(20):
        case_id = f"D-REPEATED-{attempt:02d}"
        case_engine = get_engine(ledger_path)
        case_factory = get_session_factory(case_engine)
        with case_factory() as session:
            LedgerRepository(session).create_or_get_case(
                case_id, "8830142", Decimal("340.00")
            )
            session.commit()
        case_engine.dispose()
        barrier = context.Barrier(2)
        queue = context.Queue()
        workers = [
            context.Process(
                target=_target_effect_worker,
                args=(
                    ledger_path,
                    target_path,
                    case_id,
                    barrier,
                    queue,
                    f"worker-{attempt}-{worker_number}",
                ),
            )
            for worker_number in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=20)
            assert worker.exitcode == 0

        results = [queue.get(timeout=2) for _ in workers]
        assert results.count("SUCCESS") == 1
        assert sum(result in {"ALREADY_CLAIMED", "ALREADY_APPLIED"} for result in results) == 1

        with sqlite3.connect(target_path) as target:
            effect_count = target.execute(
                "SELECT COUNT(*) FROM effects WHERE case_id = ?", (case_id,)
            ).fetchone()[0]
        assert effect_count == 1


def test_atomic_human_lease_across_processes(tmp_path: Path) -> None:
    db_path = str(tmp_path / "human-lease.db")
    engine = get_engine(db_path)
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session:
        LedgerRepository(session).create_or_get_case(
            "D-HUMAN-RACE", "8830142", Decimal("340.00")
        )
        session.commit()
    engine.dispose()

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    workers = [
        context.Process(
            target=_human_claim_worker,
            args=(db_path, barrier, queue, f"operator-{i}"),
        )
        for i in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0

    results = [queue.get(timeout=2) for _ in workers]
    assert sum(success for success, _owner, _token in results) == 1


def test_stale_lease_recovery_fences_old_owner(tmp_path: Path) -> None:
    engine = get_engine(str(tmp_path / "stale-lease.db"))
    init_db(engine)
    factory = get_session_factory(engine)
    now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    with factory() as session:
        repo = LedgerRepository(session)
        repo.create_or_get_case("D-STALE", "8830142", Decimal("340.00"))
        old = repo.acquire_lease(
            "D-STALE",
            "worker-old",
            owner_type="AUTOMATION",
            now=now,
            ttl=timedelta(seconds=1),
        )
        session.commit()
        old_token = old.fencing_token

    with factory() as session:
        repo = LedgerRepository(session)
        new = repo.acquire_lease(
            "D-STALE",
            "worker-new",
            owner_type="AUTOMATION",
            now=now + timedelta(seconds=2),
        )
        session.commit()
        assert new.fencing_token > old_token
        checked_at = now + timedelta(seconds=2)
        assert not repo.validate_lease_token(
            "D-STALE", "worker-old", old_token, now=checked_at
        )
        assert repo.validate_lease_token(
            "D-STALE", "worker-new", new.fencing_token, now=checked_at
        )


def test_stale_automation_fence_blocks_the_next_browser_action() -> None:
    ensure_simulators_running()
    reset_all_simulators()
    capability = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    validations = 0

    def lease_is_current() -> bool:
        nonlocal validations
        validations += 1
        return validations == 1

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        executor = DeterministicExecutor(browser.new_page())
        executor.lease_validator = lease_is_current
        outcome = executor.execute(
            capability,
            {
                "institution_id": "alpha",
                "member_id": "8830142",
                "account_id": "CHK-8830142-01",
                "case_id": "D-FENCED-ACTION",
                "amount": "340.00",
                "currency": "USD",
            },
        )
        browser.close()

    assert validations == 2
    assert outcome.category == OutcomeCategory.NEEDS_HUMAN
    assert outcome.code == OutcomeCode.LEASE_FENCED
    from simulators.core_bank.state import core_bank_state

    assert core_bank_state.find_credit_by_case("D-FENCED-ACTION") is None


def test_obligation_is_durable_before_effect_claim(tmp_path: Path) -> None:
    engine = get_engine(str(tmp_path / "obligation.db"))
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session:
        repo = LedgerRepository(session)
        repo.create_or_get_case("D-OBLIGATION", "8830142", Decimal("340.00"))
        repo.create_obligation(
            "D-OBLIGATION",
            "NOTICE_2_DAY",
            datetime(2026, 9, 15, 17, 0, tzinfo=timezone.utc),
        )
        repo.claim_effect(_identity("D-OBLIGATION"), "worker-1")
        session.commit()

    engine.dispose()
    reopened = get_engine(str(tmp_path / "obligation.db"))
    reopened_factory = get_session_factory(reopened)
    with reopened_factory() as session:
        repo = LedgerRepository(session)
        obligations = repo.get_obligations_for_case("D-OBLIGATION")
        events = repo.get_events_for_case("D-OBLIGATION")
        assert len(obligations) == 1
        assert obligations[0].status == "PLANNED"
        event_types = [event.event_type for event in events]
        assert event_types.index("OBLIGATION_CREATED") < event_types.index("EFFECT_CLAIMED")
