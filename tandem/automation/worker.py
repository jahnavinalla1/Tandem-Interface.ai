"""Bounded background execution of `RegEWorkflow` cases (H-07).

Root cause: no operational automation service existed at all -- the workflow could
only be invoked from tests or `scripts/demo.py`, and a restarted process never
resumed in-flight work. `CaseRunner` gives the operator console a real API surface:

- `submit_case(...)` starts (or resumes) one case's workflow on a bounded worker
  pool, each task owning its own Playwright browser/page on its own thread (Playwright
  pages are not thread-safe, so a task must both create and drive its page on the same
  thread) and its own SQLAlchemy session.
- `recover_incomplete_cases(...)` is the startup recovery scan: it resubmits every
  case left in an in-flight (non-terminal, non-human-required) state so a restart
  finishes recoverable work automatically. A case already parked in `NEEDS_HUMAN` or
  `UNCERTAIN_EFFECT` -- or a terminal `FAILED`/`RESOLVED`/`WAITING_RESOLUTION` -- is
  never touched: those are queued for human review (visible on the operator dashboard),
  never blindly retried.

Every outcome (success, policy denial, uncertain effect, hard failure) is already
durably recorded by `RegEWorkflow.run_case` itself; this module's own responsibility
is only to fail closed (to `NEEDS_HUMAN`) if the task raises *unexpectedly* -- e.g. a
browser crash -- so a case can never simply vanish without a durable trace.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from decimal import Decimal
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from tandem.config import settings
from tandem.ledger.database import SessionLocal
from tandem.ledger.repository import LedgerRepository
from tandem.workflow.reg_e import RegEWorkflow
from tandem.workflow.state_machine import RegEState

logger = logging.getLogger("tandem.automation")

# States a restarted service must never touch automatically: NEEDS_HUMAN and
# UNCERTAIN_EFFECT require a human decision, FAILED/RESOLVED/WAITING_RESOLUTION are
# terminal-for-automation. Every other state is safe to resume -- `run_case` itself
# reconciles claimed/uncertain effects against the target before doing any new work.
_DO_NOT_AUTO_RESUME = {
    RegEState.NEEDS_HUMAN.value,
    RegEState.UNCERTAIN_EFFECT.value,
    RegEState.FAILED.value,
    RegEState.RESOLVED.value,
    RegEState.WAITING_RESOLUTION.value,
}


def generate_case_id() -> str:
    return f"D-API-{uuid4().hex[:10].upper()}"


class CaseRunner:
    """Bounded pool that runs (or resumes) one `RegEWorkflow` case per task."""

    def __init__(
        self,
        max_workers: int = 4,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="tandem-case-runner"
        )
        self._futures: Dict[str, "Future[None]"] = {}
        self._session_factory = session_factory

    def submit_case(
        self,
        case_id: str,
        member_id: str,
        amount: Decimal,
        card_last4: str = "4112",
    ) -> "Future[None]":
        """Schedule one case for (re-)execution and return immediately."""
        future = self._executor.submit(self._run_case_task, case_id, member_id, amount, card_last4)
        self._futures[case_id] = future
        return future

    def wait_for(self, case_id: str, timeout: Optional[float] = None) -> None:
        """Block until the most recently submitted task for `case_id` finishes.

        Deterministic completion signal for callers (tests, orchestration code) that
        need to know a submitted case has finished its worker task, instead of polling
        the ledger on a sleep loop.
        """
        future = self._futures.get(case_id)
        if future is not None:
            future.result(timeout=timeout)

    def recover_incomplete_cases(self) -> List[str]:
        """Resubmit every in-flight case; skip anything requiring human review.

        Returns the case IDs resubmitted, for logging/testing.
        """
        db = self._session_factory()
        try:
            repo = LedgerRepository(db)
            resumed: List[str] = []
            for case in repo.list_cases():
                if case.status in _DO_NOT_AUTO_RESUME:
                    continue
                resumed.append(case.case_id)
                self.submit_case(case.case_id, case.member_id, case.amount)
            return resumed
        finally:
            db.close()

    def _run_case_task(
        self, case_id: str, member_id: str, amount: Decimal, card_last4: str
    ) -> None:
        db = self._session_factory()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=settings.playwright_headless)
                page = browser.new_page()
                workflow = RegEWorkflow(session=db, page=page)
                workflow.run_case(case_id=case_id, member_id=member_id, amount=amount, card_last4=card_last4)
                browser.close()
        except Exception as exc:  # noqa: BLE001 - must never lose a case silently
            logger.exception("Case %s failed unexpectedly in automation worker", case_id)
            self._fail_closed(case_id, exc)
        finally:
            db.close()

    def _fail_closed(self, case_id: str, exc: Exception) -> None:
        """An unexpected exception (e.g. a browser crash) must still leave a durable,
        human-reviewable trace instead of the case silently never progressing again."""
        db = self._session_factory()
        try:
            repo = LedgerRepository(db)
            if repo.get_case(case_id) is None:
                return
            repo.update_case_status(case_id, status=RegEState.NEEDS_HUMAN.value)
            repo.record_event(
                case_id,
                "AUTOMATION_WORKER_TASK_FAILED",
                "automation.case_runner",
                payload={"error_type": type(exc).__name__, "message": str(exc)},
            )
            db.commit()
        finally:
            db.close()


case_runner: Optional[CaseRunner] = None


def get_case_runner() -> CaseRunner:
    """Lazily create the process-wide case runner (avoids spawning threads on import)."""
    global case_runner
    if case_runner is None:
        case_runner = CaseRunner()
    return case_runner
