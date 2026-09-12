"""Hostile Core Banking Simulator Application.

Implements a realistic legacy banking console with:
- Nested iframes
- Dynamic unstable element IDs
- Table layouts with non-deterministic row ordering
- Fuzzy search returning transposed confusable accounts
- Control-scoped containers
- Confirmation screens
- Optional compliance interstitial
- Session expiry simulation
"""

import asyncio
import html
import random
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse

from simulators.core_bank.state import core_bank_state
from tandem.config import settings
from tandem.security.auth import require_admin_token

app = FastAPI(title="Legacy Core Banking Platform (Symitar/Keystone Simulator)")


def dynamic_id(prefix: str) -> str:
    """Generate unstable dynamic element ID to simulate legacy hostile UI."""
    return f"{prefix}_{random.randint(100000, 999999)}"


# ---------------------------------------------------------------------------
# API Management Endpoints (For test fixtures, reset, and state queries)
# ---------------------------------------------------------------------------
@app.post("/api/reset", dependencies=[Depends(require_admin_token)])
async def api_reset():
    core_bank_state.seed()
    return {"status": "ok", "message": "Core bank simulator state reset to initial seed"}


@app.post("/api/set_compliance_interstitial", dependencies=[Depends(require_admin_token)])
async def api_set_compliance(required: bool = True):
    core_bank_state.require_compliance_interstitial = required
    core_bank_state.compliance_cleared = False
    return {"status": "ok", "require_compliance_interstitial": required}


@app.post("/api/set_session_valid", dependencies=[Depends(require_admin_token)])
async def api_set_session(valid: bool = True):
    core_bank_state.session_valid = valid
    return {"status": "ok", "session_valid": valid}


@app.get("/api/member/{member_id}")
async def api_get_member(member_id: str):
    member = core_bank_state.members.get(member_id)
    if not member:
        return JSONResponse(status_code=404, content={"error": "Member not found"})
    return {
        "member_id": member.member_id,
        "name": f"{member.first_name} {member.last_name}",
        "account_id": member.account_id,
        "balance": member.balance,
        "transactions_count": len(member.transactions),
    }


@app.get("/api/credits/{case_id}")
async def api_get_credits(case_id: str):
    credit = core_bank_state.find_credit_by_case(case_id)
    if not credit:
        return JSONResponse(status_code=404, content={"error": "No credit found for case"})
    if core_bank_state.fail_credit_lookup_when_present:
        return JSONResponse(status_code=503, content={"error": "Credit inquiry unavailable"})
    return {
        "case_id": credit.case_id,
        "member_id": credit.member_id,
        "account_id": credit.account_id,
        "amount": credit.amount,
        "currency": credit.currency,
        "business_reference": credit.business_reference,
        "institution_id": credit.institution_id,
        "procedure_id": "reg_e_dispute",
        "capability_id": "core.post_provisional_credit",
        "memo_code": credit.memo_code,
        "posted_at": credit.posted_at,
        "status": credit.status,
        "effect_count": core_bank_state.effect_count(case_id),
    }


