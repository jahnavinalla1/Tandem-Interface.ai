"""Card Processor Portal Simulator (CO-OP / PSCU / Visa DPS).

Provides:
- Chargeback filing interface
- Reference generation
- Failure switches: SESSION_EXPIRED, TIMEOUT_AFTER_SUBMIT, SYSTEM_FAILURE
- Postcheck/reconciliation lookup endpoint
"""

import html
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from simulators.processor.state import processor_state
from tandem.config import settings
from tandem.security.auth import require_admin_token

app = FastAPI(title="Card Processor Portal Simulator (Visa DPS / PSCU)")


@app.post("/api/reset", dependencies=[Depends(require_admin_token)])
async def api_reset():
    processor_state.reset()
    return {"status": "ok", "message": "Processor state reset"}


@app.post("/api/set_mode", dependencies=[Depends(require_admin_token)])
async def api_set_mode(
    session_expired: bool = False,
    timeout_after_submit: bool = False,
    system_failure: bool = False,
    fail_lookup_when_present: bool = False,
):
    processor_state.session_expired = session_expired
    processor_state.timeout_after_submit = timeout_after_submit
    processor_state.system_failure = system_failure
    processor_state.fail_lookup_when_present = fail_lookup_when_present
    return {
        "status": "ok",
        "session_expired": session_expired,
        "timeout_after_submit": timeout_after_submit,
        "system_failure": system_failure,
        "fail_lookup_when_present": fail_lookup_when_present,
    }


