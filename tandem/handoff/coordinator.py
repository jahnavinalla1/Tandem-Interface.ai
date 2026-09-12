"""Human Handoff Coordinator.

Manages single-owner mutual-exclusion lease transitions between AUTOMATION and HUMAN
operators, compliance review interstitial handling, operator sign-off, and safe resumption.
"""

from typing import Any, Dict, Optional

from playwright.sync_api import Page
from sqlalchemy.orm import Session

from tandem.domain.errors import LeaseConflictError
from tandem.ledger.models import LeaseRecord
from tandem.ledger.repository import LedgerRepository
from tandem.workflow.state_machine import RegEState


class HandoffCoordinator:
    """Coordinates operator takeover, single-owner leases, and automated resumption."""

    def __init__(self, session: Session):
        self.session = session
        self.repo = LedgerRepository(session)

    def initiate_handoff(
        self,
        case_id: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Release automation lease and place case into NEEDS_HUMAN state."""
        self.repo.release_lease(case_id)
        self.repo.update_case_status(case_id, status=RegEState.NEEDS_HUMAN.value)
        self.repo.record_event(
            case_id=case_id,
            event_type="HUMAN_HANDOFF_INITIATED",
            step_name="handoff_coordinator",
            payload={"reason": reason, "details": details or {}},
        )
        self.session.commit()
        return {
            "case_id": case_id,
            "status": RegEState.NEEDS_HUMAN.value,
            "reason": reason,
            "ticket_id": f"TICKET-{case_id}",
        }

    def claim_operator_lease(self, case_id: str, operator_id: str) -> LeaseRecord:
        """Enforce single-owner lease rule before an operator can act on a case."""
        try:
            lease = self.repo.acquire_lease(
                case_id=case_id,
                owner=operator_id,
                owner_type="HUMAN",
            )
        except LeaseConflictError as exc:
            current = self.repo.get_lease(case_id)
            if current and current.owner_type == "HUMAN":
                owner_description = f"operator '{current.owner_id}'"
            else:
                owner_description = "automation"
            raise LeaseConflictError(
                f"Case {case_id} lease already held by {owner_description}: {exc}"
            ) from exc
        self.repo.record_event(
            case_id=case_id,
            event_type="OPERATOR_LEASE_ACQUIRED",
            step_name="handoff_coordinator",
            payload={"operator_id": operator_id},
        )
        self.session.commit()
        return lease

    def operator_clear_compliance(
        self,
        case_id: str,
        operator_id: str,
        lease_token: int,
        page: Page,
        frame_selector: str = "#core_workspace_frame",
    ) -> Dict[str, Any]:
        """Operator signs off compliance interstitial on the active browser session."""
        if not self.repo.validate_lease_token(case_id, operator_id, lease_token):
            raise LeaseConflictError(
                f"Operator '{operator_id}' holds a stale or inactive lease for case {case_id}."
            )
        self.repo.heartbeat_lease(case_id, operator_id, lease_token)
        self.session.commit()

        frame = page.frame_locator(frame_selector)
        signoff_btn = frame.locator("button.operator-signoff-btn, .operator-signoff-btn")
        signoff_btn.wait_for(state="visible", timeout=5000)
        signoff_btn.click()

        # Wait for iframe to process clear_compliance and navigate back to credit entry form
        try:
            frame.locator("#credit_action_container").wait_for(state="visible", timeout=5000)
        except Exception:
            pass

        # Record handoff audit entry
        self.repo.record_handoff(
            case_id=case_id,
            reason="COMPLIANCE_INTERSTITIAL",
            operator_id=operator_id,
            action_taken="ACKNOWLEDGED_COMPLIANCE_AND_SIGNOFF",
        )
        self.repo.record_event(
            case_id=case_id,
            event_type="OPERATOR_COMPLIANCE_CLEARED",
            step_name="handoff_coordinator",
            payload={"operator_id": operator_id},
        )

        # Release operator lease and yield back to AUTOMATION
        self.repo.release_lease(case_id, operator_id, lease_token)
        self.session.commit()

        return {
            "case_id": case_id,
            "status": "COMPLIANCE_CLEARED",
            "operator_id": operator_id,
            "next_owner": "AUTOMATION",
        }
