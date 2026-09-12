"""Fail-closed target prechecks for irreversible capabilities."""

from typing import Any, Dict

import httpx

from tandem.config import settings
from tandem.domain.capability import CapabilityDefinition
from tandem.domain.effects import EffectClass
from tandem.domain.money import parse_money
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.surfaces.routing import core_bank_url_for


def execute_precheck(
    capability: CapabilityDefinition, inputs: Dict[str, Any]
) -> ExecutionOutcome:
    """Execute capability precheck.

    If declared effect already exists on target system, returns BUSINESS_OUTCOME / ALREADY_APPLIED.
    Only NOT_APPLIED authorizes the caller to approach COMMIT. Every failure mode
    is represented explicitly and therefore cannot be confused with absence.
    """
    if capability.effect.effect_class != EffectClass.COMMIT:
        return _result(OutcomeCode.NOT_APPLIED, "READ/STAGE capability has no COMMIT precheck")

    precheck_spec = capability.effect.precheck
    if not precheck_spec:
        return _failure(
            OutcomeCode.PRECHECK_INVALID_RESPONSE,
            f"COMMIT capability '{capability.id}' has no precheck specification",
        )

    case_id = inputs.get("case_id")
    if not case_id:
        return _failure(OutcomeCode.PRECHECK_INVALID_RESPONSE, "Precheck requires case_id")

    url: str
    if capability.system == "core_bank" or "provisional_credit" in capability.id:
        url = f"{core_bank_url_for(inputs.get('institution_id'))}/api/credits/{case_id}"
    elif capability.system == "processor" or "chargeback" in capability.id:
        url = f"{settings.processor_url}/api/chargebacks/{case_id}"
    elif capability.system == "documents" or "notice" in capability.id:
        url = f"{settings.documents_url}/api/notices/{case_id}"
    else:
        return _failure(
            OutcomeCode.PRECHECK_INVALID_RESPONSE,
            f"No precheck adapter is registered for system '{capability.system}'",
        )

    try:
        response = httpx.get(url, timeout=3.0)
    except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
        return _failure(OutcomeCode.PRECHECK_UNAVAILABLE, f"Precheck unavailable: {exc}")
    except Exception as exc:
        return _failure(OutcomeCode.PRECHECK_UNAVAILABLE, f"Precheck failed: {exc}")

    if response.status_code == 404:
        return _result(OutcomeCode.NOT_APPLIED, "Target positively confirmed effect absence")
    if response.status_code != 200:
        return _failure(
            OutcomeCode.PRECHECK_UNAVAILABLE,
            f"Precheck target returned HTTP {response.status_code}",
        )

    try:
        data = response.json()
    except Exception as exc:
        return _failure(
            OutcomeCode.PRECHECK_INVALID_RESPONSE,
            f"Precheck returned malformed JSON: {exc}",
        )
    if not isinstance(data, dict):
        return _failure(OutcomeCode.PRECHECK_INVALID_RESPONSE, "Precheck response is not an object")

    mismatch = _identity_mismatch(capability, inputs, data)
    if mismatch:
        return _failure(OutcomeCode.PRECHECK_AMBIGUOUS, mismatch, details=data)

    audit_ref = data.get("memo_code") or data.get("network_ref") or data.get("notice_id")
    if not audit_ref or not data.get("status"):
        return _failure(
            OutcomeCode.PRECHECK_INVALID_RESPONSE,
            "Precheck response lacks status or immutable target reference",
            details=data,
        )
    return ExecutionOutcome(
        category=OutcomeCategory.BUSINESS_OUTCOME,
        code=OutcomeCode.ALREADY_APPLIED,
        message=f"Precheck confirmed the complete effect identity already exists ({audit_ref})",
        details=data,
        money_moved=False,
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
            return f"Precheck identity mismatch for {field}: expected {value!r}, observed {data.get(field)!r}"

    if "amount" in inputs:
        try:
            if parse_money(data.get("amount")) != parse_money(inputs["amount"]):
                return "Precheck amount does not match the immutable effect identity"
        except ValueError as exc:
            return f"Precheck amount is invalid: {exc}"
    return ""


def _result(code: OutcomeCode, message: str) -> ExecutionOutcome:
    return ExecutionOutcome(
        category=OutcomeCategory.SUCCESS,
        code=code,
        message=message,
        money_moved=False,
    )


def _failure(
    code: OutcomeCode,
    message: str,
    details: Dict[str, Any] | None = None,
) -> ExecutionOutcome:
    return ExecutionOutcome(
        category=OutcomeCategory.HARD_FAILURE,
        code=code,
        message=message,
        details=details or {},
        money_moved=False,
    )
