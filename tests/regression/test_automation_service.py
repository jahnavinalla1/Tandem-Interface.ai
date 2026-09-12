"""Operational automation service regressions (H-07).

Root cause: no operational automation service existed at all -- the workflow could
only be invoked from tests or `scripts/demo.py`, and a restarted process never
resumed in-flight work while blindly leaving human-review cases alone.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from tandem.api.app import app
from tandem.automation.worker import CaseRunner, get_case_runner
from tandem.config import settings
from tandem.ledger.database import get_db, get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tandem.ledger.service import LedgerService
from tandem.workflow.reg_e import RegEWorkflow
from tandem.workflow.state_machine import RegEState
from tests.server_utils import ensure_simulators_running, reset_all_simulators


def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.tandem_admin_token}"}


def _isolated_runner(tmp_path: Path, name: str) -> tuple:
    engine = get_engine(str(tmp_path / name))
    init_db(engine)
    factory = get_session_factory(engine)
    runner = CaseRunner(max_workers=2, session_factory=factory)
    return engine, factory, runner


def test_api_created_case_reaches_durable_terminal_state(tmp_path: Path) -> None:
    ensure_simulators_running()
    reset_all_simulators()
    engine, factory, runner = _isolated_runner(tmp_path, "automation-api-ledger.db")

    def override_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_case_runner] = lambda: runner
    try:
        client = TestClient(app)
        response = client.post(
            "/api/cases",
            json={"member_id": "8830142", "amount": "225.00"},
            headers=_admin_headers(),
        )
        assert response.status_code == 202
        case_id = response.json()["case_id"]

        runner.wait_for(case_id, timeout=60)

        state_resp = client.get(f"/api/cases/{case_id}")
        assert state_resp.status_code == 200
        data = state_resp.json()
        assert data["status"] == RegEState.WAITING_RESOLUTION.value
        assert data["money_moved"] is True
        assert "core.post_provisional_credit" in data["completed_capabilities"]
        assert "docs.send_notice" in data["completed_capabilities"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_case_runner, None)
        engine.dispose()


def test_api_rejects_missing_and_wrong_admin_token(tmp_path: Path) -> None:
    engine, factory, runner = _isolated_runner(tmp_path, "automation-api-auth-ledger.db")

    def override_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_case_runner] = lambda: runner
    try:
        client = TestClient(app)
        unauthenticated = client.post("/api/cases", json={"member_id": "8830142", "amount": "10.00"})
        assert unauthenticated.status_code == 401

        wrong = client.post(
            "/api/cases",
            json={"member_id": "8830142", "amount": "10.00"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert wrong.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_case_runner, None)
        engine.dispose()


def test_startup_recovery_resumes_in_flight_case_but_not_needs_human(tmp_path: Path) -> None:
    """Restart resumes recoverable work but never touches a case needing human review."""
    ensure_simulators_running()
    reset_all_simulators()
    engine, factory, runner = _isolated_runner(tmp_path, "automation-recovery-ledger.db")
    try:
        # Case A: crashed mid-procedure after money moved but before the notice was
        # sent -- a genuinely safe, realistic in-flight state (same pattern as the
        # crash-recovery suite).
        resumable_case_id = "D-RECOVERY-RESUMABLE"
        with factory() as seed_session, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=settings.playwright_headless)
            page = browser.new_page()
            crashed = False
            try:
                RegEWorkflow(session=seed_session, page=page, kill_after_credit=True).run_case(
                    case_id=resumable_case_id, member_id="8830142", amount=180.00
                )
            except RuntimeError:
                crashed = True
            finally:
                browser.close()
            assert crashed is True

        with factory() as check_session:
            mid_flight_status = LedgerRepository(check_session).get_case(resumable_case_id).status
            assert mid_flight_status not in {
                RegEState.NEEDS_HUMAN.value,
                RegEState.UNCERTAIN_EFFECT.value,
                RegEState.WAITING_RESOLUTION.value,
            }

        # Case B: already parked for human review -- must never be auto-touched.
        human_case_id = "D-RECOVERY-NEEDS-HUMAN"
        with factory() as seed_session:
            repo = LedgerRepository(seed_session)
            repo.create_or_get_case(case_id=human_case_id, member_id="8830142", amount=90.00)
            repo.update_case_status(human_case_id, status=RegEState.NEEDS_HUMAN.value)
            seed_session.commit()

        resumed = runner.recover_incomplete_cases()
        assert resumable_case_id in resumed
        assert human_case_id not in resumed

        runner.wait_for(resumable_case_id, timeout=60)

        with factory() as final_session:
            final_snapshot = LedgerService(final_session).reconstruct_case_state(resumable_case_id)
            assert final_snapshot.status == RegEState.WAITING_RESOLUTION.value
            assert final_snapshot.money_moved is True
            assert "docs.send_notice" in final_snapshot.completed_capabilities

            human_snapshot = LedgerService(final_session).reconstruct_case_state(human_case_id)
            assert human_snapshot.status == RegEState.NEEDS_HUMAN.value
    finally:
        engine.dispose()
