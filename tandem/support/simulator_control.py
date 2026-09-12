"""Production-safe simulator lifecycle helpers (H-06).

Root cause: `scripts/demo.py` previously imported `tests.server_utils` -- a test
fixture module -- into production code, and demonstration scenarios mutated
simulator Python objects (`core_bank_state.require_compliance_interstitial = True`)
directly in-process. That only ever worked when the demo started its own in-process
simulators; it silently did nothing when the documented multi-process launcher
(`scripts/start_services.py` / `tandem.cli`) had already started them as separate OS
processes, because the demo's mutation touched a different process's Python objects.

This module is the non-test equivalent of `tests/server_utils.py`: it starts
simulators in daemon threads only if they are not already listening (compatible with
being run standalone, or against services already launched separately), and every
reset/mode change goes through the simulators' authenticated HTTP admin API -- the
one interface that is correct regardless of which process is actually serving that
port. `tests/server_utils.py` now delegates here so both callers share one
implementation.
"""

from __future__ import annotations

import socket
import threading
import time

import httpx
import uvicorn

from tandem.config import settings


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def start_server_in_thread(app, port: int) -> None:
    if is_port_open(port):
        return  # Already running (e.g. started separately via the multi-process launcher)

    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config=config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Poll until ready
    for _ in range(30):
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
            if resp.status_code in (200, 401, 404):
                return
        except Exception:
            time.sleep(0.1)


def ensure_simulators_running() -> None:
    """Ensure core bank Alpha/Beta (8001/8002), processor (8003), and documents (8004)
    are reachable, starting an in-process daemon thread only for whichever is not
    already listening (e.g. because a separate `start_services.py` launched it)."""
    from simulators.core_bank.app import app as core_bank_app
    from simulators.core_bank.beta_app import app as core_bank_beta_app
    from simulators.documents.app import app as docs_app
    from simulators.processor.app import app as proc_app

    start_server_in_thread(core_bank_app, settings.core_bank_port)
    start_server_in_thread(core_bank_beta_app, settings.core_bank_2_port)
    start_server_in_thread(proc_app, settings.processor_port)
    start_server_in_thread(docs_app, settings.documents_port)


def reset_all_simulators() -> None:
    """Reset every simulator to its pristine seed state via its authenticated HTTP
    admin API -- the only mechanism guaranteed to reach whichever process is actually
    serving that port."""
    headers = {"Authorization": f"Bearer {settings.tandem_admin_token}"}
    for url in [
        settings.core_bank_url,
        settings.core_bank_2_url,
        settings.processor_url,
        settings.documents_url,
    ]:
        try:
            httpx.post(f"{url}/api/reset", headers=headers, timeout=3.0)
        except Exception:
            pass


def set_core_bank_mode(
    *,
    require_compliance_interstitial: "bool | None" = None,
    session_valid: "bool | None" = None,
    fail_credit_lookup_when_present: "bool | None" = None,
    post_commit_delay_ms: "int | None" = None,
) -> None:
    """Flip a core-bank (Alpha) failure/behavior switch via its authenticated HTTP API."""
    headers = {"Authorization": f"Bearer {settings.tandem_admin_token}"}
    if require_compliance_interstitial is not None:
        httpx.post(
            f"{settings.core_bank_url}/api/set_compliance_interstitial",
            params={"required": str(require_compliance_interstitial).lower()},
            headers=headers,
            timeout=3.0,
        ).raise_for_status()
    if session_valid is not None:
        httpx.post(
            f"{settings.core_bank_url}/api/set_session_valid",
            params={"valid": str(session_valid).lower()},
            headers=headers,
            timeout=3.0,
        ).raise_for_status()
    if fail_credit_lookup_when_present is not None:
        httpx.post(
            f"{settings.core_bank_url}/api/set_credit_lookup_failure",
            params={"fail": str(fail_credit_lookup_when_present).lower()},
            headers=headers,
            timeout=3.0,
        ).raise_for_status()
    if post_commit_delay_ms is not None:
        httpx.post(
            f"{settings.core_bank_url}/api/set_post_commit_delay",
            params={"delay_ms": post_commit_delay_ms},
            headers=headers,
            timeout=3.0,
        ).raise_for_status()


def set_processor_mode(
    *,
    session_expired: bool = False,
    timeout_after_submit: bool = False,
    system_failure: bool = False,
    fail_lookup_when_present: bool = False,
) -> None:
    """Flip a processor failure switch via its authenticated HTTP API."""
    headers = {"Authorization": f"Bearer {settings.tandem_admin_token}"}
    httpx.post(
        f"{settings.processor_url}/api/set_mode",
        params={
            "session_expired": str(session_expired).lower(),
            "timeout_after_submit": str(timeout_after_submit).lower(),
            "system_failure": str(system_failure).lower(),
            "fail_lookup_when_present": str(fail_lookup_when_present).lower(),
        },
        headers=headers,
        timeout=3.0,
    ).raise_for_status()


def get_member(member_id: str, *, institution_url: str | None = None) -> dict:
    """Read a member's account/balance via the read-only HTTP API (no admin token
    required -- this is a query, not a mutation)."""
    base = institution_url or settings.core_bank_url
    resp = httpx.get(f"{base}/api/member/{member_id}", timeout=3.0)
    resp.raise_for_status()
    return resp.json()