# ---------------------------------------------------------------------------
# Hostile UI: Frameset and Shell
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    """Top-level legacy portal with frame layout."""
    return """<!DOCTYPE html>
<html>
<head>
    <title>Core Banking Platform - Symitar / Keystone Legacy Host</title>
    <style>
        body, html { margin: 0; padding: 0; height: 100%; font-family: Tahoma, 'MS Sans Serif', sans-serif; font-size: 11px; }
        #header { background-color: #003366; color: #ffffff; padding: 6px 12px; font-weight: bold; border-bottom: 2px solid #999; }
        #container { display: flex; height: calc(100% - 30px); }
        #nav { width: 190px; background-color: #d4d0c8; border-right: 2px groove #fff; padding: 8px; box-sizing: border-box; }
        #nav a { display: block; padding: 5px 8px; margin-bottom: 4px; background: #ece9d8; border: 1px outset #fff; color: #000; text-decoration: none; font-size: 11px; }
        #nav a:hover { background: #ffffff; }
        #content-frame { flex: 1; border: none; width: 100%; height: 100%; }
    </style>
</head>
<body>
    <div id="header">
        CORE CONSOLE // SYM-KEYSTONE v11.4.82 // BRANCH: 001-CENTRAL // OPERATOR: AUTO_SVC
    </div>
    <div id="container">
        <div id="nav">
            <div style="font-weight:bold; margin-bottom:8px; border-bottom:1px solid #999; padding-bottom:3px;">SYSTEM MODULES</div>
            <a href="/workspace/search" target="core_workspace_frame">1. Member Search</a>
            <a href="/workspace/memos" target="core_workspace_frame">2. Memo & Ref Lookup</a>
            <a href="/workspace/credit/entry?member_id=8830142" target="core_workspace_frame">3. Direct Credit Entry</a>
        </div>
        <iframe name="core_workspace_frame" id="core_workspace_frame" src="/workspace/search"></iframe>
    </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Workspace Pages (Rendered inside the workspace iframe)
# ---------------------------------------------------------------------------
@app.get("/workspace/search", response_class=HTMLResponse)
async def workspace_search(q: Optional[str] = None):
    if not core_bank_state.session_valid:
        return session_expired_response()

    search_val = q or ""
    results_html = ""

    if search_val:
        members = core_bank_state.search_members(search_val, randomize_order=True)
        if members:
            rows = []
            for m in members:
                btn_id = dynamic_id("btn_post_cred")
                row_id = dynamic_id("row")
                rows.append(f"""
                <tr class="member-record-row" id="{row_id}">
                    <td class="cell-member-id" style="font-weight:bold; color:#002244;">{html.escape(m.member_id)}</td>
                    <td class="cell-account-id">{html.escape(m.account_id)}</td>
                    <td class="cell-name">{html.escape(m.first_name)} {html.escape(m.last_name)}</td>
                    <td class="cell-balance" style="text-align:right;">${m.balance:,.2f}</td>
                    <td class="cell-action" style="text-align:center;">
                        <a href="/workspace/credit/entry?member_id={m.member_id}"
                           id="{btn_id}"
                           class="action-credit-btn"
                           style="display:inline-block; padding:3px 8px; background:#d4d0c8; border:2px outset #fff; text-decoration:none; color:#000;">
                           Post Provisional Credit
                        </a>
                    </td>
                </tr>
                """)
            results_html = f"""
            <table class="legacy-data-table" border="1" cellpadding="4" cellspacing="0" style="width:100%; border-collapse:collapse; margin-top:10px; background:#fff;">
                <tr style="background:#e0dfe3; font-weight:bold;">
                    <th>Member ID</th>
                    <th>Account No</th>
                    <th>Full Name</th>
                    <th>Balance (USD)</th>
                    <th>Operation</th>
                </tr>
                {"".join(rows)}
            </table>
            """
        else:
            results_html = "<p style='color:red; margin-top:10px;'>No records found matching search criteria.</p>"

    input_id = dynamic_id("search_input")
    submit_id = dynamic_id("search_btn")

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Member Search</title>
    <style>
        body {{ font-family: Tahoma, sans-serif; font-size: 11px; background:#f0f0f0; margin: 12px; }}
        .panel {{ background:#ffffff; border: 2px groove #ffffff; padding: 12px; }}
        h2 {{ margin-top:0; font-size:13px; color:#003366; }}
    </style>
</head>
<body>
    <div class="panel">
        <h2>MEMBER INQUIRY & SERVICING</h2>
        <form method="GET" action="/workspace/search">
            <label for="{input_id}">Enter Member ID or Account Number:</label>
            <input type="text" id="{input_id}" name="q" value="{html.escape(search_val)}" style="padding:3px; width:220px;" autofocus />
            <button type="submit" id="{submit_id}" style="padding:3px 12px; background:#d4d0c8; border:2px outset #fff;">Search Core</button>
        </form>
        {results_html}
    </div>
</body>
</html>""")