@app.get("/api/chargebacks/{case_id}")
async def api_get_chargeback(case_id: str):
    cb = processor_state.find_by_case(case_id)
    if not cb:
        return JSONResponse(status_code=404, content={"error": "Chargeback not found"})
    if processor_state.fail_lookup_when_present:
        return JSONResponse(status_code=503, content={"error": "Filing inquiry unavailable"})
    return {
        "chargeback_id": cb.chargeback_id,
        "case_id": cb.case_id,
        "card_last4": cb.card_last4,
        "amount": cb.amount,
        "network_ref": cb.network_ref,
        "status": cb.status,
        "created_at": cb.created_at,
        "institution_id": cb.institution_id,
        "procedure_id": cb.procedure_id,
        "capability_id": cb.capability_id,
        "member_id": cb.member_id,
        "account_id": cb.account_id,
        "currency": cb.currency,
        "business_reference": cb.business_reference,
        "effect_count": processor_state.effect_count(case_id),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    if processor_state.session_expired:
        return session_expired_response()

    return HTMLResponse("""<!DOCTYPE html>
<html>
<head>
    <title>Card Processor Gateway - Chargeback Services</title>
    <style>
        body { font-family: Arial, sans-serif; font-size: 12px; background: #e8ecf0; margin: 0; padding: 20px; }
        .card { background: #ffffff; border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); padding: 20px; max-width: 500px; margin: 0 auto; }
        .header { background: #1a365d; color: #fff; padding: 10px 15px; border-radius: 4px 4px 0 0; margin: -20px -20px 20px -20px; font-weight: bold; }
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; font-weight: bold; margin-bottom: 4px; }
        .form-group input { width: 100%; padding: 6px; box-sizing: border-box; border: 1px solid #cbd5e0; border-radius: 3px; }
        .btn-submit { background: #2b6cb0; color: #fff; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-weight: bold; }
    </style>
</head>
<body>
    <div class="card">
        <div class="header">CARD PROCESSOR PORTAL // DISPUTE FILING GATEWAY</div>
        <form method="POST" action="/chargeback/file">
            <div class="form-group">
                <label for="case_id">Case Reference ID:</label>
                <input type="text" id="case_id" name="case_id" value="D-8842" required />
            </div>
            <div class="form-group">
                <label for="card_last4">Card Last 4 Digits:</label>
                <input type="text" id="card_last4" name="card_last4" value="4112" maxlength="4" required />
            </div>
            <div class="form-group">
                <label for="amount">Disputed Amount (USD):</label>
                <input type="number" id="amount" name="amount" step="0.01" value="340.00" required />
            </div>
            <div class="form-group">
                <label for="reason">Dispute Reason Code:</label>
                <input type="text" id="reason" name="dispute_reason" value="10.4 - Fraud / Unauthorized Transaction" />
            </div>
            <input type="hidden" name="admin_token" value="__ADMIN_TOKEN__" />
            <button type="submit" id="btn_file_chargeback" class="btn-submit">Transmit Chargeback Filing</button>
        </form>
        <div style="margin-top: 15px; border-top: 1px solid #edf2f7; padding-top: 10px;">
            <a href="/chargeback/lookup">Inquire Existing Filing</a>
        </div>
    </div>
</body>
</html>""".replace("__ADMIN_TOKEN__", html.escape(settings.tandem_admin_token)))


@app.post(
    "/chargeback/file",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_token)],
)
async def file_chargeback(
    case_id: str = Form(...),
    card_last4: str = Form(...),
    amount: Decimal = Form(...),
    dispute_reason: str = Form("Unauthorized Transaction"),
    institution_id: str = Form("alpha"),
    procedure_id: str = Form("reg_e_dispute"),
    capability_id: str = Form("processor.file_chargeback"),
    member_id: str = Form(""),
    account_id: str = Form(""),
    currency: str = Form("USD"),
    business_reference: str = Form(""),
):
    if processor_state.session_expired:
        return session_expired_response()

    if processor_state.system_failure:
        raise HTTPException(
            status_code=500, detail="Card Processor Internal Server Error (Downstream Network Fail)"
        )

    if processor_state.timeout_after_submit:
        # THE MONEY MOVED / RECORD WAS COMMITTED INTERNALLY, BUT CONNECTION DROPPED BEFORE CONFIRMATION!
        # This is the exact scenario required for Scenario 8 (UNCERTAIN_EFFECT).
        processor_state.file_chargeback(
            case_id=case_id,
            card_last4=card_last4,
            amount=amount,
            dispute_reason=dispute_reason,
            institution_id=institution_id,
            procedure_id=procedure_id,
            capability_id=capability_id,
            member_id=member_id,
            account_id=account_id,
            currency=currency,
            business_reference=business_reference or case_id,
        )
        raise HTTPException(
            status_code=504,
            detail="Gateway Timeout: Upstream card network connection dropped before confirmation was delivered",
        )

    # Standard successful submission
    cb = processor_state.file_chargeback(
        case_id=case_id,
        card_last4=card_last4,
        amount=amount,
        dispute_reason=dispute_reason,
        institution_id=institution_id,
        procedure_id=procedure_id,
        capability_id=capability_id,
        member_id=member_id,
        account_id=account_id,
        currency=currency,
        business_reference=business_reference or case_id,
    )

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Chargeback Transmitted</title>
    <style>
        body {{ font-family: Arial, sans-serif; font-size: 12px; background: #e8ecf0; padding: 20px; }}
        .card {{ background: #ffffff; border-radius: 4px; border: 2px solid #28a745; padding: 20px; max-width: 500px; margin: 0 auto; }}
        .success-title {{ color: #28a745; font-size: 16px; font-weight: bold; margin-bottom: 12px; }}
    </style>
</head>
<body>
    <div class="card" id="chargeback_receipt">
        <div class="success-title">✓ CHARGEBACK FILED WITH NETWORK</div>
        <div>Network Reference: <strong id="processor_network_ref" class="network-ref">{html.escape(cb.network_ref)}</strong></div>
        <div>Filing ID: <span id="processor_filing_id">{html.escape(cb.chargeback_id)}</span></div>
        <div>Case ID: <strong>{html.escape(cb.case_id)}</strong></div>
        <div>Card: **** **** **** {html.escape(cb.card_last4)}</div>
        <div>Amount: ${cb.amount:,.2f} USD</div>
        <div>Timestamp: {html.escape(cb.created_at)}</div>
        <div style="margin-top: 15px;">
            <a href="/">Back to Portal</a>
        </div>
    </div>
</body>
</html>""")


@app.get("/chargeback/lookup", response_class=HTMLResponse)
async def lookup_chargeback(case_id: Optional[str] = Query(None)):
    """Reconciliation and postcheck inquiry endpoint."""
    if processor_state.session_expired:
        return session_expired_response()

    result_html = ""
    case_query = case_id or ""

    if case_query:
        cb = processor_state.find_by_case(case_query)
        if cb:
            result_html = f"""
            <div id="reconciliation_found" style="background: #e6ffed; border: 1px solid #52c41a; padding: 12px; border-radius: 4px; margin-top: 12px;">
                <h4 style="color: #52c41a; margin-top:0;">CONFIRMED NETWORK FILING</h4>
                <div>Case ID: <strong class="inquiry-case-id">{html.escape(cb.case_id)}</strong></div>
                <div>Network Ref: <strong class="inquiry-network-ref" id="inquiry_network_ref">{html.escape(cb.network_ref)}</strong></div>
                <div>Amount: ${cb.amount:,.2f} USD</div>
                <div>Status: <strong class="inquiry-status">{html.escape(cb.status)}</strong></div>
            </div>
            """
        else:
            result_html = f"""
            <div id="reconciliation_not_found" style="background: #fff1f0; border: 1px solid #ffa39e; padding: 12px; border-radius: 4px; margin-top: 12px;">
                <span style="color: #cf1322; font-weight: bold;">NO RECORD FOUND FOR CASE {html.escape(case_query)}</span>
            </div>
            """

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Inquire Chargeback Status</title>
    <style>
        body {{ font-family: Arial, sans-serif; font-size: 12px; background: #e8ecf0; padding: 20px; }}
        .card {{ background: #fff; padding: 20px; max-width: 500px; margin: 0 auto; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="card">
        <h3 style="margin-top:0; color: #1a365d;">PROCESSOR INQUIRY / RECONCILIATION</h3>
        <form method="GET" action="/chargeback/lookup">
            <label for="case_id">Case ID:</label>
            <input type="text" id="case_id" name="case_id" value="{html.escape(case_query)}" style="padding:5px; width: 180px;" />
            <button type="submit" style="padding: 5px 12px;">Search Processor</button>
        </form>
        {result_html}
    </div>
</body>
</html>""")


def session_expired_response() -> HTMLResponse:
    return HTMLResponse(
        """<!DOCTYPE html>
<html>
<head>
    <title>Session Expired</title>
    <style>
        body { font-family: Arial, sans-serif; background: #fdf2f2; text-align: center; padding-top: 40px; }
        .error-card { background: #fff; border: 2px solid #e53e3e; padding: 20px; display: inline-block; max-width: 400px; }
    </style>
</head>
<body>
    <div class="error-card" id="processor_session_expired">
        <h2 style="color: #e53e3e; margin-top:0;">PROCESSOR SESSION EXPIRED</h2>
        <p>Your session with the card network gateway has expired. Please re-authenticate.</p>
        <p><strong>Code: PROCESSOR_SESSION_TIMEOUT_401</strong></p>
    </div>
</body>
</html>""",
        status_code=401,
    )
