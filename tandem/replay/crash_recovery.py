"""Recovery of effects whose worker died while holding an irreversible claim."""

from __future__ import annotations

import ctypes
import os
from typing import Optional

from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.effects import EffectClaimStatus
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.ledger.repository import LedgerRepository
from tandem.replay.precheck import execute_precheck

_CAPABILITY_FILES = {
    "core.post_provisional_credit": "capabilities/core/post_provisional_credit.yaml",
    "processor.file_chargeback": "capabilities/processor/file_chargeback.yaml",
    "docs.send_notice": "capabilities/documents/send_notice.yaml",
}


def recover_dead_effect_claims(
    repo: LedgerRepository, case_id: str
) -> Optional[ExecutionOutcome]:
    """Resolve any abandoned COMMIT claim using independent target truth."""
    lease = repo.get_lease(case_id)
    if lease and lease.released_at is None and _local_owner_is_dead(lease.owner_id):
        repo.release_lease(case_id, lease.owner_id, lease.fencing_token)

    for claim in repo.get_effect_claims_for_case(case_id):
        if claim.status not in {
            EffectClaimStatus.CLAIMED.value,
            EffectClaimStatus.APPLYING.value,
        }:
            continue
        if not _local_owner_is_dead(claim.owner_id):
            return _needs_human(
                OutcomeCode.ALREADY_CLAIMED,
                f"Effect claim is still owned by live or unverifiable worker {claim.owner_id}",
            )
        capability_file = _CAPABILITY_FILES.get(claim.capability_id)
        if capability_file is None:
            return _needs_human(
                OutcomeCode.TARGET_RECONCILIATION_UNAVAILABLE,
                f"No recovery adapter for {claim.capability_id}",
            )

        capability = load_capability_from_yaml(capability_file)
        inputs = {
            "institution_id": claim.institution_id,
            "member_id": claim.member_id,
            "account_id": claim.account_id,
            "case_id": claim.case_id,
            "amount": claim.amount,
            "currency": claim.currency,
        }
        target = execute_precheck(capability, inputs)
        if target.code == OutcomeCode.ALREADY_APPLIED:
            if not repo.transition_effect_claim(
                claim.idempotency_key,
                claim.owner_id,
                claim.fencing_token,
                {EffectClaimStatus.CLAIMED, EffectClaimStatus.APPLYING},
                EffectClaimStatus.APPLIED,
            ):
                return _needs_human(
                    OutcomeCode.TARGET_RECONCILIATION_UNAVAILABLE,
                    "Claim changed concurrently during crash recovery",
                )
            executions = repo.get_executions_for_case(case_id)
            if not any(
                item.capability_id == claim.capability_id
                and item.status in {"SUCCESS", "COMPLETED", "ALREADY_APPLIED"}
                for item in executions
            ):
                execution = repo.start_execution(
                    case_id,
                    claim.capability_id,
                    "1.0.0",
                    "COMMIT",
                    idempotency_key=claim.idempotency_key,
                    expected_entity=claim.member_id,
                    expected_amount=claim.amount,
                )
                repo.complete_execution(
                    execution.id,
                    status="COMPLETED",
                    observed_entity=claim.member_id,
                    observed_amount=claim.amount,
                    audit_ref=target.audit_ref,
                    money_moved=claim.capability_id == "core.post_provisional_credit",
                )
            repo.stage_intent(case_id, claim.idempotency_key, claim.capability_id)
            repo.mark_intent_committed(claim.idempotency_key)
            if claim.capability_id == "core.post_provisional_credit":
                repo.update_case_status(case_id, "PROVISIONAL_CREDIT_POSTED", money_moved=True)
            repo.record_event(
                case_id,
                "CRASH_RECOVERY_CONFIRMED_APPLIED",
                claim.capability_id,
                payload={"audit_ref": target.audit_ref},
            )
            continue
        if target.code == OutcomeCode.NOT_APPLIED:
            if not repo.transition_effect_claim(
                claim.idempotency_key,
                claim.owner_id,
                claim.fencing_token,
                {EffectClaimStatus.CLAIMED, EffectClaimStatus.APPLYING},
                EffectClaimStatus.FAILED_RETRYABLE,
            ):
                return _needs_human(
                    OutcomeCode.TARGET_RECONCILIATION_UNAVAILABLE,
                    "Claim changed concurrently during crash recovery",
                )
            repo.record_event(
                case_id,
                "CRASH_RECOVERY_CONFIRMED_ABSENT",
                claim.capability_id,
                payload={"safe_to_retry": True},
            )
            continue
        return _needs_human(
            OutcomeCode.TARGET_RECONCILIATION_UNAVAILABLE,
            f"Crash recovery inquiry was inconclusive: {target.code.value}",
        )
    return None


def recover_dead_core_claims(
    repo: LedgerRepository, case_id: str, member_id: str, amount: object
) -> Optional[ExecutionOutcome]:
    """Backward-compatible wrapper for callers predating generalized recovery."""
    del member_id, amount
    return recover_dead_effect_claims(repo, case_id)


def _local_owner_is_dead(owner_id: str) -> bool:
    parts = owner_id.split(":", 2)
    if len(parts) < 2 or parts[0] != "AUTOMATION":
        return False
    try:
        pid = int(parts[1])
    except ValueError:
        return False
    if pid == os.getpid():
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, pid
        )
        if not handle:
            return True
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                handle, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value != still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def _needs_human(code: OutcomeCode, message: str) -> ExecutionOutcome:
    return ExecutionOutcome(
        category=OutcomeCategory.NEEDS_HUMAN,
        code=code,
        message=message,
        money_moved=False,
    )