@app.get("/workspace/credit/entry", response_class=HTMLResponse)
async def workspace_credit_entry(member_id: str = Query(...)):
    if not core_bank_state.session_valid:
        return session_expired_response()

    member = core_bank_state.members.get(member_id)
    if not member:
        return HTMLResponse("<h2>Error: Member record not found.</h2>", status_code=404)

    # Compliance review interstitial simulation (used in Demo 6)
    if core_bank_state.require_compliance_interstitial and not core_bank_state.compliance_cleared:
        interstitial_btn_id = dynamic_id("btn_clear_compliance")
        return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Compliance Review Required</title>
    <style>
        body {{ font-family: Tahoma, sans-serif; background:#ffefef; padding:20px; font-size:12px; }}
        .interstitial-box {{ background:#ffffff; border:3px solid #cc0000; padding:20px; max-width:600px; margin:0 auto; }}
        h1 {{ color:#cc0000; font-size:16px; margin-top:0; }}
    </style>
</head>
<body>
    <div class="interstitial-box" id="compliance_interstitial_panel">
        <h1>ATTENTION: COMPLIANCE INTERSTITIAL REVIEW REQUIRED</h1>
        <p><strong>Case Review Alert:</strong> This member account is subject to heightened Regulation E / BSA verification.</p>
        <p>Automation cannot proceed without manual human operator review and sign-off.</p>
        <div style="background:#eee; padding:10px; border:1px solid #ccc; margin:15px 0;">
            Target Member: <span id="interstitial_member_id">{html.escape(member.member_id)}</span> ({html.escape(member.first_name)} {html.escape(member.last_name)})<br/>
            Target Account: <span id="interstitial_account_id">{html.escape(member.account_id)}</span>
        </div>
        <form method="POST" action="/workspace/credit/clear_compliance">
            <input type="hidden" name="member_id" value="{html.escape(member.member_id)}" />
            <input type="hidden" name="admin_token" value="{html.escape(settings.tandem_admin_token)}" />
            <button type="submit" id="{interstitial_btn_id}" class="operator-signoff-btn" style="background:#cc0000; color:#fff; font-weight:bold; padding:8px 16px; border:none; cursor:pointer;">
                [OPERATOR SIGN-OFF] Acknowledge Compliance & Resume
            </button>
        </form>
    </div>
</body>
</html>""")

    form_id = dynamic_id("credit_form")
    case_input_id = dynamic_id("case_ref")
    amount_input_id = dynamic_id("credit_amount")
    memo_input_id = dynamic_id("credit_memo")
    submit_btn_id = dynamic_id("btn_proceed_confirm")

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Post Provisional Credit</title>
    <style>
        body {{ font-family: Tahoma, sans-serif; font-size:11px; background:#f0f0f0; margin:12px; }}
        .container-panel {{ background:#ffffff; border:2px groove #fff; padding:15px; }}
        .member-summary-box {{ background:#f9f9e8; border:1px solid #d0c080; padding:8px; margin-bottom:12px; }}
        .form-row {{ margin-bottom:8px; }}
        .form-row label {{ display:inline-block; width:140px; font-weight:bold; }}
    </style>
</head>
<body>
    <div class="container-panel" id="credit_action_container">
        <h2 style="margin-top:0; color:#003366;">POST PROVISIONAL CREDIT ENTRY</h2>

        <!-- Scoped container holding member identity & account -->
        <div class="member-summary-box" id="scoped_target_member_box">
            <div>Target Member ID: <span class="scoped-member-id" id="display_member_id" style="font-weight:bold;">{html.escape(member.member_id)}</span></div>
            <div>Account Number: <span class="scoped-account-id" id="display_account_id" style="font-weight:bold;">{html.escape(member.account_id)}</span></div>
            <div>Member Name: <span class="scoped-name">{html.escape(member.first_name)} {html.escape(member.last_name)}</span></div>
            <div>Current Ledger Balance: <span class="scoped-balance">${member.balance:,.2f}</span></div>
        </div>

        <form id="{form_id}" method="POST" action="/workspace/credit/confirm">
            <input type="hidden" name="member_id" value="{html.escape(member.member_id)}" />
            <input type="hidden" name="account_id" value="{html.escape(member.account_id)}" />
            <input type="hidden" name="admin_token" value="{html.escape(settings.tandem_admin_token)}" />

            <div class="form-row">
                <label for="{case_input_id}">Case / Dispute Ref:</label>
                <input type="text" id="{case_input_id}" name="case_id" value="D-8842" style="width:160px;" required />
            </div>
            <div class="form-row">
                <label for="{amount_input_id}">Credit Amount (USD):</label>
                <input type="number" id="{amount_input_id}" name="amount" step="0.01" value="340.00" style="width:160px;" required />
            </div>
            <div class="form-row">
                <label for="{memo_input_id}">Adjustment Reason:</label>
                <input type="text" id="{memo_input_id}" name="reason" value="Reg E Provisional Credit - Card Dispute" style="width:260px;" />
            </div>

            <div style="margin-top:15px;">
                <button type="submit" id="{submit_btn_id}" class="btn-proceed" style="background:#003366; color:#fff; font-weight:bold; padding:5px 15px; border:1px solid #001122; cursor:pointer;">
                    Review & Continue &gt;&gt;
                </button>
            </div>
        </form>
    </div>
</body>
</html>""")


@app.post(
    "/workspace/credit/clear_compliance",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_token)],
)
async def clear_compliance(member_id: str = Form(...)):
    core_bank_state.compliance_cleared = True
    return HTMLResponse(f"""
    <p>Compliance cleared by operator. Redirecting...</p>
    <script>window.location.href = '/workspace/credit/entry?member_id={html.escape(member_id)}';</script>
    """)


@app.post(
    "/workspace/credit/confirm",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_token)],
)
async def workspace_credit_confirm(
    member_id: str = Form(...),
    account_id: str = Form(...),
    case_id: str = Form(...),
    amount: Decimal = Form(...),
    reason: str = Form(""),
):
    if not core_bank_state.session_valid:
        return session_expired_response()

    member = core_bank_state.members.get(member_id)
    if not member:
        return HTMLResponse("<h2>Error: Member record not found.</h2>", status_code=404)

    submit_btn_id = dynamic_id("btn_commit_credit")
    container_id = dynamic_id("commit_scope_container")

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Confirm Provisional Credit Posting</title>
    <style>
        body {{ font-family: Tahoma, sans-serif; font-size:11px; background:#f0f0f0; margin:12px; }}
        .confirm-panel {{ background:#ffffff; border:2px solid #cc0000; padding:15px; }}
        .warning-text {{ color:#cc0000; font-weight:bold; font-size:12px; margin-bottom:10px; }}
        .detail-row {{ margin:4px 0; }}
    </style>
</head>
<body>
    <!-- CONTROL-SCOPED CONTAINER: contains the exact member, account, amount, and commit button -->
    <div class="confirm-panel" id="{container_id}" data-member-id="{html.escape(member.member_id)}" data-amount="{amount:.2f}">
        <div class="warning-text">⚠️ ACTION REQUIRES CONFIRMATION: IRREVERSIBLE MONEY MOVEMENT</div>
        <p>You are about to post a provisional credit to the following account:</p>

        <div style="background:#f7f7f7; border:1px solid #ddd; padding:10px; margin-bottom:15px;">
            <div class="detail-row">Target Member ID: <strong class="scoped-member-id">{html.escape(member.member_id)}</strong></div>
            <div class="detail-row">Target Account: <strong class="scoped-account-id">{html.escape(account_id)}</strong></div>
            <div class="detail-row">Dispute Case Ref: <strong class="scoped-case-id">{html.escape(case_id)}</strong></div>
            <div class="detail-row">Credit Amount: <strong class="scoped-amount" style="color:#006600; font-size:13px;">${amount:,.2f} USD</strong></div>
            <div class="detail-row">Memo Description: {html.escape(reason)}</div>
        </div>

        <form method="POST" action="/workspace/credit/commit">
            <input type="hidden" name="institution_id" value="alpha" />
            <input type="hidden" name="member_id" value="{html.escape(member.member_id)}" />
            <input type="hidden" name="account_id" value="{html.escape(account_id)}" />
            <input type="hidden" name="case_id" value="{html.escape(case_id)}" />
            <input type="hidden" name="amount" value="{amount}" />
            <input type="hidden" name="currency" value="USD" />
            <input type="hidden" name="admin_token" value="{html.escape(settings.tandem_admin_token)}" />
            <button type="submit" id="{submit_btn_id}" class="btn-commit-final" style="background:#cc0000; color:#fff; font-weight:bold; font-size:12px; padding:8px 20px; border:2px outset #fff; cursor:pointer;">
                POST PROVISIONAL CREDIT NOW
            </button>
            <a href="/workspace/search" style="margin-left:15px; color:#555;">Cancel</a>
        </form>
    </div>
</body>
</html>""")


@app.post(
    "/workspace/credit/commit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_token)],
)
async def workspace_credit_commit(
    institution_id: str = Form(...),
    member_id: str = Form(...),
    account_id: str = Form(...),
    case_id: str = Form(...),
    amount: Decimal = Form(...),
    currency: str = Form(...),
):
    if not core_bank_state.session_valid:
        return session_expired_response()

    if core_bank_state.simulate_latency_ms > 0:
        await asyncio.sleep(core_bank_state.simulate_latency_ms / 1000.0)

    try:
        member = core_bank_state.members.get(member_id)
        if institution_id != "alpha":
            raise ValueError("Institution binding mismatch")
        if not member or account_id != member.account_id:
            raise ValueError("Account binding mismatch")
        if currency != "USD":
            raise ValueError("Currency binding mismatch")
        credit = core_bank_state.post_credit(
            case_id=case_id,
            member_id=member_id,
            amount=amount,
            currency=currency,
            business_reference=case_id,
        )
        member = core_bank_state.members[member_id]
        if core_bank_state.simulate_post_commit_delay_ms > 0:
            await asyncio.sleep(core_bank_state.simulate_post_commit_delay_ms / 1000.0)
    except Exception as e:
        return HTMLResponse(f"<h2>Transaction Failed: {html.escape(str(e))}</h2>", status_code=400)

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Transaction Completed</title>
    <style>
        body {{ font-family: Tahoma, sans-serif; font-size:11px; background:#f0f0f0; margin:12px; }}
        .success-panel {{ background:#ffffff; border:2px solid #008800; padding:15px; }}
        .success-header {{ color:#008800; font-weight:bold; font-size:14px; margin-bottom:10px; }}
    </style>
</head>
<body>
    <div class="success-panel" id="credit_success_receipt">
        <div class="success-header">✓ PROVISIONAL CREDIT POSTED SUCCESSFULLY</div>
        <table border="0" cellpadding="4" style="margin-top:10px;">
            <tr><td>Transaction Memo:</td><td><strong id="receipt_memo_code" class="result-memo-code">{html.escape(credit.memo_code)}</strong></td></tr>
            <tr><td>Dispute Case Ref:</td><td><strong id="receipt_case_id">{html.escape(credit.case_id)}</strong></td></tr>
            <tr><td>Target Member ID:</td><td><span id="receipt_member_id">{html.escape(credit.member_id)}</span></td></tr>
            <tr><td>Target Account:</td><td>{html.escape(credit.account_id)}</td></tr>
            <tr><td>Credited Amount:</td><td><strong id="receipt_amount">${credit.amount:,.2f} USD</strong></td></tr>
            <tr><td>New Ledger Balance:</td><td>${member.balance:,.2f} USD</td></tr>
            <tr><td>Timestamp:</td><td>{html.escape(credit.posted_at)}</td></tr>
            <tr><td>Money Movement Status:</td><td><strong style="color:green;" id="receipt_money_moved">MONEY_MOVED=TRUE</strong></td></tr>
        </table>
        <div style="margin-top:15px;">
            <a href="/workspace/search" style="padding:4px 10px; background:#d4d0c8; border:1px outset #fff; text-decoration:none; color:#000;">Return to Member Search</a>
        </div>
    </div>
</body>
</html>""")


@app.get("/workspace/memos", response_class=HTMLResponse)
async def workspace_memos(case_id: Optional[str] = Query(None)):
    """Precheck / Memo lookup screen: find existing credit by case reference."""
    if not core_bank_state.session_valid:
        return session_expired_response()

    result_html = ""
    case_query = case_id or ""

    if case_query:
        credit = core_bank_state.find_credit_by_case(case_query)
        if credit:
            result_html = f"""
            <div class="memo-found-record" id="memo_found_box" style="background:#e8f4e8; border:1px solid #4a8; padding:10px; margin-top:10px;">
                <h4 style="color:#006600; margin:0 0 5px 0;">✓ EXISTING PROVISIONAL CREDIT RECORD FOUND</h4>
                <div>Case Reference: <strong class="found-case-id">{html.escape(credit.case_id)}</strong></div>
                <div>Member ID: <strong class="found-member-id">{html.escape(credit.member_id)}</strong></div>
                <div>Account ID: <strong class="found-account-id">{html.escape(credit.account_id)}</strong></div>
                <div>Amount: <strong class="found-amount">${credit.amount:,.2f} USD</strong></div>
                <div>Memo Code: <strong class="found-memo-code">{html.escape(credit.memo_code)}</strong></div>
                <div>Posted At: <span>{html.escape(credit.posted_at)}</span></div>
                <div>Status: <strong class="found-status">{html.escape(credit.status)}</strong></div>
            </div>
            """
        else:
            result_html = f"""
            <div class="memo-not-found" id="memo_not_found_box" style="background:#f4e8e8; border:1px solid #a44; padding:10px; margin-top:10px;">
                <span style="color:#aa0000; font-weight:bold;">NO PROVISIONAL CREDIT RECORD FOUND FOR CASE: {html.escape(case_query)}</span>
            </div>
            """

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <title>Memo & Reference Lookup</title>
    <style>
        body {{ font-family: Tahoma, sans-serif; font-size:11px; background:#f0f0f0; margin:12px; }}
        .panel {{ background:#ffffff; border:2px groove #fff; padding:12px; }}
    </style>
</head>
<body>
    <div class="panel">
        <h2 style="margin-top:0; color:#003366;">PRECHECK / MEMO LOOKUP BY CASE REFERENCE</h2>
        <form method="GET" action="/workspace/memos">
            <label for="case_ref_search">Dispute Case Reference:</label>
            <input type="text" id="case_ref_search" name="case_id" value="{html.escape(case_query)}" style="width:160px; padding:3px;" />
            <button type="submit" style="padding:3px 10px; background:#d4d0c8; border:2px outset #fff;">Search Core Memos</button>
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
    <title>Session Timed Out</title>
    <style>
        body { font-family: Tahoma, sans-serif; background:#f7f7f7; text-align:center; padding-top:50px; font-size:12px; }
        .error-card { background:#fff; border:1px solid #999; padding:25px; display:inline-block; max-width:400px; }
    </style>
</head>
<body>
    <div class="error-card" id="session_expired_panel">
        <h2 style="color:#cc0000; margin-top:0;">SECURITY EXCEPTION: SESSION EXPIRED</h2>
        <p>Your legacy host terminal session has timed out due to inactivity or credentials revocation.</p>
        <p><strong>Code: ERR_SESSION_EXPIRED_401</strong></p>
    </div>
</body>
</html>""",
        status_code=401,
    )
