"""End-to-end Regulation E debit card dispute workflow orchestrator."""

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import httpx
from playwright.sync_api import Page
from sqlalchemy.orm import Session

from tandem.config import settings
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.money import parse_money
from tandem.domain.outcomes import OutcomeCategory, OutcomeCode
from tandem.ledger.repository import LedgerRepository
from tandem.ledger.service import LedgerService
from tandem.replay.crash_injection import maybe_crash
from tandem.replay.crash_recovery import recover_dead_effect_claims
from tandem.replay.engine import EffectEngine
from tandem.replay.target_reconciliation import reconcile_applied_effects
from tandem.workflow.deadlines import add_business_days, calculate_reg_e_deadlines
from tandem.workflow.state_machine import RegEState, can_transition

_HAPPY_PATH = [
    RegEState.RECEIVED,
    RegEState.MEMBER_VERIFIED,
    RegEState.TRANSACTION_VERIFIED,
    RegEState.DUPLICATE_CHECKED,
    RegEState.CHARGEBACK_FILED,
    RegEState.PROVISIONAL_CREDIT_POSTED,
    RegEState.NOTICE_PENDING,
    RegEState.NOTICE_SENT,
    RegEState.WAITING_RESOLUTION,
]


class RegEWorkflow:
    """Orchestrates cross-system Regulation E dispute processing."""

    def __init__(
        self,
        session: Session,
        page: Optional[Page] = None,
        kill_after_credit: bool = False,
        browser_session_id: str | None = None,
    ):
        self.session = session
        self.page = page
        self.repo = LedgerRepository(session)
        self.service = LedgerService(session)
        self.kill_after_credit = kill_after_credit or (
            os.environ.get("PROCESS_KILL_AFTER") == "core.post_provisional_credit"
        )
        self.browser_session_id = browser_session_id

    def transition(
        self, case_id: str, new_state: RegEState, money_moved: Optional[bool] = None
    ) -> None:
        case = self.repo.get_case(case_id)
        current = RegEState(case.status) if case else RegEState.RECEIVED
        if current == new_state:
            if money_moved:
                self.repo.update_case_status(case_id, status=new_state.value, money_moved=True)
                self.session.commit()
            return
        if not can_transition(current, new_state):
            raise ValueError(f"Illegal state transition from {current} to {new_state}")

        self.repo.update_case_status(case_id, status=new_state.value, money_moved=money_moved)
        self.repo.record_event(
            case_id=case_id,
            event_type="STATE_TRANSITION",
            step_name="orchestrator",
            payload={"from": current.value, "to": new_state.value},
        )
        self.session.commit()

    def _ensure_reached(
        self, case_id: str, milestone: RegEState, money_moved: Optional[bool] = None
    ) -> None:
        """Advance the state projection to a happy-path milestone if it is behind.

        Projections are derived from durable effect facts. When crash recovery
        confirms an effect that the previous worker never settled, the state
        machine must still catch up; when the projection is already at or past
        the milestone (or parked in an escalated state), nothing changes.
        """
        case = self.repo.get_case(case_id)
        current = RegEState(case.status) if case else RegEState.RECEIVED
        if current in _HAPPY_PATH and _HAPPY_PATH.index(current) >= _HAPPY_PATH.index(milestone):
            return
        if can_transition(current, milestone):
            self.transition(case_id, milestone, money_moved=money_moved)

    def run_case(
        self,
        case_id: str,
        member_id: str,
        amount: Decimal,
        card_last4: str = "4112",
        injected_clock: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Execute full Reg E dispute processing or resume from persistent ledger."""
        clock = injected_clock or datetime.now(timezone.utc)
        amount = parse_money(amount)

        # 0. Check if case already exists in ledger
        existing_case = self.repo.get_case(case_id)
        if existing_case:
            recovery_failure = recover_dead_effect_claims(self.repo, case_id)
            if recovery_failure is not None:
                self.repo.update_case_status(case_id, RegEState.NEEDS_HUMAN.value)
                self.repo.record_event(
                    case_id,
                    "CRASH_RECOVERY_REQUIRES_HUMAN",
                    "resume.crash_recovery",
                    payload={"message": recovery_failure.message},
                )
                self.session.commit()
                return {
                    "status": "NEEDS_HUMAN",
                    "code": recovery_failure.code.value,
                    "message": recovery_failure.message,
                }
            self.session.commit()
            reconciliation_failure = reconcile_applied_effects(self.repo, case_id)
            if reconciliation_failure is not None:
                self.repo.update_case_status(case_id, RegEState.NEEDS_HUMAN.value)
                self.repo.record_event(
                    case_id=case_id,
                    event_type=reconciliation_failure.code.value,
                    step_name="resume.target_reconciliation",
                    payload={
                        "message": reconciliation_failure.message,
                        "details": reconciliation_failure.details,
                    },
                )
                self.session.commit()
                return {
                    "status": "NEEDS_HUMAN",
                    "code": reconciliation_failure.code.value,
                    "message": reconciliation_failure.message,
                }
            deadlines = calculate_reg_e_deadlines(clock)
            self.repo.create_obligation(case_id, "NOTICE_2_DAY", add_business_days(clock, 2))
            self.repo.create_deadline(
                case_id, "INVESTIGATION_10_DAY", deadlines["INVESTIGATION_10_DAY"]
            )
            self.repo.create_deadline(
                case_id, "FINAL_RESOLUTION_45_DAY", deadlines["FINAL_RESOLUTION_45_DAY"]
            )
            self.session.commit()
            snapshot = self.service.reconstruct_case_state(case_id)
        else:
            self.repo.create_or_get_case(
                case_id=case_id,
                member_id=member_id,
                amount=amount,
                procedure_name="reg_e_dispute",
            )
            maybe_crash("A_BEFORE_OBLIGATION")
            # Register statutory deadlines
            deadlines = calculate_reg_e_deadlines(clock)
            notice_due_at = add_business_days(clock, 2)
            self.repo.create_obligation(case_id, "NOTICE_2_DAY", notice_due_at)
            self.session.commit()
            maybe_crash("B_AFTER_OBLIGATION")
            self.repo.create_deadline(
                case_id, "INVESTIGATION_10_DAY", deadlines["INVESTIGATION_10_DAY"]
            )
            self.repo.create_deadline(
                case_id, "FINAL_RESOLUTION_45_DAY", deadlines["FINAL_RESOLUTION_45_DAY"]
            )
            self.session.commit()
            snapshot = self.service.reconstruct_case_state(case_id)

        # -------------------------------------------------------------------
        # Step 1: Member Verification (READ)
        # -------------------------------------------------------------------
        if "core.verify_member" not in snapshot.completed_capabilities:
            resp = httpx.get(f"{settings.core_bank_url}/api/member/{member_id}", timeout=3.0)
            if resp.status_code != 200:
                self.transition(case_id, RegEState.FAILED)
                return {"status": "FAILED", "error": f"Member {member_id} not found"}

            exec_rec = self.repo.start_execution(
                case_id=case_id,
                capability_id="core.verify_member",
                capability_version="1.0.0",
                effect_class="READ",
                expected_entity=member_id,
            )
            self.repo.complete_execution(exec_rec.id, status="SUCCESS", observed_entity=member_id)
            self.transition(case_id, RegEState.MEMBER_VERIFIED)

        member_response = httpx.get(f"{settings.core_bank_url}/api/member/{member_id}", timeout=3.0)
        if member_response.status_code != 200:
            self.transition(case_id, RegEState.NEEDS_HUMAN)
            return {"status": "NEEDS_HUMAN", "error": "Unable to bind member account identity"}
        account_id = str(member_response.json()["account_id"])
        common_effect_inputs = {
            "institution_id": "alpha",
            "member_id": member_id,
            "account_id": account_id,
            "case_id": case_id,
            "amount": amount,
            "currency": "USD",
        }

        # -------------------------------------------------------------------
        # Step 2: Locate Transaction (READ)
        # -------------------------------------------------------------------
        if "core.locate_transaction" not in snapshot.completed_capabilities:
            exec_rec = self.repo.start_execution(
                case_id=case_id,
                capability_id="core.locate_transaction",
                capability_version="1.0.0",
                effect_class="READ",
            )
            self.repo.complete_execution(exec_rec.id, status="SUCCESS", observed_amount=amount)
            self.transition(case_id, RegEState.TRANSACTION_VERIFIED)

        # -------------------------------------------------------------------
        # Step 3: Duplicate Dispute Check (READ)
        # -------------------------------------------------------------------
        if "core.check_duplicate_dispute" not in snapshot.completed_capabilities:
            exec_rec = self.repo.start_execution(
                case_id=case_id,
                capability_id="core.check_duplicate_dispute",
                capability_version="1.0.0",
                effect_class="READ",
            )
            self.repo.complete_execution(exec_rec.id, status="SUCCESS")
            self.transition(case_id, RegEState.DUPLICATE_CHECKED)

        # -------------------------------------------------------------------
        # Step 4: Card Processor Chargeback (COMMIT)
        # -------------------------------------------------------------------
        if "processor.file_chargeback" not in snapshot.completed_capabilities:
            if not self.page:
                raise RuntimeError("Execution page required for effect orchestration")
            processor_capability = load_capability_from_yaml(
                "capabilities/processor/file_chargeback.yaml"
            )
            processor_outcome = EffectEngine(
                self.session,
                self.page,
                browser_session_id=self.browser_session_id,
            ).execute_capability(
                processor_capability,
                {
                    **common_effect_inputs,
                    "card_last4": card_last4,
                    "dispute_reason": "10.4 - Fraud / Unauthorized Transaction",
                },
            )
            failure = self._effect_failure(case_id, processor_outcome)
            if failure:
                return failure
        self._ensure_reached(case_id, RegEState.CHARGEBACK_FILED)

        # -------------------------------------------------------------------
        # Step 5: Post Provisional Credit (COMMIT - MOVES MONEY)
        # -------------------------------------------------------------------
        if "core.post_provisional_credit" not in snapshot.completed_capabilities:
            obligations = self.repo.get_obligations_for_case(case_id)
            if not any(item.obligation_type == "NOTICE_2_DAY" for item in obligations):
                raise RuntimeError("NOTICE_2_DAY obligation must exist before provisional credit")
            cap = load_capability_from_yaml("capabilities/core/post_provisional_credit.yaml")

            if not self.page:
                raise RuntimeError(
                    "Playwright page required for core.post_provisional_credit replay"
                )

            engine = EffectEngine(
                session=self.session,
                page=self.page,
                browser_session_id=self.browser_session_id,
            )
            outcome = engine.execute_capability(
                capability=cap,
                inputs=common_effect_inputs,
            )

            if outcome.code == OutcomeCode.POLICY_DENIED:
                self.transition(case_id, RegEState.NEEDS_HUMAN)
                return {"status": "POLICY_DENIED", "message": outcome.message}

            failure = self._effect_failure(case_id, outcome)
            if failure:
                return failure

        # The credit is a durable target fact (either just confirmed or found
        # applied during recovery); derive its projections idempotently.
        obligation = self.repo.activate_obligation(case_id, "NOTICE_2_DAY")
        self.repo.create_deadline(case_id, "NOTICE_2_DAY", due_at=obligation.due_at)
        self._ensure_reached(case_id, RegEState.PROVISIONAL_CREDIT_POSTED, money_moved=True)
        self.session.commit()

        if "core.post_provisional_credit" not in snapshot.completed_capabilities:
            # CRASH INJECTION HOOK: Simulate sudden process crash after money movement!
            if self.kill_after_credit:
                self.repo.record_event(
                    case_id=case_id,
                    event_type="PROCESS_KILLED",
                    step_name="core.post_provisional_credit",
                    payload={"message": "Intentional crash injection after money movement"},
                )
                self.session.commit()
                raise RuntimeError(
                    "PROCESS_KILL_AFTER=core.post_provisional_credit triggered! "
                    "Simulating process crash. Money has moved. Notice has not been sent."
                )

        # -------------------------------------------------------------------
        # Step 6: Dispatch Member Notice (COMMIT)
        # -------------------------------------------------------------------
        if "docs.send_notice" not in snapshot.completed_capabilities:
            maybe_crash("K_BEFORE_NOTICE")
            self._ensure_reached(case_id, RegEState.NOTICE_PENDING)
            notice_deadline = add_business_days(clock, 2).strftime("%Y-%m-%d %H:%M:%S UTC")

            if not self.page:
                raise RuntimeError("Execution page required for effect orchestration")
            document_capability = load_capability_from_yaml(
                "capabilities/documents/send_notice.yaml"
            )
            document_outcome = EffectEngine(
                self.session,
                self.page,
                browser_session_id=self.browser_session_id,
            ).execute_capability(
                document_capability,
                {
                    **common_effect_inputs,
                    "notice_type": "REG_E_PROVISIONAL_CREDIT_DISCLOSURE",
                    "deadline_due_at": notice_deadline,
                },
            )
            failure = self._effect_failure(case_id, document_outcome)
            if failure:
                return failure
        # The notice is a durable target fact; settle its projections idempotently.
        self.repo.resolve_deadline(case_id, "NOTICE_2_DAY")
        self.repo.satisfy_obligation(case_id, "NOTICE_2_DAY")
        self._ensure_reached(case_id, RegEState.NOTICE_SENT)

        # -------------------------------------------------------------------
        # Step 7: Settle Case in Waiting Resolution
        # -------------------------------------------------------------------
        self._ensure_reached(case_id, RegEState.WAITING_RESOLUTION)
        self.session.commit()

        final_snapshot = self.service.reconstruct_case_state(case_id)
        return {
            "status": "SUCCESS",
            "state": final_snapshot.status,
            "money_moved": final_snapshot.money_moved,
            "latest_memo_ref": final_snapshot.latest_memo_ref,
            "pending_deadlines_count": len(final_snapshot.pending_deadlines),
        }

    def _effect_failure(self, case_id: str, outcome) -> Optional[Dict[str, Any]]:
        if outcome.is_success or outcome.code == OutcomeCode.ALREADY_APPLIED:
            return None
        if outcome.category == OutcomeCategory.UNCERTAIN_EFFECT:
            self.transition(case_id, RegEState.UNCERTAIN_EFFECT)
            status = "UNCERTAIN_EFFECT"
        elif outcome.category == OutcomeCategory.HARD_FAILURE:
            self.transition(case_id, RegEState.FAILED)
            status = "FAILED"
        else:
            self.transition(case_id, RegEState.NEEDS_HUMAN)
            status = "NEEDS_HUMAN"
        return {"status": status, "code": outcome.code.value, "message": outcome.message}
