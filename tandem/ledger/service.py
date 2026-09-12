"""Ledger service and state reconstruction across process restarts."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from tandem.ledger.models import (
    CapabilityExecutionRecord,
    DeadlineRecord,
)
from tandem.ledger.repository import LedgerRepository


@dataclass
class CaseStateSnapshot:
    """Reconstructed state of a business procedure derived entirely from the persisted ledger."""

    case_id: str
    member_id: str
    amount: Decimal
    status: str
    money_moved: bool
    completed_capabilities: List[str] = field(default_factory=list)
    successful_executions: List[CapabilityExecutionRecord] = field(default_factory=list)
    latest_memo_ref: Optional[str] = None
    processor_ref: Optional[str] = None
    pending_deadlines: List[DeadlineRecord] = field(default_factory=list)
    lease_owner: Optional[str] = None
    requires_human: bool = False
    events_count: int = 0


class LedgerService:
    """High-level service for recording events and reconstructing durable procedure state."""

    def __init__(self, session: Session):
        self.session = session
        self.repo = LedgerRepository(session)

    def reconstruct_case_state(self, case_id: str) -> CaseStateSnapshot:
        """Reconstruct case state from persisted SQLite records.

        Survives hard process termination: no in-memory Python state required.
        """
        case = self.repo.get_case(case_id)
        if not case:
            raise ValueError(f"Cannot reconstruct state: Case '{case_id}' does not exist in ledger")

        executions = self.repo.get_executions_for_case(case_id)
        self.repo.mark_overdue_deadlines(case_id)
        deadlines = self.repo.get_deadlines_for_case(case_id)
        lease = self.repo.get_lease(case_id)
        events = self.repo.get_events_for_case(case_id)

        completed_caps: List[str] = []
        successful_execs: List[CapabilityExecutionRecord] = []
        money_moved = case.money_moved
        latest_memo_ref: Optional[str] = None
        processor_ref: Optional[str] = None
        requires_human = False

        for exc in executions:
            if exc.status in (
                "SUCCESS",
                "COMPLETED",
                "CONFIRMED_APPLIED",
                "ALREADY_APPLIED",
            ):
                completed_caps.append(exc.capability_id)
                successful_execs.append(exc)
                if exc.money_moved:
                    money_moved = True
                if exc.audit_ref:
                    if "MC-" in exc.audit_ref:
                        latest_memo_ref = exc.audit_ref
                    elif "VISA-DISP-" in exc.audit_ref or "CB-" in exc.audit_ref:
                        processor_ref = exc.audit_ref
            elif exc.status in ("HARD_FAILURE", "UNCERTAIN_EFFECT"):
                requires_human = True

        # Includes OVERDUE alongside PENDING: an operator must see a lapsed statutory
        # deadline at least as prominently as one still on track. Only MET is excluded.
        pending_deadlines = [d for d in deadlines if d.status != "MET"]

        return CaseStateSnapshot(
            case_id=case.case_id,
            member_id=case.member_id,
            amount=case.amount,
            status=case.status,
            money_moved=money_moved,
            completed_capabilities=completed_caps,
            successful_executions=successful_execs,
            latest_memo_ref=latest_memo_ref,
            processor_ref=processor_ref,
            pending_deadlines=pending_deadlines,
            lease_owner=lease.owner if lease and not lease.released_at else None,
            requires_human=requires_human or (case.status in ("NEEDS_HUMAN", "UNCERTAIN_EFFECT")),
            events_count=len(events),
        )
