"""Member Notice / Document Delivery System Simulator.

Implements:
- Notice creation and delivery confirmation
- Regulatory deadline persistence (12 CFR 1005.11 2-day notice)
- Failure switch simulation
- Notice status lookup
"""

import html
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from simulators.documents.state import document_state
from tandem.config import settings
from tandem.security.auth import require_admin_token

app = FastAPI(title="Document & Member Notice Delivery Simulator")


@app.post("/api/reset", dependencies=[Depends(require_admin_token)])
async def api_reset():
    document_state.reset()
    return {"status": "ok", "message": "Document system state reset"}


@app.post("/api/set_failure", dependencies=[Depends(require_admin_token)])
async def api_set_failure(fail: bool = True):
    document_state.simulate_failure = fail
    return {"status": "ok", "simulate_failure": fail}


@app.get("/api/notices/{case_id}")
async def api_get_notice(case_id: str):
    notice = document_state.find_by_case(case_id)
    if not notice:
        return JSONResponse(status_code=404, content={"error": "Notice not found"})
    return {
        "notice_id": notice.notice_id,
        "case_id": notice.case_id,
        "member_id": notice.member_id,
        "notice_type": notice.notice_type,
        "amount": notice.amount,
        "deadline_due_at": notice.deadline_due_at,
        "sent_at": notice.sent_at,
        "status": notice.status,
        "institution_id": notice.institution_id,
        "procedure_id": notice.procedure_id,
        "capability_id": notice.capability_id,
        "account_id": notice.account_id,
        "currency": notice.currency,
        "business_reference": notice.business_reference,
        "effect_count": document_state.effect_count(case_id),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse("""<!DOCTYPE html>
<html>
<head>
    <title>Document & Member Communication Gateway</title>
    <style>
        body { font-family: Arial, sans-serif; font-size: 12px; background: #f4f6f8; margin: 20px; }
        .box { background: #fff; border: 1px solid #dcdfe6; padding: 20px; max-width: 500px; margin: 0 auto; border-radius: 4px; }
        .title { color: #303133; font-size: 15px; font-weight: bold; margin-bottom: 15px; }
        .field { margin-bottom: 12px; }
        .field label { display: block; font-weight: bold; margin-bottom: 4px; }
        .field input, .field select { width: 100%; padding: 6px; box-sizing: border-box; border: 1px solid #dcdfe6; border-radius: 3px; }
        .btn { background: #409eff; color: #fff; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-weight: bold; }
    </style>
</head>
<body>
    <div class="box">
        <div class="title">DOCUMENT DISPATCH // REG E NOTICE GENERATOR</div>
        <form method="POST" action="/notices/send">
            <div class="field">
                <label for="case_id">Case Reference ID:</label>
                <input type="text" id="case_id" name="case_id" value="D-8842" required />
            </div>
            <div class="field">
                <label for="member_id">Member ID:</label>
                <input type="text" id="member_id" name="member_id" value="8830142" required />
            </div>
            <div class="field">
                <label for="notice_type">Notice Classification:</label>
                <select id="notice_type" name="notice_type">
                    <option value="REG_E_PROVISIONAL_CREDIT_DISCLOSURE">Reg E 12 CFR 1005.11 - Provisional Credit Notice</option>
                    <option value="REG_E_FINAL_RESOLUTION">Reg E - Final Dispute Resolution Letter</option>
                </select>
            </div>
            <div class="field">
                <label for="amount">Credited Amount (USD):</label>
                <input type="number" id="amount" name="amount" step="0.01" value="340.00" required />
            </div>
            <div class="field">
                <label for="deadline">Compliance Notice Deadline:</label>
                <input type="text" id="deadline" name="deadline_due_at" value="2026-09-04 17:00:00" required />
            </div>
            <input type="hidden" name="admin_token" value="__ADMIN_TOKEN__" />
            <button type="submit" id="btn_send_notice" class="btn">Dispatch Member Disclosure Notice</button>
        </form>
    </div>
</body>
</html>""".replace("__ADMIN_TOKEN__", html.escape(settings.tandem_admin_token)))


@app.post(
    "/notices/send",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_token)],
)
async def send_notice(
    case_id: str = Form(...),
    member_id: str = Form(...),
    notice_type: str = Form(...),
    amount: Decimal = Form(...),
    deadline_due_at: str = Form(...),
    institution_id: str = Form("alpha"),
    procedure_id: str = Form("reg_e_dispute"),
    capability_id: str = Form("docs.send_notice"),
    account_id: str = Form(""),
    currency: str = Form("USD"),
    business_reference: str = Form(""),
):
    if document_state.simulate_failure:
        raise HTTPException(
            status_code=500,
            detail="Document Dispatch Gateway Error: Connection to Mail / Electronic Delivery Provider Failed",
        )

    notice = document_state.send_notice(
        case_id=case_id,
        member_id=member_id,
        notice_type=notice_type,
        amount=amount,
        deadline_due_at=deadline_due_at,
        institution_id=institution_id,
        procedure_id=procedure_id,
        capability_id=capability_id,
        account_id=account_id,
        currency=currency,
        business_reference=business_reference or case_id,
    )

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Notice Sent</title>
    <style>
        body {{ font-family: Arial, sans-serif; font-size: 12px; background: #f4f6f8; padding: 20px; }}
        .card {{ background: #fff; border: 2px solid #67c23a; padding: 20px; max-width: 500px; margin: 0 auto; border-radius: 4px; }}
        .success {{ color: #67c23a; font-size: 16px; font-weight: bold; margin-bottom: 12px; }}
    </style>
</head>
<body>
    <div class="card" id="notice_receipt">
        <div class="success">✓ MEMBER COMPLIANCE NOTICE DISPATCHED</div>
        <div>Notice ID: <strong id="notice_id">{html.escape(notice.notice_id)}</strong></div>
        <div>Case Reference: <strong>{html.escape(notice.case_id)}</strong></div>
        <div>Recipient Member: <span>{html.escape(notice.member_id)}</span></div>
        <div>Disclosure Amount: ${notice.amount:,.2f} USD</div>
        <div>Dispatched At: <span id="notice_sent_at">{html.escape(notice.sent_at)}</span></div>
        <div>Statutory Deadline Met: <strong style="color: green;">MET (Due: {html.escape(notice.deadline_due_at)})</strong></div>
        <div>Status: <strong id="notice_status">{html.escape(notice.status)}</strong></div>
    </div>
</body>
</html>""")


@app.get("/notices/lookup", response_class=HTMLResponse)
async def lookup_notice(case_id: Optional[str] = Query(None)):
    result_html = ""
    case_query = case_id or ""

    if case_query:
        notice = document_state.find_by_case(case_query)
        if notice:
            result_html = f"""
            <div id="notice_found" style="background: #f0f9eb; border: 1px solid #e1f3d8; padding: 12px; margin-top: 12px;">
                <h4 style="color: #67c23a; margin-top:0;">NOTICE FOUND</h4>
                <div>Notice ID: <strong>{html.escape(notice.notice_id)}</strong></div>
                <div>Status: <strong id="inquiry_notice_status">{html.escape(notice.status)}</strong></div>
                <div>Sent At: <span>{html.escape(notice.sent_at)}</span></div>
            </div>
            """
        else:
            result_html = f"""
            <div id="notice_not_found" style="background: #fef0f0; border: 1px solid #fde2e2; padding: 12px; margin-top: 12px;">
                <span style="color: #f56c6c; font-weight: bold;">NO NOTICE RECORDED FOR CASE {html.escape(case_query)}</span>
            </div>
            """

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Notice Status Lookup</title>
</head>
<body style="font-family: Arial, sans-serif; font-size:12px; padding:20px;">
    <h3>LOOKUP MEMBER NOTICE STATUS</h3>
    <form method="GET" action="/notices/lookup">
        <label>Case ID:</label>
        <input type="text" name="case_id" value="{html.escape(case_query)}" />
        <button type="submit">Search</button>
    </form>
    {result_html}
</body>
</html>""")
