"""Worker-owned Playwright sessions and a bounded operator command broker."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from tandem.security.evidence import sanitize, screenshot


@dataclass
class _Command:
    action: str
    params: dict[str, Any]
    response: queue.Queue[tuple[bool, Any]] = field(default_factory=queue.Queue)


class BrowserSessionWorker:
    """Own one Playwright browser/context/page exclusively on its worker thread."""

    def __init__(
        self,
        session_id: str,
        initial_url: str,
        evidence_root: str | Path = "evidence/sessions",
        headless: bool = True,
    ) -> None:
        self.session_id = session_id
        self.initial_url = initial_url
        self.evidence_dir = Path(evidence_root) / session_id
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self._commands: queue.Queue[_Command] = queue.Queue()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"browser-session-{session_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=20):
            raise TimeoutError(f"Browser session {self.session_id} did not start")
        if self._startup_error is not None:
            raise RuntimeError(f"Browser session failed to start: {self._startup_error}")

    def execute(self, action: str, **params: Any) -> dict[str, Any]:
        command = _Command(action=action, params=params)
        self._commands.put(command)
        try:
            ok, result = command.response.get(timeout=20)
        except queue.Empty as exc:
            raise TimeoutError(f"Browser session command {action} timed out") from exc
        if not ok:
            raise RuntimeError(f"Browser session command {action} failed: {result}")
        if not isinstance(result, dict):
            raise RuntimeError("Browser session returned an invalid result")
        return result

    def close(self) -> None:
        if self._thread.is_alive():
            self.execute("CLOSE")
            self._thread.join(timeout=10)

    def _run(self) -> None:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context = browser.new_context()
                page = context.new_page()
                if self.initial_url:
                    page.goto(self.initial_url, wait_until="networkidle")
                self._ready.set()
                self._command_loop(browser, context, page)
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()

    def _command_loop(
        self, browser: Browser, browser_context: BrowserContext, page: Page
    ) -> None:
        while True:
            command = self._commands.get()
            try:
                if command.action == "CLOSE":
                    browser.close()
                    command.response.put((True, {"status": "CLOSED"}))
                    return
                result = self._execute_command(browser_context, page, command)
                command.response.put((True, result))
            except BaseException as exc:
                command.response.put((False, f"{type(exc).__name__}: {exc}"))

    def _execute_command(
        self, browser_context: BrowserContext, page: Page, command: _Command
    ) -> dict[str, Any]:
        action = command.action
        params = command.params
        if action == "NAVIGATE":
            page.goto(str(params["url"]), wait_until="networkidle")
        elif action == "FILL":
            control = self._context(page, params.get("frame_selector")).locator(
                str(params["selector"])
            ).first
            control.wait_for(state="visible", timeout=5000)
            control.fill(str(params["value"]), timeout=5000)
        elif action == "CLICK":
            control = self._context(page, params.get("frame_selector")).locator(
                str(params["selector"])
            ).first
            control.wait_for(state="visible", timeout=5000)
            # A form inside an iframe navigates that frame, not the top-level
            # page. The parent's previous networkidle state can already be set.
            # Wait on the actual action frame before collecting masked evidence.
            element = control.element_handle(timeout=5000)
            action_frame = element.owner_frame() if element is not None else None
            control.click(timeout=5000)
            if action_frame is not None:
                action_frame.wait_for_load_state("networkidle", timeout=5000)
            else:
                page.wait_for_load_state("networkidle", timeout=5000)
        elif action == "SET_COOKIE":
            browser_context.add_cookies(
                [
                    {
                        "name": str(params["name"]),
                        "value": str(params["value"]),
                        "url": str(params["url"]),
                    }
                ]
            )
        elif action != "SNAPSHOT":
            raise ValueError(f"Unsupported browser-session action '{action}'")
        return self._snapshot(browser_context, page)

    def _snapshot(self, browser_context: BrowserContext, page: Page) -> dict[str, Any]:
        screenshot_path = self.evidence_dir / "latest.png"
        screenshot_path.write_bytes(screenshot(page))
        controls: list[dict[str, Any]] = []
        for frame in page.frames:
            try:
                locator = frame.locator("button, input, select, textarea, a")
                for index in range(min(locator.count(), 100)):
                    controls.append(
                        locator.nth(index).evaluate(
                            """el => ({
                                tag: el.tagName.toLowerCase(),
                                id: el.id || null,
                                name: el.getAttribute('name'),
                                type: el.getAttribute('type'),
                                text: (el.innerText || el.getAttribute('aria-label') || '').trim()
                            })"""
                        )
                    )
            except Exception:
                continue
        cookies = browser_context.cookies()
        return {
            "session_id": self.session_id,
            "status": "ACTIVE",
            "url": page.url,
            "title": page.title(),
            "controls": sanitize(controls),
            "cookie_names": [cookie["name"] for cookie in cookies],
            "cookies": {cookie["name"]: cookie["value"] for cookie in cookies},
            "screenshot_path": str(screenshot_path.resolve()),
            "worker_thread_id": threading.get_ident(),
        }

    @staticmethod
    def _context(page: Page, frame_selector: Any):
        return page.frame_locator(str(frame_selector)) if frame_selector else page


class BrowserSessionBroker:
    """Process-local command transport for worker-owned browser sessions."""

    def __init__(self) -> None:
        self._workers: dict[str, BrowserSessionWorker] = {}
        self._lock = threading.Lock()

    def create(
        self,
        initial_url: str,
        evidence_root: str | Path = "evidence/sessions",
        headless: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        session_id = f"browser-{uuid4().hex}"
        worker = BrowserSessionWorker(
            session_id,
            initial_url,
            evidence_root=evidence_root,
            headless=headless,
        )
        worker.start()
        with self._lock:
            self._workers[session_id] = worker
        return session_id, worker.execute("SNAPSHOT")

    def execute(self, session_id: str, action: str, **params: Any) -> dict[str, Any]:
        with self._lock:
            worker = self._workers.get(session_id)
        if worker is None:
            raise KeyError(f"Browser session '{session_id}' is not active in this worker")
        return worker.execute(action, **params)

    def close(self, session_id: str) -> None:
        with self._lock:
            worker = self._workers.pop(session_id, None)
        if worker is not None:
            worker.close()


browser_session_broker = BrowserSessionBroker()
