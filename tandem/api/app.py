"""Tandem Operator & Audit Console (FastAPI Web Application).

Provides:
- Case timeline and audit trail visualization
- Statutory deadline monitoring (12 CFR 1005.11)
- Live session linkage
- Single-owner lease handoff controls
- REST API for case state and event histories
"""

import html
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Literal, Optional

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from tandem.automation.worker import CaseRunner, generate_case_id, get_case_runner
from tandem.config import settings
from tandem.domain.errors import LeaseConflictError
from tandem.handoff.browser_session import browser_session_broker
from tandem.handoff.coordinator import HandoffCoordinator
from tandem.ledger.database import get_db
from tandem.ledger.models import ProcedureCaseRecord
from tandem.ledger.repository import LedgerRepository
from tandem.ledger.service import LedgerService
from tandem.security.auth import require_admin_token

app = FastAPI(title="Tandem Operator & Audit Console")


class BrowserActionRequest(BaseModel):
    """A fenced operator action dispatched to the worker-owned page."""

    owner_id: str
    fencing_token: int
    action: Literal["CLICK", "FILL"]
    selector: str
    frame_selector: Optional[str] = None
    value: Optional[str] = None


class CreateCaseRequest(BaseModel):
    """Request to open (or resume) a Regulation E dispute case via the automation API."""

    member_id: str
    amount: Decimal
    case_id: Optional[str] = None
    card_last4: str = "4112"


@app.on_event("startup")
def _resume_incomplete_cases_on_startup() -> None:
    """Startup recovery scan (H-07): resubmit in-flight cases; never touch NEEDS_HUMAN
    or UNCERTAIN_EFFECT work -- those stay queued for human review on the dashboard."""
    resumed = get_case_runner().recover_incomplete_cases()
    if resumed:
        print(f"[startup] Resubmitted {len(resumed)} in-flight case(s) for resumption: {resumed}")


