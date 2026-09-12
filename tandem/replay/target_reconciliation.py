"""Reconcile durable Tandem claims against independently durable target state."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from tandem.config import settings
from tandem.domain.effects import EffectClaimStatus
from tandem.domain.money import parse_money
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.ledger.models import EffectClaimRecord
from tandem.ledger.repository import LedgerRepository
from tandem.surfaces.routing import core_bank_url_for


def reconcile_applied_effects(
    repo: LedgerRepository, case_id: str
) -> Optional[ExecutionOutcome]:
    """Return a human-routing outcome if any applied claim disagrees with its target."""
    claims = repo.get_effect_claims_for_case(case_id)
    for claim in claims:
        if claim.status != EffectClaimStatus.APPLIED.value:
            continue
        outcome = _reconcile_claim(repo, claim)
        if outcome is not None:
            return outcome
    return None


def _reconcile_claim(
    repo: LedgerRepository, claim: EffectClaimRecord
) -> Optional[ExecutionOutcome]:
    adapters = {
        "core.post_provisional_credit": (
            f"{core_bank_url_for(claim.institution_id)}/api/credits/{claim.business_reference}",
            "POSTED",
            "memo_code",
        ),
        "processor.file_chargeback": (
            f"{settings.processor_url}/api/chargebacks/{claim.business_reference}",
            "FILED",
            "network_ref",
        ),
        "docs.send_notice": (
            f"{settings.documents_url}/api/notices/{claim.business_reference}",
            "SENT",
            "notice_id",
        ),
    }
    adapter = adapters.get(claim.capability_id)
    if adapter is None:
        return _unavailable(
            claim,
            f"No target reconciliation adapter for applied capability {claim.capability_id}",
        )
    url, expected_status, reference_field = adapter

    try:
        response = httpx.get(url, timeout=3.0)
    except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
        return _unavailable(claim, f"Target inquiry failed: {exc}")
    except Exception as exc:
        return _unavailable(claim, f"Target inquiry failed unexpectedly: {exc}")

    if response.status_code == 404:
        return _divergence(claim, "ledger is APPLIED but the target positively reports absence")
    if response.status_code != 200:
        return _unavailable(claim, f"Target inquiry returned HTTP {response.status_code}")
    try:
        data = response.json()
    except Exception as exc:
        return _unavailable(claim, f"Target inquiry returned malformed JSON: {exc}")
    if not isinstance(data, dict):
        return _unavailable(claim, "Target inquiry response is not an object")

    expected: dict[str, Any] = {
        "institution_id": claim.institution_id,
        "procedure_id": claim.procedure_id,
        "capability_id": claim.capability_id,
        "case_id": claim.case_id,
        "member_id": claim.member_id,
        "account_id": claim.account_id,
        "currency": claim.currency,
        "business_reference": claim.business_reference,
        "status": expected_status,
        "effect_count": 1,
    }
    mismatches = [
        f"{field}: expected {value!r}, observed {data.get(field)!r}"
        for field, value in expected.items()
        if data.get(field) != value
    ]
    try:
        if parse_money(data.get("amount")) != parse_money(claim.amount):
            mismatches.append(
                f"amount: expected {claim.amount!r}, observed {data.get('amount')!r}"
            )
    except ValueError as exc:
        mismatches.append(f"amount is invalid: {exc}")

    executions = repo.get_executions_for_case(claim.case_id)
    matching = [item for item in executions if item.capability_id == claim.capability_id]
    audit_refs = {item.audit_ref for item in matching if item.audit_ref}
    target_reference = data.get(reference_field)
    if len(audit_refs) != 1 or target_reference not in audit_refs:
        mismatches.append(
            f"target reference {target_reference!r} does not match one ledger audit reference"
        )
    if mismatches:
        return _divergence(claim, "; ".join(mismatches), details=data)
    return None


def _divergence(
    claim: EffectClaimRecord,
    reason: str,
    details: Optional[dict[str, Any]] = None,
) -> ExecutionOutcome:
    return ExecutionOutcome(
        category=OutcomeCategory.NEEDS_HUMAN,
        code=OutcomeCode.TARGET_LEDGER_DIVERGENCE,
        message=f"Applied effect {claim.idempotency_key} diverges from target: {reason}",
        details=details or {},
        money_moved=False,
    )


def _unavailable(claim: EffectClaimRecord, reason: str) -> ExecutionOutcome:
    return ExecutionOutcome(
        category=OutcomeCategory.NEEDS_HUMAN,
        code=OutcomeCode.TARGET_RECONCILIATION_UNAVAILABLE,
        message=f"Could not reconcile applied effect {claim.idempotency_key}: {reason}",
        money_moved=False,
    )
