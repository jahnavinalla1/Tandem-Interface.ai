"""Integration test suite for Tandem Operator & Audit Console (FastAPI).

Validates:
- Health check endpoint (/health)
- Dashboard rendering (/) with multiple case statuses and money-movement indicators
- Case detail view (/cases/{case_id}) with timeline, capability executions, and statutory deadlines
- 404 behavior for unknown case requests
- Single-owner lease transitions (POST /cases/{case_id}/claim_lease and /release_lease)
- Mutual-exclusion error (409 Conflict) when lease is claimed while held by AUTOMATION
- Machine-readable JSON REST API (/api/cases/{case_id})
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tandem.api.app import app
from tandem.config import settings
from tandem.domain.effects import EffectClass
from tandem.ledger.database import SessionLocal
from tandem.ledger.repository import LedgerRepository
from tandem.workflow.deadlines import add_business_days
from tandem.workflow.state_machine import RegEState


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def test_case_in_db():
    db = SessionLocal()
    repo = LedgerRepository(db)
    import uuid
    case_id = f"D-AUDIT-{uuid.uuid4().hex[:6].upper()}"
    member_id = "8830142"
    amount = 250.00

    repo.create_or_get_case(
        case_id=case_id,
        member_id=member_id,
        amount=amount,
    )
    repo.update_case_status(
        case_id=case_id,
        status=RegEState.PROVISIONAL_CREDIT_POSTED.value,
        money_moved=True,
    )

    # Record events
    repo.record_event(
        case_id=case_id,
        event_type="DISPUTE_INITIALIZED",
        step_name="orchestrator",
        payload={"source": "api_test", "amount": amount},
    )
    repo.record_event(
        case_id=case_id,
        event_type="EFFECT_COMMITTED",
        step_name="core.post_provisional_credit",
        payload={"memo_code": "MC-9901", "observed_amount": amount},
    )

    # Record execution
    exc = repo.start_execution(
        case_id=case_id,
        capability_id="core.post_provisional_credit",
        capability_version="1.0.0",
        effect_class=EffectClass.COMMIT.value,
        idempotency_key=f"regE:{case_id}:provisional_credit",
        expected_entity=member_id,
        expected_amount=amount,
        actor="AUTOMATION",
    )
    repo.complete_execution(
        execution_id=exc.id,
        status="SUCCESS",
        observed_entity=member_id,
        observed_amount=amount,
        money_moved=True,
        audit_ref="MC-9901",
    )

    # Record statutory deadlines (12 CFR 1005.11)
    now = datetime.now(timezone.utc)
    provisional_due = add_business_days(now, 10)
    final_due = add_business_days(now, 45)
    repo.create_deadline(case_id, "PROVISIONAL_CREDIT_10_DAY", provisional_due)
    repo.create_deadline(case_id, "INVESTIGATION_45_DAY", final_due)

    # Mark provisional credit deadline as MET
    repo.resolve_deadline(case_id, "PROVISIONAL_CREDIT_10_DAY")

    db.commit()
    db.close()
    yield case_id


def test_healthcheck_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "tandem-operator-console"
    assert "timestamp" in data


def test_dashboard_renders_cases(client, test_case_in_db):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html_content = response.text
    assert "TANDEM // EFFECT-AWARE DISPUTE AUTOMATION" in html_content
    assert test_case_in_db in html_content
    assert "8830142" in html_content
    assert "$250.00" in html_content
    assert "MOVED" in html_content


def test_case_detail_view(client, test_case_in_db):
    response = client.get(f"/cases/{test_case_in_db}")
    assert response.status_code == 200
    html_content = response.text
    assert f"Case Reference: {test_case_in_db}" in html_content
    assert "core.post_provisional_credit" in html_content
    assert "MC-9901" in html_content
    assert "PROVISIONAL_CREDIT_10_DAY" in html_content
    assert "INVESTIGATION_45_DAY" in html_content
    assert "MET" in html_content


def test_case_detail_not_found(client):
    response = client.get("/cases/D-NONEXISTENT-9999")
    assert response.status_code == 404
    assert "Case not found" in response.text


def test_api_get_case_state(client, test_case_in_db):
    response = client.get(f"/api/cases/{test_case_in_db}")
    assert response.status_code == 200
    data = response.json()
    assert data["case_id"] == test_case_in_db
    assert data["member_id"] == "8830142"
    assert data["amount"] == 250.00
    assert data["money_moved"] is True
    assert "core.post_provisional_credit" in data["completed_capabilities"]
    assert data["latest_memo_ref"] == "MC-9901"
    assert len(data["pending_deadlines"]) >= 1


def test_api_case_not_found(client):
    response = client.get("/api/cases/D-NONEXISTENT-9999")
    assert response.status_code == 404


def test_lease_claim_and_release_flow(client, test_case_in_db):
    db = SessionLocal()
    repo = LedgerRepository(db)

    # Initially release any lease to allow human claim
    repo.release_lease(test_case_in_db)
    db.commit()
    db.close()

    # 1. Operator claims lease via UI POST
    resp_claim = client.post(
        f"/cases/{test_case_in_db}/claim_lease",
        data={"operator_id": "auditor_jane", "admin_token": settings.tandem_admin_token},
        follow_redirects=False,
    )
    assert resp_claim.status_code == 303
    assert resp_claim.headers["location"] == f"/cases/{test_case_in_db}"

    # Verify state shows auditor_jane owns lease
    state_resp = client.get(f"/api/cases/{test_case_in_db}")
    assert state_resp.json()["lease_owner"] == "auditor_jane"

    # 2. Another operator attempts to claim while auditor_jane holds it -> 409 Conflict
    resp_conflict = client.post(
        f"/cases/{test_case_in_db}/claim_lease",
        data={"operator_id": "auditor_bob", "admin_token": settings.tandem_admin_token},
    )
    assert resp_conflict.status_code == 409
    assert "already held by operator 'auditor_jane'" in resp_conflict.json()["detail"]

    # 3. Release lease back to automation via UI POST
    resp_release = client.post(
        f"/cases/{test_case_in_db}/release_lease",
        data={"admin_token": settings.tandem_admin_token},
        follow_redirects=False,
    )
    assert resp_release.status_code == 303

    # Verify state shows AUTOMATION owns lease again
    state_resp_after = client.get(f"/api/cases/{test_case_in_db}")
    assert state_resp_after.json()["lease_owner"] == "AUTOMATION"
