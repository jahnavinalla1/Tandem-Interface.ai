"""Integration tests for the hostile core banking simulator."""

import pytest
from fastapi.testclient import TestClient

from simulators.core_bank.app import app
from simulators.core_bank.state import core_bank_state
from tandem.config import settings

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_core_bank():
    core_bank_state.seed()
    yield
    core_bank_state.seed()


def test_seed_and_member_lookup():
    response = client.get("/api/member/8830142")
    assert response.status_code == 200
    data = response.json()
    assert data["member_id"] == "8830142"
    assert data["balance"] == 1240.50

    response_confusable = client.get("/api/member/8830124")
    assert response_confusable.status_code == 200
    assert response_confusable.json()["balance"] == 410.25


def test_hostile_search_returns_confusable_members():
    response = client.get("/workspace/search?q=88301")
    assert response.status_code == 200
    html_text = response.text
    # Deliberate hostility: both transposed IDs appear on the same results page
    assert "8830142" in html_text
    assert "8830124" in html_text
    assert "CHK-8830142-01" in html_text
    assert "CHK-8830124-01" in html_text


def test_precheck_before_and_after_credit():
    # 1. Before credit: precheck should report not found
    pre_resp = client.get("/workspace/memos?case_id=D-8842")
    assert pre_resp.status_code == 200
    assert "NO PROVISIONAL CREDIT RECORD FOUND" in pre_resp.text

    # 2. Post provisional credit
    post_resp = client.post(
        "/workspace/credit/commit",
        data={
            "institution_id": "alpha",
            "member_id": "8830142",
            "account_id": "CHK-8830142-01",
            "case_id": "D-8842",
            "amount": 340.00,
            "currency": "USD",
            "admin_token": settings.tandem_admin_token,
        },
    )
    assert post_resp.status_code == 200
    assert "PROVISIONAL CREDIT POSTED SUCCESSFULLY" in post_resp.text
    assert "MONEY_MOVED=TRUE" in post_resp.text
    assert "$1,580.50 USD" in post_resp.text

    # 3. After credit: precheck should immediately find the memo
    postcheck_resp = client.get("/workspace/memos?case_id=D-8842")
    assert postcheck_resp.status_code == 200
    assert "EXISTING PROVISIONAL CREDIT RECORD FOUND" in postcheck_resp.text
    assert "8830142" in postcheck_resp.text
    assert "$340.00 USD" in postcheck_resp.text


def test_session_expiry_switch():
    core_bank_state.session_valid = False
    resp = client.get("/workspace/search?q=8830142")
    assert resp.status_code == 401
    assert "SESSION EXPIRED" in resp.text
