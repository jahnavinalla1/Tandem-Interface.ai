"""Deterministic HTTP actuation adapter for thin, API-shaped external systems.

The processor portal and the document/notice system are deliberately thin
targets, so their COMMIT capabilities actuate through one structurally
declared ``HTTP_POST`` step instead of a browser replay. Everything around the
actuation -- claim, precheck, policy, intent, postcheck, reconciliation, and
fenced settlement -- is owned by :class:`tandem.replay.engine.EffectEngine`
exactly as it is for the browser-driven core capability.

The request-identity guard is the HTTP-surface equivalent of the form-bound
browser guard: the exact payload handed to the transport must carry the
complete immutable effect identity, and any divergence is a hard failure
before the request is dispatched.
"""

from __future__ import annotations

from typing import Any

import httpx

from tandem.config import settings
from tandem.domain.capability import CapabilityDefinition, StepAction
from tandem.domain.errors import AmountMismatchError, EntityBindingMismatchError
from tandem.domain.identity import EffectIdentity
from tandem.domain.money import format_money, parse_money
from tandem.domain.outcomes import ExecutionOutcome, ExecutionPhase, OutcomeCategory, OutcomeCode
from tandem.replay.crash_injection import maybe_crash
from tandem.replay.postcheck import execute_postcheck

_IDENTITY_FIELDS = (
    "institution_id",
    "procedure_id",
    "capability_id",
    "case_id",
    "member_id",
    "account_id",
    "currency",
    "business_reference",
)


def verify_request_identity_guard(payload: dict[str, str], identity: EffectIdentity) -> None:
    """Fail closed unless the submitted payload binds every identity field exactly."""
    expected = identity.canonical_payload
    for field in _IDENTITY_FIELDS:
        submitted = payload.get(field)
        if submitted is None or submitted == "":
            raise EntityBindingMismatchError(
                f"Request-identity guard failed closed: submitted {field} evidence is missing"
            )
        if submitted != expected[field]:
            raise EntityBindingMismatchError(
                f"Request-identity guard failed: expected {field} '{expected[field]}' "
                f"but the request would submit '{submitted}'"
            )
    submitted_amount = payload.get("amount")
    if submitted_amount is None or submitted_amount == "":
        raise AmountMismatchError(
            "Request-identity guard failed closed: submitted amount evidence is missing"
        )
    try:
        parsed = parse_money(submitted_amount)
    except ValueError as exc:
        raise AmountMismatchError(
            f"Request-identity guard failed: submitted amount is invalid ({exc})"
        ) from exc
    if parsed != identity.amount:
        raise AmountMismatchError(
            f"Request-identity guard failed: expected amount {format_money(identity.amount)}, "
            f"but the request would submit {format_money(parsed)}"
        )


def build_request(
    capability: CapabilityDefinition, inputs: dict[str, Any], identity: EffectIdentity
) -> tuple[str, dict[str, str]]:
    """Resolve the target route and the exact form payload for one HTTP COMMIT."""
    common = {**identity.canonical_payload}
    if capability.system == "processor":
        return (
            f"{settings.processor_url}/chargeback/file",
            {
                **common,
                "card_last4": str(inputs["card_last4"]),
                "dispute_reason": str(inputs.get("dispute_reason", "Unauthorized Transaction")),
            },
        )
    if capability.system == "documents":
        return (
            f"{settings.documents_url}/notices/send",
            {
                **common,
                "notice_type": str(inputs["notice_type"]),
                "deadline_due_at": str(inputs["deadline_due_at"]),
            },
        )
    raise ValueError(f"No deterministic HTTP adapter for system '{capability.system}'")


def execute_http_commit(
    capability: CapabilityDefinition, inputs: dict[str, Any]
) -> ExecutionOutcome:
    """Send one structurally declared request and independently confirm its effect."""
    steps = [step for step in capability.steps if step.action == StepAction.HTTP_POST]
    guard = capability.scoped_guard
    if len(steps) != 1 or guard is None or steps[0].guard_ref != guard.guard_id:
        return _hard_failure(
            OutcomeCode.POLICY_VIOLATION, "HTTP COMMIT lacks one structurally guarded actuation"
        )
    identity = EffectIdentity.from_capability(capability, inputs)
    try:
        url, payload = build_request(capability, inputs, identity)
    except (KeyError, ValueError) as exc:
        return _hard_failure(OutcomeCode.POLICY_VIOLATION, f"HTTP COMMIT request invalid: {exc}")

    # Guard immediately precedes the actuation: the payload below is the exact
    # object handed to the transport, not a display of it.
    try:
        verify_request_identity_guard(payload, identity)
    except EntityBindingMismatchError as exc:
        return _hard_failure(OutcomeCode.ENTITY_BINDING_MISMATCH, str(exc))
    except AmountMismatchError as exc:
        return _hard_failure(OutcomeCode.AMOUNT_MISMATCH, str(exc))
    maybe_crash("E_AFTER_GUARD", capability.id)
    maybe_crash("F_BEFORE_SUBMIT", capability.id)

    headers = {"Authorization": f"Bearer {settings.tandem_admin_token}"}
    try:
        response = httpx.post(url, data=payload, headers=headers, timeout=5.0)
    except Exception as exc:
        # The request may or may not have reached the target; nothing below can
        # decide that, so the engine reconciles against the target itself.
        return _possibly_applied(f"HTTP COMMIT transport failed: {exc}")
    if response.status_code == 401:
        return ExecutionOutcome(
            category=OutcomeCategory.RECOVERABLE_FAILURE,
            code=OutcomeCode.SESSION_EXPIRED,
            message=f"External session expired before {capability.id} was accepted",
            money_moved=False,
            execution_phase=ExecutionPhase.BEFORE_SUBMIT,
        )
    if response.status_code >= 400:
        return _possibly_applied(
            f"HTTP COMMIT returned {response.status_code} after request dispatch"
        )
    maybe_crash("G_AFTER_TARGET_ACCEPTS", capability.id)
    maybe_crash("H_BEFORE_POSTCHECK", capability.id)
    postcheck = execute_postcheck(capability, inputs)
    maybe_crash("I_AFTER_POSTCHECK", capability.id)
    if postcheck.is_success:
        return postcheck.model_copy(update={"execution_phase": ExecutionPhase.SUBMIT_CONFIRMED})
    return postcheck.model_copy(update={"execution_phase": ExecutionPhase.AFTER_SUBMIT_UNKNOWN})


def _possibly_applied(message: str) -> ExecutionOutcome:
    return ExecutionOutcome(
        category=OutcomeCategory.UNCERTAIN_EFFECT,
        code=OutcomeCode.POSSIBLY_APPLIED,
        message=message,
        execution_phase=ExecutionPhase.AFTER_SUBMIT_UNKNOWN,
    )


def _hard_failure(code: OutcomeCode, message: str) -> ExecutionOutcome:
    return ExecutionOutcome(
        category=OutcomeCategory.HARD_FAILURE,
        code=code,
        message=message,
        money_moved=False,
        execution_phase=ExecutionPhase.BEFORE_SUBMIT,
    )