@app.get("/health")
def healthcheck():
    return {"status": "ok", "app": "tandem-operator-console", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/cases", status_code=202, dependencies=[Depends(require_admin_token)])
def api_create_case(
    request: CreateCaseRequest,
    db: Session = Depends(get_db),
    runner: CaseRunner = Depends(get_case_runner),
):
    """Open a new dispute case and run it on the background automation worker pool.

    Root cause (H-07): no operational automation service existed at all -- the
    workflow could previously only be invoked from tests or `scripts/demo.py`.
    """
    repo = LedgerRepository(db)
    case_id = request.case_id or generate_case_id()
    if request.case_id is not None and repo.get_case(case_id) is not None:
        raise HTTPException(status_code=409, detail=f"Case '{case_id}' already exists")

    repo.create_or_get_case(case_id=case_id, member_id=request.member_id, amount=request.amount)
    db.commit()

    runner.submit_case(case_id, request.member_id, request.amount, request.card_last4)
    return {"case_id": case_id, "status": "ACCEPTED"}


@app.get("/", response_class=HTMLResponse)
def operator_dashboard(db: Session = Depends(get_db)):
    """Overview dashboard displaying all active dispute cases and safety states."""
    service = LedgerService(db)

    cases = list(db.scalars(select(ProcedureCaseRecord).order_by(ProcedureCaseRecord.opened_at.desc())).all())

    rows = []
    for c in cases:
        snapshot = service.reconstruct_case_state(c.case_id)
        status_color = "#27ae60" if c.status == "WAITING_RESOLUTION" else (
            "#d35400" if c.status == "NEEDS_HUMAN" else (
                "#8e44ad" if c.status == "UNCERTAIN_EFFECT" else "#2980b9"
            )
        )
        money_badge = (
            '<span style="background:#e8f5e9; color:#2e7d32; font-weight:bold; padding:3px 8px; border-radius:4px; border:1px solid #a5d6a7;">✓ MOVED ($' + f"{c.amount:.2f})" + '</span>'
            if snapshot.money_moved
            else '<span style="background:#f5f5f5; color:#757575; padding:3px 8px; border-radius:4px;">UNTOUCHED</span>'
        )
        lease_badge = (
            f'<span style="color:#c0392b; font-weight:bold;">{html.escape(snapshot.lease_owner)}</span>'
            if snapshot.lease_owner
            else '<span style="color:#7f8c8d;">UNASSIGNED</span>'
        )

        rows.append(
            f"""<tr>
                <td><a href="/cases/{html.escape(c.case_id)}" style="font-weight:bold; color:#2980b9; text-decoration:none;">{html.escape(c.case_id)}</a></td>
                <td>{html.escape(c.member_id)}</td>
                <td>${c.amount:,.2f} USD</td>
                <td><span style="background:{status_color}; color:#fff; padding:3px 8px; border-radius:3px; font-weight:bold; font-size:11px;">{html.escape(c.status)}</span></td>
                <td>{money_badge}</td>
                <td>{lease_badge}</td>
                <td>{len(snapshot.pending_deadlines)} pending</td>
                <td><a href="/cases/{html.escape(c.case_id)}" style="padding:3px 10px; background:#f0f0f0; border:1px solid #ccc; text-decoration:none; color:#333; border-radius:3px; font-size:11px;">Inspect Audit &gt;&gt;</a></td>
            </tr>"""
        )

    table_body = "".join(rows) if rows else '<tr><td colspan="8" style="text-align:center; color:#888; padding:20px;">No dispute cases currently recorded in procedure ledger.</td></tr>'

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Tandem // Operator &amp; Audit Console</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8f9fa; margin:0; padding:25px; color:#212529; }}
        .header {{ background:#1e293b; color:#fff; padding:18px 24px; border-radius:8px; margin-bottom:25px; display:flex; justify-content:space-between; align-items:center; }}
        .header h1 {{ margin:0; font-size:20px; font-weight:600; letter-spacing:-0.5px; }}
        .header .sub {{ color:#94a3b8; font-size:13px; }}
        .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05); }}
        table {{ width:100%; border-collapse:collapse; font-size:13px; }}
        th {{ background:#f1f5f9; text-align:left; padding:10px 12px; border-bottom:2px solid #cbd5e1; color:#475569; font-weight:600; }}
        td {{ padding:12px; border-bottom:1px solid #f1f5f9; }}
        tr:hover {{ background:#f8fafc; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>TANDEM // EFFECT-AWARE DISPUTE AUTOMATION</h1>
            <div class="sub">Legacy Banking Console Orchestrator &amp; Compliance Audit Ledger (12 CFR 1005.11)</div>
        </div>
        <div>
            <span style="background:#0f766e; color:#ccfbf1; font-size:12px; padding:4px 12px; border-radius:9999px; font-weight:bold;">LIVE LEDGER (SQLITE WAL)</span>
        </div>
    </div>
    <div class="card">
        <h2 style="margin-top:0; font-size:16px; color:#0f172a; margin-bottom:15px;">Dispute Cases &amp; State Machine Oversight</h2>
        <table>
            <thead>
                <tr>
                    <th>Case Reference</th>
                    <th>Member ID</th>
                    <th>Amount</th>
                    <th>State Machine</th>
                    <th>Money Movement</th>
                    <th>Lease Owner</th>
                    <th>Statutory Deadlines</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                {table_body}
            </tbody>
        </table>
    </div>
</body>
</html>""")


@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail_view(case_id: str, db: Session = Depends(get_db)):
    """Timeline, audit records, and operator handoff controls for a single dispute case."""
    service = LedgerService(db)
    repo = LedgerRepository(db)

    try:
        snapshot = service.reconstruct_case_state(case_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Case not found in ledger")

    events = repo.get_events_for_case(case_id)
    executions = repo.get_executions_for_case(case_id)
    deadlines = repo.get_deadlines_for_case(case_id)

    # Deadlines HTML
    dl_rows = []
    for d in deadlines:
        status_style = "background:#dcfce7; color:#15803d;" if d.status == "MET" else "background:#fef9c3; color:#854d0e;"
        dl_rows.append(
            f"""<tr>
                <td><strong>{html.escape(d.deadline_type)}</strong></td>
                <td>{html.escape(d.due_at.strftime('%Y-%m-%d %H:%M:%S UTC'))}</td>
                <td><span style="{status_style} padding:2px 8px; border-radius:4px; font-weight:bold; font-size:11px;">{html.escape(d.status)}</span></td>
                <td>{html.escape(d.resolved_at.strftime('%Y-%m-%d %H:%M:%S UTC')) if d.resolved_at else '<span style="color:#94a3b8;">Pending</span>'}</td>
            </tr>"""
        )

    # Executions HTML
    exec_rows = []
    for exc in executions:
        m_moved = '<strong style="color:#16a34a;">YES ($' + f"{exc.observed_amount:.2f})" + '</strong>' if exc.money_moved else '<span style="color:#64748b;">No</span>'
        status_col = "#16a34a" if exc.status in ("SUCCESS", "COMPLETED") else ("#d97706" if exc.status == "ALREADY_APPLIED" else "#dc2626")
        exec_rows.append(
            f"""<tr>
                <td><strong>{html.escape(exc.capability_id)}</strong></td>
                <td><span style="font-size:11px; padding:2px 6px; background:#f1f5f9; border-radius:3px;">{html.escape(exc.effect_class)}</span></td>
                <td><span style="color:{status_col}; font-weight:bold;">{html.escape(exc.status)}</span></td>
                <td>{m_moved}</td>
                <td><code>{html.escape(exc.audit_ref or "—")}</code></td>
                <td>{html.escape(exc.actor)}</td>
                <td style="font-size:11px; color:#64748b;">{html.escape(exc.started_at.strftime('%H:%M:%S'))}</td>
            </tr>"""
        )

    # Events HTML
    event_rows = []
    for ev in events:
        event_rows.append(
            f"""<div style="border-left:3px solid #3b82f6; padding-left:12px; margin-bottom:12px;">
                <div style="font-size:12px; font-weight:600; color:#1e293b;">
                    {html.escape(ev.event_type)} &bull; <span style="color:#64748b; font-weight:normal;">{html.escape(ev.step_name or '')}</span>
                    <span style="float:right; color:#94a3b8; font-weight:normal;">{html.escape(ev.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC'))}</span>
                </div>
                <div style="font-family:monospace; font-size:11px; color:#475569; background:#f8fafc; padding:4px 8px; border-radius:4px; margin-top:4px;">
                    {html.escape(json.dumps(ev.payload))}
                </div>
            </div>"""
        )

    lease_info = (
        f'<strong style="color:#dc2626;">HELD BY {html.escape(snapshot.lease_owner)}</strong>'
        if snapshot.lease_owner
        else '<span style="color:#16a34a; font-weight:bold;">RELEASED / AVAILABLE</span>'
    )

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Dispute Case {html.escape(case_id)} // Tandem</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f8f9fa; margin:0; padding:25px; color:#212529; }}
        .nav {{ margin-bottom:15px; font-size:13px; }}
        .nav a {{ color:#2563eb; text-decoration:none; font-weight:600; }}
        .header {{ background:#1e293b; color:#fff; padding:18px 24px; border-radius:8px; margin-bottom:20px; }}
        .grid {{ display:grid; grid-template-columns: 2fr 1fr; gap:20px; }}
        .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:20px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05); }}
        table {{ width:100%; border-collapse:collapse; font-size:12px; }}
        th {{ background:#f1f5f9; text-align:left; padding:8px 10px; border-bottom:1px solid #cbd5e1; color:#475569; font-weight:600; }}
        td {{ padding:8px 10px; border-bottom:1px solid #f1f5f9; }}
        .btn {{ padding:6px 14px; border-radius:4px; border:none; font-weight:600; cursor:pointer; font-size:12px; }}
        .btn-claim {{ background:#dc2626; color:#fff; }}
        .btn-release {{ background:#2563eb; color:#fff; }}
    </style>
</head>
<body>
    <div class="nav"><a href="/">&larr; Back to Operator Dashboard</a></div>
    <div class="header">
        <h1 style="margin:0; font-size:22px;">Case Reference: {html.escape(case_id)}</h1>
        <div style="margin-top:6px; font-size:13px; color:#94a3b8;">
            Member ID: <strong>{html.escape(snapshot.member_id)}</strong> &bull;
            Disputed Amount: <strong>${snapshot.amount:,.2f} USD</strong> &bull;
            State: <strong>{html.escape(snapshot.status)}</strong> &bull;
            Lease Status: {lease_info}
        </div>
    </div>

    <div class="grid">
        <div>
            <div class="card">
                <h3 style="margin-top:0; font-size:15px; border-bottom:1px solid #e2e8f0; padding-bottom:8px;">Capability Execution History</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Capability ID</th>
                            <th>Class</th>
                            <th>Outcome</th>
                            <th>Money Moved</th>
                            <th>Audit Ref</th>
                            <th>Actor</th>
                            <th>Time</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(exec_rows) if exec_rows else '<tr><td colspan="7" style="color:#888;">No executions yet.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h3 style="margin-top:0; font-size:15px; border-bottom:1px solid #e2e8f0; padding-bottom:8px;">Append-Only Procedure Ledger Events</h3>
                {''.join(event_rows) if event_rows else '<p style="color:#888;">No events recorded.</p>'}
            </div>
        </div>

        <div>
            <div class="card">
                <h3 style="margin-top:0; font-size:15px; border-bottom:1px solid #e2e8f0; padding-bottom:8px;">12 CFR 1005.11 Deadlines</h3>
                <table>
                    <thead>
                        <tr><th>Type</th><th>Due At</th><th>Status</th><th>Resolved</th></tr>
                    </thead>
                    <tbody>
                        {''.join(dl_rows) if dl_rows else '<tr><td colspan="4" style="color:#888;">No deadlines recorded.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h3 style="margin-top:0; font-size:15px; border-bottom:1px solid #e2e8f0; padding-bottom:8px;">Operator Handoff Controls</h3>
                <p style="font-size:12px; color:#64748b;">Enforces single-owner lease mutual exclusion between automation and human operators.</p>
                <form method="POST" action="/cases/{html.escape(case_id)}/claim_lease" style="margin-bottom:10px;">
                    <input type="hidden" name="operator_id" value="operator_ui" />
                    <input type="hidden" name="admin_token" value="{html.escape(settings.tandem_admin_token)}" />
                    <button type="submit" class="btn btn-claim" style="width:100%;">Claim Operator Lease</button>
                </form>
                <form method="POST" action="/cases/{html.escape(case_id)}/release_lease">
                    <input type="hidden" name="admin_token" value="{html.escape(settings.tandem_admin_token)}" />
                    <button type="submit" class="btn btn-release" style="width:100%;">Release Lease to Automation</button>
                </form>
                <div style="margin-top:15px; padding-top:10px; border-top:1px solid #eee;">
                    <a href="http://127.0.0.1:8001/" target="_blank" style="font-size:12px; color:#2563eb; text-decoration:none; font-weight:600;">Open Core Bank Simulator &nearr;</a>
                </div>
            </div>
        </div>
    </div>
</body>
</html>""")


@app.post("/cases/{case_id}/claim_lease", dependencies=[Depends(require_admin_token)])
def handle_claim_lease(case_id: str, operator_id: str = Form("operator_ui"), db: Session = Depends(get_db)):
    coordinator = HandoffCoordinator(db)
    try:
        coordinator.claim_operator_lease(case_id, operator_id=operator_id)
    except LeaseConflictError as err:
        raise HTTPException(status_code=409, detail=str(err))
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/release_lease", dependencies=[Depends(require_admin_token)])
def handle_release_lease(case_id: str, db: Session = Depends(get_db)):
    repo = LedgerRepository(db)
    repo.release_lease(case_id)
    repo.acquire_lease(case_id=case_id, owner="AUTOMATION")
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@app.get("/api/cases/{case_id}")
def api_get_case_state(case_id: str, db: Session = Depends(get_db)):
    service = LedgerService(db)
    try:
        snapshot = service.reconstruct_case_state(case_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))

    return {
        "case_id": snapshot.case_id,
        "member_id": snapshot.member_id,
        "amount": snapshot.amount,
        "status": snapshot.status,
        "money_moved": snapshot.money_moved,
        "completed_capabilities": snapshot.completed_capabilities,
        "latest_memo_ref": snapshot.latest_memo_ref,
        "lease_owner": snapshot.lease_owner,
        "requires_human": snapshot.requires_human,
        "pending_deadlines": [
            {"type": d.deadline_type, "due_at": d.due_at.isoformat(), "status": d.status}
            for d in snapshot.pending_deadlines
        ],
        "events_count": snapshot.events_count,
    }


def _operator_snapshot(result: Dict[str, Any]) -> Dict[str, Any]:
    """Remove cookie values while retaining continuity evidence for operators."""

    return {key: value for key, value in result.items() if key != "cookies"}


@app.get("/api/browser-sessions/{session_id}")
def api_get_browser_session(session_id: str, db: Session = Depends(get_db)):
    """Return a screenshot reference, current URL, and observed controls."""

    repo = LedgerRepository(db)
    record = repo.get_browser_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Browser session not found")
    try:
        result = browser_session_broker.execute(session_id, "SNAPSHOT")
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.update_browser_session(session_id, current_url=str(result["url"]))
    db.commit()
    return _operator_snapshot(result)


@app.post(
    "/api/browser-sessions/{session_id}/actions",
    dependencies=[Depends(require_admin_token)],
)
def api_execute_browser_action(
    session_id: str,
    request: BrowserActionRequest,
    db: Session = Depends(get_db),
):
    """Execute a bounded action in the same context after validating ownership."""

    repo = LedgerRepository(db)
    record = repo.get_browser_session(session_id)
    if record is None or record.status != "ACTIVE":
        raise HTTPException(status_code=404, detail="Active browser session not found")
    if not repo.validate_lease_token(
        record.case_id, request.owner_id, request.fencing_token
    ):
        raise HTTPException(status_code=409, detail="Ownership fencing token is stale")
    lease = repo.get_lease(record.case_id)
    assert lease is not None
    if request.action == "FILL" and request.value is None:
        raise HTTPException(status_code=422, detail="FILL requires value")
    repo.heartbeat_lease(record.case_id, request.owner_id, request.fencing_token)
    db.commit()
    try:
        result = browser_session_broker.execute(
            session_id,
            request.action,
            selector=request.selector,
            frame_selector=request.frame_selector,
            value=request.value,
        )
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.update_browser_session(session_id, current_url=str(result["url"]))
    repo.record_event(
        record.case_id,
        "BROWSER_ACTION_EXECUTED",
        "browser_session",
        actor=lease.owner_type,
        payload={
            "session_id": session_id,
            "owner_id": request.owner_id,
            "fencing_token": request.fencing_token,
            "action": request.action,
            "selector": request.selector,
            "url": result["url"],
        },
    )
    db.commit()
    return _operator_snapshot(result)
