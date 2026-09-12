"""Launcher / demo compatibility regression (H-06).

Root cause: `scripts/demo.py` imported a test fixture module (`tests.server_utils`)
and mutated simulator Python objects (`core_bank_state.require_compliance_interstitial
= True`) directly in-process. That only ever worked when the demo started its own
in-process simulators; it silently did nothing once the documented multi-process
launcher (`scripts/start_services.py` / `tandem.cli`) had already started the real
services as separate OS processes, because the mutation touched a different process's
objects.

This test launches the real simulator services as independent OS subprocesses (via
the same `tandem.cli.build_service_commands` the documented launcher uses, on
alternate ports so it does not collide with this test session's own ambient daemon-
thread simulators) and then drives them exclusively through
`tandem.support.simulator_control` -- the same, non-test-importing module
`scripts/demo.py` now uses -- proving admin control and a full deterministic replay
work correctly against a process this test never imported.
"""

from __future__ import annotations

import subprocess
import time
from typing import Iterator

import httpx
import pytest
from playwright.sync_api import sync_playwright

from tandem.cli import build_service_commands
from tandem.config import Settings, settings
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCode
from tandem.replay.executor import DeterministicExecutor

_ALT_PORTS = {
    "core_bank_port": 18101,
    "core_bank_2_port": 18102,
    "processor_port": 18103,
    "documents_port": 18104,
}


def _wait_for_health(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
            if resp.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.2)
    raise TimeoutError(f"Service on port {port} did not become healthy: {last_error}")


@pytest.fixture
def subprocess_simulators() -> Iterator[None]:
    """Launch core-bank Alpha/Beta, processor, and documents as real OS subprocesses,
    exactly as the documented launcher does, on alternate ports."""
    alt_settings = Settings(**_ALT_PORTS)
    commands = [
        c
        for c in build_service_commands("127.0.0.1", settings_obj=alt_settings)
        if c.name != "Tandem Operator Console"
    ]
    processes = [subprocess.Popen(c.cmd) for c in commands]
    try:
        for port in _ALT_PORTS.values():
            _wait_for_health(port)
        yield
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
        for p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def test_demo_style_control_and_replay_work_against_separately_launched_processes(
    subprocess_simulators, monkeypatch
) -> None:
    for attr, value in _ALT_PORTS.items():
        monkeypatch.setattr(settings, attr, value)

    # Import after monkeypatching settings: these read `settings.*` at call time, not
    # import time, so this proves the exact functions `scripts/demo.py` now uses reach
    # a process this test never imported -- the actual compatibility bug being fixed.
    from tandem.support.simulator_control import get_member, reset_all_simulators

    reset_all_simulators()

    member = get_member("8830142")
    assert member["member_id"] == "8830142"
    baseline_balance = member["balance"]

    cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")
    inputs = {
        "institution_id": "alpha",
        "member_id": "8830142",
        "account_id": member["account_id"],
        "case_id": "D-LAUNCHER-COMPAT-001",
        "amount": 50.00,
        "currency": "USD",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        outcome = DeterministicExecutor(page=page).execute(capability=cap, inputs=inputs)
        browser.close()

    assert outcome.code == OutcomeCode.COMPLETED, outcome.message
    assert outcome.money_moved is True

    after = get_member("8830142")
    assert after["balance"] == baseline_balance + 50.00
