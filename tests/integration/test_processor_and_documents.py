"""Integration tests for card processor and notice document simulators."""

import pytest
from fastapi.testclient import TestClient

from simulators.documents.app import app as docs_app
from simulators.documents.state import document_state
from simulators.processor.app import app as processor_app
from simulators.processor.state import processor_state

proc_client = TestClient(processor_app)
docs_client = TestClient(docs_app)


@pytest.fixture(autouse=True)
def reset_simulators():
    processor_state.reset()
    document_state.reset()
    yield
    processor_state.reset()
    document_state.reset()


def test_processor_successful_chargeback():
    response = proc_client.post(
        "/chargeback/file",
        data={
            "case_id": "D-8842",
            "card_last4": "4112",
            "amount": 340.00,
            "dispute_reason": "Fraud / Unauthorized Transaction",
        },
    )
    assert response.status_code == 200
    assert "CHARGEBACK FILED WITH NETWORK" in response.text
    assert "VISA-DISP-" in response.text

    # Verify reconciliation lookup
    lookup = proc_client.get("/chargeback/lookup?case_id=D-8842")
    assert lookup.status_code == 200
    assert "CONFIRMED NETWORK FILING" in lookup.text
    assert "VISA-DISP-" in lookup.text


def test_processor_session_expired_switch():
    processor_state.session_expired = True
    response = proc_client.post(
        "/chargeback/file",
        data={"case_id": "D-8842", "card_last4": "4112", "amount": 340.00},
    )
    assert response.status_code == 401
    assert "PROCESSOR SESSION EXPIRED" in response.text


def test_processor_timeout_after_submit_leaves_effect_for_reconciliation():
    """Critical scenario: connection dropped after submit.

    The mutation took place on the backend, but the response was a 504 Gateway Timeout.
    """
    processor_state.timeout_after_submit = True
    response = proc_client.post(
        "/chargeback/file",
        data={"case_id": "D-8842", "card_last4": "4112", "amount": 340.00},
    )
    assert response.status_code == 504

    # The effect DID happen on the processor backend!
    cb = processor_state.find_by_case("D-8842")
    assert cb is not None
    assert cb.amount == 340.00
    assert cb.network_ref.startswith("VISA-DISP-")

    # A postcheck inquiry finds it
    lookup = proc_client.get("/chargeback/lookup?case_id=D-8842")
    assert lookup.status_code == 200
    assert cb.network_ref in lookup.text


def test_document_notice_success_and_failure():
    # 1. Successful dispatch
    send_resp = docs_client.post(
        "/notices/send",
        data={
            "case_id": "D-8842",
            "member_id": "8830142",
            "notice_type": "REG_E_PROVISIONAL_CREDIT_DISCLOSURE",
            "amount": 340.00,
            "deadline_due_at": "2026-09-04 17:00:00",
        },
    )
    assert send_resp.status_code == 200
    assert "MEMBER COMPLIANCE NOTICE DISPATCHED" in send_resp.text
    assert "NOT-" in send_resp.text

    # Lookup confirms notice is sent
    lookup = docs_client.get("/notices/lookup?case_id=D-8842")
    assert lookup.status_code == 200
    assert "NOTICE FOUND" in lookup.text

    # 2. Failure simulation
    document_state.simulate_failure = True
    fail_resp = docs_client.post(
        "/notices/send",
        data={
            "case_id": "D-9999",
            "member_id": "8830142",
            "notice_type": "REG_E_PROVISIONAL_CREDIT_DISCLOSURE",
            "amount": 100.00,
            "deadline_due_at": "2026-09-04 17:00:00",
        },
    )
    assert fail_resp.status_code == 500
