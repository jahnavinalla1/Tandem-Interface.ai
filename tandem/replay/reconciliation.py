"""Reconciliation engine for ambiguous execution and uncertain effects."""

import time
from typing import Any, Dict

from tandem.domain.capability import CapabilityDefinition
from tandem.domain.outcomes import ExecutionOutcome, ExecutionPhase, OutcomeCategory, OutcomeCode
from tandem.replay.postcheck import execute_postcheck


def reconcile_commit_execution(
    capability: CapabilityDefinition,
    inputs: Dict[str, Any],
    error_message: str = "",
    retry_delay_seconds: float = 0.2,
) -> ExecutionOutcome:
    """Reconcile ambiguous execution state when a network or browser exception occurs during a COMMIT.

    SAFETY INVARIANT: An uncertain effect MUST NEVER be blindly retried!
    1. Perform postcheck inquiry against the target system.
    2. If postcheck proves the mutation completed -> resolve as SUCCESS with audit reference.
    3. If postcheck is inconclusive -> classify as UNCERTAIN_EFFECT and escalate to human.

    `capability.effect.reconciliation.max_inquiry_attempts` (M-03: previously declared
    but never read) bounds how many times the *read-only inquiry itself* is retried when
    the inquiry endpoint is transiently unreachable (`POSTCHECK_UNAVAILABLE`) -- never the
    mutating COMMIT action, so this does not weaken the no-blind-retry invariant above. A
    malformed or ambiguous inquiry response is not retried: an immediate re-query would not
    fix bad data, so it is classified as uncertain on the first attempt.
    """
    spec = capability.effect.reconciliation
    max_attempts = max(1, spec.max_inquiry_attempts) if spec else 1

    postcheck_result = execute_postcheck(capability, inputs)
    attempts = 1
    while postcheck_result.code == OutcomeCode.POSTCHECK_UNAVAILABLE and attempts < max_attempts:
        time.sleep(retry_delay_seconds)
        postcheck_result = execute_postcheck(capability, inputs)
        attempts += 1

    if postcheck_result.is_success:
        return ExecutionOutcome(
            category=OutcomeCategory.SUCCESS,
            code=OutcomeCode.CONFIRMED_APPLIED,
            message=(
                f"Reconciliation successful: Postcheck confirmed effect occurred despite "
                f"interrupted connection. (Ref: {postcheck_result.audit_ref})"
            ),
            details={
                "reconciled": True,
                "postcheck_details": postcheck_result.details,
                "original_error": error_message,
                "inquiry_attempts": attempts,
            },
            money_moved=postcheck_result.money_moved,
            audit_ref=postcheck_result.audit_ref,
            execution_phase=ExecutionPhase.SUBMIT_CONFIRMED,
        )

    if postcheck_result.code == OutcomeCode.CONFIRMED_NOT_APPLIED:
        return postcheck_result.model_copy(
            update={
                "details": {
                    **postcheck_result.details,
                    "safe_to_retry": True,
                    "original_error": error_message,
                    "inquiry_attempts": attempts,
                },
                "execution_phase": ExecutionPhase.AFTER_SUBMIT_UNKNOWN,
            }
        )

    # Inconclusive: effect status cannot be proven
    return ExecutionOutcome(
        category=OutcomeCategory.UNCERTAIN_EFFECT,
        code=OutcomeCode.UNCERTAIN_EFFECT,
        message=(
            f"CRITICAL SAFETY EXCEPTION: COMMIT operation '{capability.id}' encountered an interruption: "
            f"'{error_message}'. Postcheck could not confirm state after {attempts} inquiry "
            f"attempt(s). Automatic retry is strictly forbidden (of the mutation itself) to prevent "
            f"duplicate money movement. Case requires manual human investigation."
        ),
        details={
            "capability_id": capability.id,
            "inputs": inputs,
            "original_error": error_message,
            "postcheck_code": postcheck_result.code.value,
            "inquiry_attempts": attempts,
        },
        money_moved=False,
        execution_phase=ExecutionPhase.AFTER_SUBMIT_UNKNOWN,
    )
