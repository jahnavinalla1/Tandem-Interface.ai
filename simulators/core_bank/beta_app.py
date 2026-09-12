"""Independent Institution Beta core simulator with a materially different DOM."""

from __future__ import annotations

import html
import random
from decimal import Decimal

from fastapi import Depends, FastAPI, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse

from simulators.core_bank.beta_state import core_bank_beta_state
from tandem.config import settings
from tandem.security.auth import require_admin_token

app = FastAPI(title="Institution Beta - Heritage Core Simulator")


def _id(prefix: str) -> str:
    return f"{prefix}-{random.randint(10000, 99999)}"


@app.post("/api/reset", dependencies=[Depends(require_admin_token)])
async def reset():
    core_bank_beta_state.seed()
    return {"status": "ok", "institution_id": "beta"}


@app.get("/api/member/{member_id}")
async def member(member_id: str):
    item = core_bank_beta_state.members.get(member_id)
    if item is None:
        return JSONResponse(status_code=404, content={"error": "Member not found"})
    return {
        "member_id": item.member_id,
        "account_id": item.account_id,
        "balance": item.balance,
        "institution_id": "beta",
    }


@app.get("/api/credits/{case_id}")
async def credit(case_id: str):
    item = core_bank_beta_state.find_credit_by_case(case_id)
    if item is None:
        return JSONResponse(status_code=404, content={"error": "No credit found for case"})
    return {
        "case_id": item.case_id,
        "member_id": item.member_id,
        "account_id": item.account_id,
        "amount": item.amount,
        "currency": item.currency,
        "business_reference": item.business_reference,
        "institution_id": "beta",
        "procedure_id": "reg_e_dispute",
        "capability_id": "core.post_provisional_credit",
        "memo_code": item.memo_code,
        "status": item.status,
        "effect_count": core_bank_beta_state.effect_count(case_id),
    }


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(
        """<!doctype html><html><head><title>Heritage Core - Institution Beta</title></head>
        <body style="margin:0;font-family:monospace;background:#172554;color:#e0e7ff">
        <header style="padding:12px">INSTITUTION BETA // HERITAGE CORE // TERMINAL 7</header>
        <iframe id="core_workspace_frame" name="core_workspace_frame"
          src="/workspace/search" style="width:100%;height:90vh;border:4px ridge #64748b"></iframe>
        </body></html>"""
    )


@app.get("/workspace/search", response_class=HTMLResponse)
async def search(member_lookup: str | None = Query(None)):
    rows = ""
    if member_lookup:
        members = core_bank_beta_state.search_members(member_lookup, randomize_order=True)
        rows = "".join(
            f"""<tr><td>{html.escape(item.member_id)}</td><td>{html.escape(item.account_id)}</td>
            <td>{html.escape(item.last_name)}, {html.escape(item.first_name)}</td>
            <td><a class="btn-action-legacy" href="/workspace/credit/entry?member_id={html.escape(item.member_id)}">Select &amp; Adjust</a></td></tr>"""
            for item in members
        )
    return HTMLResponse(
        f"""<!doctype html><html><body style="font-family:monospace;background:#e2e8f0">
        <h2>BETA MEMBER SERVICING</h2>
        <form method="get"><input id="legacy_search_box" name="member_lookup"
          value="{html.escape(member_lookup or '')}"><input class="legacy-search-action"
          type="submit" value="Locate Account"></form>
        <table><tbody>{rows}</tbody></table></body></html>"""
    )


@app.get("/workspace/credit/entry", response_class=HTMLResponse)
async def entry(member_id: str = Query(...)):
    item = core_bank_beta_state.members.get(member_id)
    if item is None:
        return HTMLResponse("Unknown member", status_code=404)
    return HTMLResponse(
        f"""<!doctype html><html><body style="font-family:monospace;background:#fef3c7">
        <h2>HERITAGE ADJUSTMENT ENTRY</h2>
        <form method="post" action="/workspace/credit/confirm">
          <input type="hidden" name="institution_id" value="beta">
          <input type="hidden" name="member_id" value="{html.escape(item.member_id)}">
          <input type="hidden" name="account_id" value="{html.escape(item.account_id)}">
          <input type="hidden" name="currency" value="USD">
          <input type="hidden" name="admin_token" value="{html.escape(settings.tandem_admin_token)}">
          <label>Dispute ref <input name="case_id" id="beta_dispute_ref"></label>
          <label>Credit value <input name="amount" id="beta_credit_value"></label>
          <button class="legacy-review-action" type="submit">Review Adjustment</button>
        </form></body></html>"""
    )


@app.post(
    "/workspace/credit/confirm",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_token)],
)
async def confirm(
    institution_id: str = Form(...),
    member_id: str = Form(...),
    account_id: str = Form(...),
    case_id: str = Form(...),
    amount: Decimal = Form(...),
    currency: str = Form(...),
):
    if institution_id != "beta":
        return HTMLResponse("Institution mismatch", status_code=400)
    return HTMLResponse(
        f"""<!doctype html><html><body style="font-family:monospace;background:#fee2e2">
        <div class="legacy-confirm-box"><h2>FINAL BETA ADJUSTMENT REVIEW</h2>
        <p>{html.escape(member_id)} / {html.escape(account_id)} / {html.escape(case_id)} /
        {amount:.2f} {html.escape(currency)}</p>
        <form method="post" action="/workspace/credit/commit">
          <input type="hidden" name="institution_id" value="beta">
          <input type="hidden" name="member_id" value="{html.escape(member_id)}">
          <input type="hidden" name="account_id" value="{html.escape(account_id)}">
          <input type="hidden" name="case_id" value="{html.escape(case_id)}">
          <input type="hidden" name="amount" value="{amount:.2f}">
          <input type="hidden" name="currency" value="{html.escape(currency)}">
          <input type="hidden" name="admin_token" value="{html.escape(settings.tandem_admin_token)}">
          <button id="{_id('beta-commit')}" class="btn-commit-legacy" type="submit">Apply Heritage Adjustment</button>
        </form></div></body></html>"""
    )


@app.post(
    "/workspace/credit/commit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_token)],
)
async def commit(
    institution_id: str = Form(...),
    member_id: str = Form(...),
    account_id: str = Form(...),
    case_id: str = Form(...),
    amount: Decimal = Form(...),
    currency: str = Form(...),
):
    if institution_id != "beta":
        return HTMLResponse("Institution mismatch", status_code=400)
    member = core_bank_beta_state.members.get(member_id)
    if member is None or member.account_id != account_id:
        return HTMLResponse("Account mismatch", status_code=400)
    posted = core_bank_beta_state.post_credit(
        case_id,
        member_id,
        amount,
        currency=currency,
        business_reference=case_id,
    )
    return HTMLResponse(
        f"""<!doctype html><html><body><h2>BETA ADJUSTMENT POSTED</h2>
        <span id="receipt_memo_code">{html.escape(posted.memo_code)}</span>
        <span id="receipt_money_moved">MONEY_MOVED=TRUE</span></body></html>"""
    )
