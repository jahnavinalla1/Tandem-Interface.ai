"""Admin/operator mutation-route authentication and bind-default regressions (H-10).

Root cause: every simulator's `/api/reset` and `/api/set_*` failure-injection switches,
plus the Tandem operator console's lease-claim/release and browser-session action
routes, had zero authentication, and the launcher bound every service to `0.0.0.0` --
so any host on the network could reset ledger state, flip failure switches, or seize a
case lease. `tandem.security.auth.require_admin_token` closes the mutation surface;
`tandem.cli.build_service_commands` closes the network exposure.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from simulators.core_bank.app import app as core_bank_app
from simulators.core_bank.beta_app import app as core_bank_beta_app
from simulators.documents.app import app as documents_app
from simulators.processor.app import app as processor_app
from tandem.api.app import app as operator_app
from tandem.cli import build_service_commands
from tandem.config import settings
from tandem.ledger.database import SessionLocal
from tandem.ledger.repository import LedgerRepository


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "app,path",
    [
        (core_bank_app, "/api/reset"),
        (core_bank_app, "/api/set_compliance_interstitial"),
        (core_bank_app, "/api/set_session_valid"),
        (core_bank_beta_app, "/api/reset"),
        (processor_app, "/api/reset"),
        (processor_app, "/api/set_mode"),
        (documents_app, "/api/reset"),
        (documents_app, "/api/set_failure"),
    ],
)
def test_simulator_admin_route_rejects_missing_and_wrong_token(app, path) -> None:
    client = TestClient(app)

    unauthenticated = client.post(path)
    assert unauthenticated.status_code == 401

    wrong_token = client.post(path, headers=_bearer("not-the-real-token"))
    assert wrong_token.status_code == 401

    authenticated = client.post(path, headers=_bearer(settings.tandem_admin_token))
    assert authenticated.status_code == 200


def test_lease_routes_reject_missing_and_wrong_admin_token() -> None:
    client = TestClient(operator_app)
    db = SessionLocal()
    repo = LedgerRepository(db)
    case_id = "D-AUTH-LEASE-001"
    repo.create_or_get_case(case_id=case_id, member_id="8830142", amount=100.00)
    repo.release_lease(case_id)
    db.commit()
    db.close()

    # No admin_token field at all: rejected (also the console's CSRF defense --
    # a forged cross-origin form submission cannot know this value either).
    forged = client.post(f"/cases/{case_id}/claim_lease", data={"operator_id": "attacker"})
    assert forged.status_code == 401

    wrong = client.post(
        f"/cases/{case_id}/claim_lease",
        data={"operator_id": "attacker", "admin_token": "wrong-token"},
    )
    assert wrong.status_code == 401

    valid = client.post(
        f"/cases/{case_id}/claim_lease",
        data={"operator_id": "auditor", "admin_token": settings.tandem_admin_token},
        follow_redirects=False,
    )
    assert valid.status_code == 303

    release_forged = client.post(f"/cases/{case_id}/release_lease")
    assert release_forged.status_code == 401

    release_valid = client.post(
        f"/cases/{case_id}/release_lease",
        data={"admin_token": settings.tandem_admin_token},
        follow_redirects=False,
    )
    assert release_valid.status_code == 303


def test_browser_session_action_route_requires_admin_token() -> None:
    client = TestClient(operator_app)

    # A session need not even exist for the auth check to fire first (401 before 404).
    unauthenticated = client.post(
        "/api/browser-sessions/nonexistent/actions",
        json={"owner_id": "x", "fencing_token": 1, "action": "CLICK", "selector": "#x"},
    )
    assert unauthenticated.status_code == 401

    authenticated_but_missing = client.post(
        "/api/browser-sessions/nonexistent/actions",
        json={"owner_id": "x", "fencing_token": 1, "action": "CLICK", "selector": "#x"},
        headers=_bearer(settings.tandem_admin_token),
    )
    assert authenticated_but_missing.status_code == 404


def test_launcher_binds_configured_host_not_hardcoded_0000() -> None:
    """Bind-default regression: previously always hardcoded `--host 0.0.0.0`."""
    loopback_commands = build_service_commands("127.0.0.1")
    assert loopback_commands
    for service in loopback_commands:
        assert "--host" in service.cmd
        host_index = service.cmd.index("--host")
        assert service.cmd[host_index + 1] == "127.0.0.1"
        assert "0.0.0.0" not in service.cmd

    # The bind host is a parameter driven by settings, not a hardcoded literal --
    # a containerized deployment can still opt into 0.0.0.0 explicitly.
    container_commands = build_service_commands("0.0.0.0")
    assert all(
        service.cmd[service.cmd.index("--host") + 1] == "0.0.0.0" for service in container_commands
    )
