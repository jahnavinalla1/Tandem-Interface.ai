"""Fail-closed postchecks for irreversible external effects."""

from typing import Any, Dict

import httpx

from tandem.config import settings
from tandem.domain.capability import CapabilityDefinition
from tandem.domain.money import parse_money
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.surfaces.routing import core_bank_url_for


def execute_postcheck(
    capability: CapabilityDefinition, inputs: Dict[str, Any]
) -> ExecutionOutcome:
    """Execute postcheck against target legacy system to verify whether effect landed."""
    case_id = inputs.get("case_id")
    if not case_id:
        return _uncertain(OutcomeCode.POSTCHECK_INVALID_RESPONSE, "Postcheck requires case_id")

    if capability.system == "core_bank" or "provisional_credit" in capability.id:
        url = f"{core_bank_url_for(inputs.get('institution_id'))}/api/credits/{case_id}"
        moves_money = True
        reference_field = "memo_code"
    elif capability.system == "processor" or "chargeback" in capability.id:
        url = f"{settings.processor_url}/api/chargebacks/{case_id}"
        moves_money = False
        reference_field = "network_ref"
    elif capability.system == "documents" or "notice" in capability.id:
        url = f"{settings.documents_url}/api/notices/{case_id}"
        moves_money = False
        reference_field = "notice_id"
    else:
        return _uncertain(
            OutcomeCode.POSTCHECK_INVALID_RESPONSE,
            f"No postcheck adapter is registered for system '{capability.system}'",
        )

    try:
        response = httpx.get(url, timeout=3.0)
    except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
        return _uncertain(OutcomeCode.POSTCHECK_UNAVAILABLE, f"Postcheck unavailable: {exc}")
    except Exception as exc:
        return _uncertain(OutcomeCode.POSTCHECK_UNAVAILABLE, f"Postcheck failed: {exc}")

    if response.status_code == 404:
        return ExecutionOutcome(
            category=OutcomeCategory.BUSINESS_OUTCOME,
            code=OutcomeCode.CONFIRMED_NOT_APPLIED,
            message="Target positively confirmed that the effect is absent",
            money_moved=False,
        )
    if response.status_code != 200:
        return _uncertain(
            OutcomeCode.POSTCHECK_UNAVAILABLE,
            f"Postcheck target returned HTTP {response.status_code}",
        )

    try:
        data = response.json()
    except Exception as exc:
        return _uncertain(
            OutcomeCode.POSTCHECK_INVALID_RESPONSE,
            f"Postcheck returned malformed JSON: {exc}",
        )
    if not isinstance(data, dict):
        return _uncertain(
            OutcomeCode.POSTCHECK_INVALID_RESPONSE, "Postcheck response is not an object"
        )

    mismatch = _identity_mismatch(capability, inputs, data)
    if mismatch:
        return _uncertain(OutcomeCode.POSTCHECK_AMBIGUOUS, mismatch, details=data)

    audit_ref = data.get(reference_field)
    if not audit_ref or not data.get("status"):
        return _uncertain(
            OutcomeCode.POSTCHECK_INVALID_RESPONSE,
            "Postcheck response lacks status or immutable target reference",
            details=data,
        )
    return ExecutionOutcome(
        category=OutcomeCategory.SUCCESS,
        code=OutcomeCode.COMPLETED,
        message=f"Postcheck confirmed the complete external effect ({audit_ref})",
        details=data,
        money_moved=moves_money,
        audit_ref=str(audit_ref),
    )


def _identity_mismatch(
    capability: CapabilityDefinition,
    inputs: Dict[str, Any],
    data: Dict[str, Any],
) -> str:
    expected = {
        "institution_id": str(inputs.get("institution_id", "")),
        "procedure_id": "reg_e_dispute",
        "capability_id": capability.id,
        "case_id": str(inputs.get("case_id", "")),
        "member_id": str(inputs.get("member_id", "")),
        "account_id": str(inputs.get("account_id", "")),
        "currency": str(inputs.get("currency", "")),
        "business_reference": str(inputs.get("case_id", "")),
    }
    for field, value in expected.items():
        if not value or str(data.get(field, "")) != value:
            return f"Postcheck identity mismatch for {field}: expected {value!r}, observed {data.get(field)!r}"
    if "amount" in inputs:
        try:
            if parse_money(data.get("amount")) != parse_money(inputs["amount"]):
                return "Postcheck amount does not match the immutable effect identity"
        except ValueError as exc:
            return f"Postcheck amount is invalid: {exc}"
    return ""


def _uncertain(
    code: OutcomeCode,
    message: str,
    details: Dict[str, Any] | None = None,
) -> ExecutionOutcome:
    return ExecutionOutcome(
        category=OutcomeCategory.UNCERTAIN_EFFECT,
        code=code,
        message=message,
        details=details or {},
        money_moved=False,
    )
