"""A human acts through the API in the actual worker-owned browser context."""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tandem.api.app import app
from tandem.config import settings
from tandem.handoff.browser_session import browser_session_broker
from tandem.ledger.database import get_db, get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tests.server_utils import ensure_simulators_running, reset_all_simulators


def test_operator_action_preserves_worker_owned_context_and_cookie(tmp_path: Path) -> None:
    ensure_simulators_running()
    reset_all_simulators()
    admin_headers = {"Authorization": f"Bearer {settings.tandem_admin_token}"}
    httpx.post(
        f"{settings.core_bank_url}/api/set_compliance_interstitial",
        params={"required": "true"},
        headers=admin_headers,
        timeout=3,
    ).raise_for_status()

    engine = get_engine(str(tmp_path / "handoff-ledger.db"))
    init_db(engine)
    factory = get_session_factory(engine)
    case_id = "D-BROKER-HANDOFF"
    automation_id = "AUTOMATION:test-worker"
    operator_id = "operator-sarah"
    session_id, initial = browser_session_broker.create(
        settings.core_bank_url,
        evidence_root=tmp_path / "session-evidence",
    )

    def override_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with factory() as session:
            repo = LedgerRepository(session)
            repo.create_or_get_case(case_id, "8830142", "340.00")
            repo.register_browser_session(case_id, session_id, str(initial["url"]))
            automation = repo.acquire_lease(
                case_id, automation_id, owner_type="AUTOMATION"
            )
            session.commit()
            automation_token = automation.fencing_token

        browser_session_broker.execute(
            session_id,
            "SET_COOKIE",
            name="handoff_continuity",
            value="same-context",
            url=settings.core_bank_url,
        )
        client = TestClient(app)

        def action(
            owner_id: str,
            token: int,
            action_name: str,
            selector: str,
            *,
            value: str | None = None,
            frame: str | None = "#core_workspace_frame",
        ):
            return client.post(
                f"/api/browser-sessions/{session_id}/actions",
                json={
                    "owner_id": owner_id,
                    "fencing_token": token,
                    "action": action_name,
                    "selector": selector,
                    "frame_selector": frame,
                    "value": value,
                },
                headers=admin_headers,
            )

        assert action(automation_id, automation_token, "FILL", "input[name='q']", value="8830142").status_code == 200
        search = action(automation_id, automation_token, "CLICK", "button[type='submit']")
        assert search.status_code == 200, search.text
        open_credit = action(automation_id, automation_token, "CLICK", "a.action-credit-btn")
        assert open_credit.status_code == 200, open_credit.text
        before_thread = open_credit.json()["worker_thread_id"]
        assert "handoff_continuity" in open_credit.json()["cookie_names"]

        with factory() as session:
            repo = LedgerRepository(session)
            repo.release_lease(case_id, automation_id, automation_token)
            human = repo.acquire_lease(case_id, operator_id, owner_type="HUMAN")
            session.commit()
            human_token = human.fencing_token

        stale = action(automation_id, automation_token, "CLICK", ".operator-signoff-btn")
        assert stale.status_code == 409

        signoff = action(operator_id, human_token, "CLICK", ".operator-signoff-btn")
        assert signoff.status_code == 200
        assert signoff.json()["worker_thread_id"] == before_thread
        assert "handoff_continuity" in signoff.json()["cookie_names"]

        with factory() as session:
            repo = LedgerRepository(session)
            repo.release_lease(case_id, operator_id, human_token)
            resumed = repo.acquire_lease(
                case_id, "AUTOMATION:resumed-worker", owner_type="AUTOMATION"
            )
            resumed_token = resumed.fencing_token
            session.commit()

        resumed_fill = action(
            "AUTOMATION:resumed-worker",
            resumed_token,
            "FILL",
            "input[name='case_id']",
            value=case_id,
        )
        assert resumed_fill.status_code == 200, resumed_fill.text
        assert resumed_fill.json()["worker_thread_id"] == before_thread

        with factory() as session:
            repo = LedgerRepository(session)
            execution = repo.start_execution(
                case_id,
                "core.post_provisional_credit",
                "1.0.0",
                "COMMIT",
                browser_session_id=session_id,
            )
            session.commit()
            assert resumed_token > human_token > automation_token
            assert execution.browser_session_id == session_id
            persisted = repo.get_browser_session(session_id)
            assert persisted is not None and persisted.status == "ACTIVE"

        final = browser_session_broker.execute(session_id, "SNAPSHOT")
        assert final["worker_thread_id"] == before_thread
        assert final["cookies"]["handoff_continuity"] == "same-context"
        assert Path(final["screenshot_path"]).exists()
        assert any(control.get("name") == "case_id" for control in final["controls"])
    finally:
        app.dependency_overrides.pop(get_db, None)
        browser_session_broker.close(session_id)
        httpx.post(
            f"{settings.core_bank_url}/api/set_compliance_interstitial",
            params={"required": "false"},
            headers=admin_headers,
            timeout=3,
        )
        engine.dispose()
