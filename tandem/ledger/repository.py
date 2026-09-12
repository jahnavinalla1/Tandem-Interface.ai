"""Data access repository for procedure ledger operations."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from tandem.domain.effects import EffectClaimStatus
from tandem.domain.identity import EffectIdentity
from tandem.domain.money import parse_money
from tandem.domain.outcomes import OutcomeCode
from tandem.ledger.events import (
    GENESIS_HASH,
    ChainVerification,
    canonical_payload,
    compute_event_hash,
)
from tandem.ledger.models import (
    BrowserSessionRecord,
    CapabilityExecutionRecord,
    DeadlineRecord,
    EffectClaimRecord,
    EffectIntentRecord,
    EventStreamHeadRecord,
    HumanHandoffRecord,
    LeaseRecord,
    ObligationRecord,
    ProcedureCaseRecord,
    ProcedureEventRecord,
)


@dataclass(frozen=True)
class EffectClaim:
    """Result of an atomic effect-reservation attempt."""

    acquired: bool
    status: str
    fencing_token: int
    owner_id: str


class LedgerRepository:
    """Encapsulates all database operations for the append-only procedure ledger."""

    def __init__(self, session: Session):
        self.session = session

    # -----------------------------------------------------------------------
    # Case Operations
    # -----------------------------------------------------------------------
    def get_case(self, case_id: str) -> Optional[ProcedureCaseRecord]:
        stmt = select(ProcedureCaseRecord).where(ProcedureCaseRecord.case_id == case_id)
        return self.session.scalar(stmt)

    def list_cases(self) -> List[ProcedureCaseRecord]:
        stmt = select(ProcedureCaseRecord).order_by(ProcedureCaseRecord.opened_at.desc())
        return list(self.session.scalars(stmt).all())

    def create_or_get_case(
        self,
        case_id: str,
        member_id: str,
        amount: Decimal = Decimal("0.00"),
        currency: str = "USD",
        procedure_name: str = "reg_e_dispute",
    ) -> ProcedureCaseRecord:
        # Two workers may open the same case concurrently; the unique primary
        # key decides, never a SELECT-then-INSERT read.
        now = datetime.now(timezone.utc)
        insert_stmt = sqlite_insert(ProcedureCaseRecord).values(
            case_id=case_id,
            member_id=member_id,
            amount=parse_money(amount),
            currency=currency,
            procedure_name=procedure_name,
            status="RECEIVED",
            money_moved=False,
            opened_at=now,
            updated_at=now,
        )
        result = self.session.execute(
            insert_stmt.on_conflict_do_nothing(index_elements=["case_id"])
        )
        self.session.flush()
        case = self.get_case(case_id)
        if case is None:
            raise RuntimeError(f"Case {case_id} could not be created or read")
        if bool(getattr(result, "rowcount", 0) == 1):
            self.record_event(
                case_id,
                "CASE_CREATED",
                "orchestrator",
                payload={
                    "member_id": member_id,
                    "amount": str(parse_money(amount)),
                    "currency": currency,
                    "procedure_name": procedure_name,
                    "status": "RECEIVED",
                    "money_moved": False,
                    "opened_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            )
        return case

    def update_case_status(
        self, case_id: str, status: str, money_moved: Optional[bool] = None
    ) -> ProcedureCaseRecord:
        case = self.get_case(case_id)
        if not case:
            raise ValueError(f"Case {case_id} not found")
        case.status = status
        if money_moved is not None:
            case.money_moved = case.money_moved or money_moved
        case.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        self.record_event(
            case_id,
            "CASE_STATUS_UPDATED",
            "orchestrator",
            payload={
                "status": case.status,
                "money_moved": case.money_moved,
                "updated_at": case.updated_at.isoformat(),
            },
        )
        return case

    # -----------------------------------------------------------------------
    # Event Log
    # -----------------------------------------------------------------------
    def record_event(
        self,
        case_id: str,
        event_type: str,
        step_name: str,
        actor: str = "AUTOMATION",
        payload: Optional[Dict[str, Any]] = None,
    ) -> ProcedureEventRecord:
        # The insert-or-ignore is deliberately a write: on SQLite it acquires
        # the writer lock before the stream head is read, serializing competing
        # appenders without a SELECT-then-INSERT race.
        self.session.execute(
            sqlite_insert(EventStreamHeadRecord)
            .values(
                case_id=case_id,
                last_sequence=0,
                last_event_hash=GENESIS_HASH,
            )
            .on_conflict_do_nothing(index_elements=["case_id"])
        )
        head = self.session.get(EventStreamHeadRecord, case_id)
        if head is None:
            raise RuntimeError(f"Event stream head for {case_id} was not created")
        sequence = head.last_sequence + 1
        previous_hash = head.last_event_hash
        event_id = str(uuid4())
        timestamp = datetime.now(timezone.utc)
        created_at = timestamp.isoformat()
        serialized_payload = canonical_payload(payload)
        event_hash = compute_event_hash(
            event_id=event_id,
            case_id=case_id,
            sequence=sequence,
            event_type=event_type,
            step_name=step_name,
            actor=actor,
            payload=serialized_payload,
            created_at=created_at,
            previous_event_hash=previous_hash,
        )
        event = ProcedureEventRecord(
            event_id=event_id,
            case_id=case_id,
            sequence=sequence,
            event_type=event_type,
            step_name=step_name,
            actor=actor,
            payload=serialized_payload,
            timestamp=timestamp,
            created_at=created_at,
            previous_event_hash=previous_hash,
            event_hash=event_hash,
        )
        self.session.add(event)
        head.last_sequence = sequence
        head.last_event_hash = event_hash
        self.session.flush()
        return event

    def get_events_for_case(self, case_id: str) -> List[ProcedureEventRecord]:
        stmt = (
            select(ProcedureEventRecord)
            .where(ProcedureEventRecord.case_id == case_id)
            .order_by(ProcedureEventRecord.sequence.asc())
        )
        return list(self.session.scalars(stmt).all())

    def verify_event_chain(self, case_id: str) -> ChainVerification:
        """Recompute and verify sequence continuity and every chained digest."""
        previous_hash = GENESIS_HASH
        expected_sequence = 1
        for event in self.get_events_for_case(case_id):
            if event.sequence != expected_sequence or event.previous_event_hash != previous_hash:
                return ChainVerification(
                    valid=False,
                    broken_sequence=expected_sequence,
                    message="Event sequence or previous hash is discontinuous",
                )
            computed = compute_event_hash(
                event_id=event.event_id,
                case_id=event.case_id,
                sequence=event.sequence,
                event_type=event.event_type,
                step_name=event.step_name,
                actor=event.actor,
                payload=event.payload,
                created_at=event.created_at,
                previous_event_hash=event.previous_event_hash,
            )
            if computed != event.event_hash:
                return ChainVerification(
                    valid=False,
                    broken_sequence=event.sequence,
                    message="Event digest does not match its immutable content",
                )
            previous_hash = event.event_hash
            expected_sequence += 1
        return ChainVerification(valid=True)

    # -----------------------------------------------------------------------
    # Capability Execution Tracking
    # -----------------------------------------------------------------------
    def start_execution(
        self,
        case_id: str,
        capability_id: str,
        capability_version: str,
        effect_class: str,
        idempotency_key: Optional[str] = None,
        expected_entity: Optional[str] = None,
        expected_amount: Optional[Decimal] = None,
        actor: str = "AUTOMATION",
        browser_session_id: Optional[str] = None,
    ) -> CapabilityExecutionRecord:
        record = CapabilityExecutionRecord(
            case_id=case_id,
            capability_id=capability_id,
            capability_version=capability_version,
            effect_class=effect_class,
            idempotency_key=idempotency_key,
            status="RUNNING",
            expected_entity=expected_entity,
            expected_amount=parse_money(expected_amount) if expected_amount is not None else None,
            actor=actor,
            browser_session_id=browser_session_id,
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(record)
        self.session.flush()
        self.record_event(
            case_id,
            "CAPABILITY_EXECUTION_STARTED",
            capability_id,
            actor=actor,
            payload={
                "execution_id": record.id,
                "capability_id": capability_id,
                "capability_version": capability_version,
                "effect_class": effect_class,
                "idempotency_key": idempotency_key,
                "status": "RUNNING",
                "expected_entity": expected_entity,
                "expected_amount": (
                    str(record.expected_amount) if record.expected_amount is not None else None
                ),
                "actor": actor,
                "browser_session_id": browser_session_id,
                "started_at": record.started_at.isoformat(),
            },
        )
        return record

    def complete_execution(
        self,
        execution_id: int,
        status: str,
        observed_entity: Optional[str] = None,
        observed_amount: Optional[Decimal] = None,
        failure_category: Optional[str] = None,
        audit_ref: Optional[str] = None,
        money_moved: bool = False,
    ) -> CapabilityExecutionRecord:
        stmt = select(CapabilityExecutionRecord).where(CapabilityExecutionRecord.id == execution_id)
        record = self.session.scalar(stmt)
        if not record:
            raise ValueError(f"Execution record {execution_id} not found")

        record.status = status
        record.observed_entity = observed_entity
        record.observed_amount = (
            parse_money(observed_amount) if observed_amount is not None else None
        )
        record.failure_category = failure_category
        record.audit_ref = audit_ref
        record.money_moved = money_moved
        record.completed_at = datetime.now(timezone.utc)
        self.session.flush()
        self.record_event(
            record.case_id,
            "CAPABILITY_EXECUTION_COMPLETED",
            record.capability_id,
            actor=record.actor,
            payload={
                "execution_id": record.id,
                "status": record.status,
                "observed_entity": record.observed_entity,
                "observed_amount": (
                    str(record.observed_amount) if record.observed_amount is not None else None
                ),
                "failure_category": record.failure_category,
                "audit_ref": record.audit_ref,
                "money_moved": record.money_moved,
                "completed_at": record.completed_at.isoformat(),
            },
        )
        return record

    def get_executions_for_case(self, case_id: str) -> List[CapabilityExecutionRecord]:
        stmt = (
            select(CapabilityExecutionRecord)
            .where(CapabilityExecutionRecord.case_id == case_id)
            .order_by(CapabilityExecutionRecord.id.asc())
        )
        return list(self.session.scalars(stmt).all())

    # -----------------------------------------------------------------------
    # Effect Intent
    # -----------------------------------------------------------------------
    def stage_intent(
        self, case_id: str, idempotency_key: str, capability_id: str
    ) -> EffectIntentRecord:
        intent = self.find_intent(idempotency_key)
        if not intent:
            intent = EffectIntentRecord(
                case_id=case_id,
                idempotency_key=idempotency_key,
                capability_id=capability_id,
                intent_status="STAGED",
            )
            self.session.add(intent)
            self.session.flush()
            self.record_event(
                case_id,
                "INTENT_STAGED",
                capability_id,
                payload={
                    "idempotency_key": idempotency_key,
                    "capability_id": capability_id,
                    "intent_status": intent.intent_status,
                    "created_at": intent.created_at.isoformat(),
                },
            )
        return intent

    def find_intent(self, idempotency_key: str) -> Optional[EffectIntentRecord]:
        stmt = select(EffectIntentRecord).where(
            EffectIntentRecord.idempotency_key == idempotency_key
        )
        return self.session.scalar(stmt)

    def mark_intent_committed(self, idempotency_key: str) -> None:
        intent = self.find_intent(idempotency_key)
        if intent and intent.intent_status != "COMMITTED":
            intent.intent_status = "COMMITTED"
            self.session.flush()
            self.record_event(
                intent.case_id,
                "INTENT_COMMITTED",
                intent.capability_id,
                payload={"idempotency_key": idempotency_key},
            )

    # -----------------------------------------------------------------------
    # Atomic Effect Claim
    # -----------------------------------------------------------------------
    def claim_effect(
        self,
        identity: EffectIdentity,
        owner_id: str,
        now: Optional[datetime] = None,
        ttl: timedelta = timedelta(minutes=5),
    ) -> EffectClaim:
        """Atomically reserve one external effect using SQLite's unique constraint."""
        claim_time = now or datetime.now(timezone.utc)
        values = {
            "idempotency_key": identity.idempotency_key,
            **identity.canonical_payload,
            "amount": identity.amount,
            "status": EffectClaimStatus.CLAIMED.value,
            "owner_id": owner_id,
            "fencing_token": 1,
            "claimed_at": claim_time,
            "heartbeat_at": claim_time,
            "expires_at": claim_time + ttl,
            "updated_at": claim_time,
        }
        insert_stmt = sqlite_insert(EffectClaimRecord).values(**values)
        insert_stmt = insert_stmt.on_conflict_do_nothing(index_elements=["idempotency_key"])
        result = self.session.execute(insert_stmt)
        self.session.flush()

        record = self.get_effect_claim(identity.idempotency_key)
        if record is None:
            raise RuntimeError("Effect claim insert completed without a readable claim")
        acquired = bool(getattr(result, "rowcount", 0) == 1)
        if not acquired and record.status == EffectClaimStatus.FAILED_RETRYABLE.value:
            reclaim = (
                update(EffectClaimRecord)
                .where(
                    EffectClaimRecord.idempotency_key == identity.idempotency_key,
                    EffectClaimRecord.status == EffectClaimStatus.FAILED_RETRYABLE.value,
                    EffectClaimRecord.fencing_token == record.fencing_token,
                )
                .values(
                    status=EffectClaimStatus.CLAIMED.value,
                    owner_id=owner_id,
                    fencing_token=record.fencing_token + 1,
                    claimed_at=claim_time,
                    heartbeat_at=claim_time,
                    expires_at=claim_time + ttl,
                    updated_at=claim_time,
                )
            )
            reclaim_result = self.session.execute(reclaim)
            acquired = bool(getattr(reclaim_result, "rowcount", 0) == 1)
            if acquired:
                self.session.expire(record)
                self.session.refresh(record)
        if acquired and self.get_case(identity.case_id) is not None:
            self.record_event(
                identity.case_id,
                "EFFECT_CLAIMED",
                identity.capability_id,
                payload={
                    "idempotency_key": identity.idempotency_key,
                    "owner_id": owner_id,
                    "fencing_token": record.fencing_token,
                    "institution_id": record.institution_id,
                    "procedure_id": record.procedure_id,
                    "case_id": record.case_id,
                    "capability_id": record.capability_id,
                    "member_id": record.member_id,
                    "account_id": record.account_id,
                    "amount": str(record.amount),
                    "currency": record.currency,
                    "business_reference": record.business_reference,
                    "status": record.status,
                    "claimed_at": record.claimed_at.isoformat(),
                    "heartbeat_at": record.heartbeat_at.isoformat(),
                    "expires_at": record.expires_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                },
            )
        return EffectClaim(
            acquired=acquired,
            status=record.status if acquired else self._existing_claim_outcome(record.status),
            fencing_token=record.fencing_token,
            owner_id=record.owner_id,
        )

    @staticmethod
    def _existing_claim_outcome(status: str) -> str:
        if status == EffectClaimStatus.APPLIED.value:
            return OutcomeCode.ALREADY_APPLIED.value
        return OutcomeCode.ALREADY_CLAIMED.value

    def get_effect_claim(self, idempotency_key: str) -> Optional[EffectClaimRecord]:
        stmt = select(EffectClaimRecord).where(
            EffectClaimRecord.idempotency_key == idempotency_key
        )
        return self.session.scalar(stmt)

    def get_effect_claims_for_case(self, case_id: str) -> List[EffectClaimRecord]:
        stmt = (
            select(EffectClaimRecord)
            .where(EffectClaimRecord.case_id == case_id)
            .order_by(EffectClaimRecord.id.asc())
        )
        return list(self.session.scalars(stmt).all())

    def transition_effect_claim(
        self,
        idempotency_key: str,
        owner_id: str,
        fencing_token: int,
        from_statuses: set[EffectClaimStatus],
        to_status: EffectClaimStatus,
        now: Optional[datetime] = None,
    ) -> bool:
        """Conditionally transition a claim only for its current fenced owner."""
        transition_time = now or datetime.now(timezone.utc)
        stmt = (
            update(EffectClaimRecord)
            .where(
                EffectClaimRecord.idempotency_key == idempotency_key,
                EffectClaimRecord.owner_id == owner_id,
                EffectClaimRecord.fencing_token == fencing_token,
                EffectClaimRecord.status.in_([status.value for status in from_statuses]),
            )
            .values(status=to_status.value, updated_at=transition_time, heartbeat_at=transition_time)
        )
        result = self.session.execute(stmt)
        self.session.flush()
        transitioned = bool(getattr(result, "rowcount", 0) == 1)
        if transitioned:
            record = self.get_effect_claim(idempotency_key)
            if record is None:
                raise RuntimeError("Transitioned effect claim could not be read")
            self.record_event(
                record.case_id,
                "EFFECT_CLAIM_TRANSITIONED",
                record.capability_id,
                payload={
                    "idempotency_key": idempotency_key,
                    "status": record.status,
                    "owner_id": record.owner_id,
                    "fencing_token": record.fencing_token,
                    "heartbeat_at": record.heartbeat_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                },
            )
        return transitioned

    def validate_effect_token(
        self,
        idempotency_key: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        record = self.get_effect_claim(idempotency_key)
        return bool(
            record
            and record.owner_id == owner_id
            and record.fencing_token == fencing_token
            and record.status
            in {EffectClaimStatus.CLAIMED.value, EffectClaimStatus.APPLYING.value}
        )

    # -----------------------------------------------------------------------
    # Deadlines
    # -----------------------------------------------------------------------
    def create_deadline(self, case_id: str, deadline_type: str, due_at: datetime) -> DeadlineRecord:
        existing = self.session.scalar(
            select(DeadlineRecord).where(
                DeadlineRecord.case_id == case_id,
                DeadlineRecord.deadline_type == deadline_type,
            )
        )
        if existing:
            return existing
        record = DeadlineRecord(
            case_id=case_id,
            deadline_type=deadline_type,
            due_at=due_at,
            status="PENDING",
        )
        self.session.add(record)
        self.session.flush()
        self.record_event(
            case_id,
            "DEADLINE_CREATED",
            "orchestrator",
            payload={
                "deadline_type": deadline_type,
                "due_at": due_at.isoformat(),
                "status": record.status,
            },
        )
        return record

    def resolve_deadline(self, case_id: str, deadline_type: str) -> Optional[DeadlineRecord]:
        # A deadline resolved after it lapsed (status already flipped to OVERDUE by
        # `mark_overdue_deadlines`) must still transition to MET -- it was satisfied
        # late, not never. Restricting this to PENDING only would strand a genuinely
        # satisfied obligation at OVERDUE forever.
        stmt = select(DeadlineRecord).where(
            DeadlineRecord.case_id == case_id,
            DeadlineRecord.deadline_type == deadline_type,
            DeadlineRecord.status.in_(["PENDING", "OVERDUE"]),
        )
        record = self.session.scalar(stmt)
        if record:
            record.status = "MET"
            record.resolved_at = datetime.now(timezone.utc)
            self.session.flush()
            self.record_event(
                case_id,
                "DEADLINE_RESOLVED",
                "orchestrator",
                payload={
                    "deadline_type": deadline_type,
                    "status": record.status,
                    "resolved_at": record.resolved_at.isoformat(),
                },
            )
        return record

    def get_deadlines_for_case(self, case_id: str) -> List[DeadlineRecord]:
        stmt = select(DeadlineRecord).where(DeadlineRecord.case_id == case_id)
        return list(self.session.scalars(stmt).all())

    def mark_overdue_deadlines(
        self, case_id: str, now: Optional[datetime] = None
    ) -> List[DeadlineRecord]:
        """Transition any PENDING deadline whose due date has lapsed into OVERDUE.

        The original model never evaluated a persisted deadline against the clock, so a
        lapsed statutory deadline stayed labelled PENDING forever. This is the lifecycle
        evaluation step: called on every case-state reconstruction so stale reads never
        report a lapsed deadline as still on track.
        """
        from tandem.workflow.deadlines import evaluate_deadlines

        reference = now or datetime.now(timezone.utc)
        pending = list(
            self.session.scalars(
                select(DeadlineRecord).where(
                    DeadlineRecord.case_id == case_id,
                    DeadlineRecord.status == "PENDING",
                )
            ).all()
        )
        if not pending:
            return []

        statuses = evaluate_deadlines({d.deadline_type: d.due_at for d in pending}, now=reference)
        overdue = [d for d in pending if statuses.get(d.deadline_type) == "OVERDUE"]
        for record in overdue:
            record.status = "OVERDUE"
        if overdue:
            self.session.flush()
            for record in overdue:
                self.record_event(
                    case_id,
                    "DEADLINE_OVERDUE",
                    "orchestrator",
                    payload={
                        "deadline_type": record.deadline_type,
                        "due_at": record.due_at.isoformat(),
                    },
                )
        return overdue

    # -----------------------------------------------------------------------
    # Durable Regulatory Obligations
    # -----------------------------------------------------------------------
    def create_obligation(
        self, case_id: str, obligation_type: str, due_at: datetime
    ) -> ObligationRecord:
        existing = self.session.scalar(
            select(ObligationRecord).where(
                ObligationRecord.case_id == case_id,
                ObligationRecord.obligation_type == obligation_type,
            )
        )
        if existing:
            return existing
        obligation = ObligationRecord(
            case_id=case_id,
            obligation_type=obligation_type,
            due_at=due_at,
            status="PLANNED",
        )
        self.session.add(obligation)
        self.session.flush()
        self.record_event(
            case_id,
            "OBLIGATION_CREATED",
            "orchestrator",
            payload={
                "obligation_type": obligation_type,
                "due_at": due_at.isoformat(),
                "status": obligation.status,
                "created_at": obligation.created_at.isoformat(),
            },
        )
        return obligation

    def get_obligations_for_case(self, case_id: str) -> List[ObligationRecord]:
        stmt = (
            select(ObligationRecord)
            .where(ObligationRecord.case_id == case_id)
            .order_by(ObligationRecord.id.asc())
        )
        return list(self.session.scalars(stmt).all())

    def activate_obligation(self, case_id: str, obligation_type: str) -> ObligationRecord:
        obligation = self._get_obligation(case_id, obligation_type)
        if obligation.status == "PLANNED":
            obligation.status = "ACTIVE"
            obligation.activated_at = datetime.now(timezone.utc)
            self.record_event(
                case_id,
                "OBLIGATION_ACTIVATED",
                "orchestrator",
                payload={
                    "obligation_type": obligation_type,
                    "status": obligation.status,
                    "activated_at": obligation.activated_at.isoformat(),
                },
            )
        self.session.flush()
        return obligation

    def satisfy_obligation(self, case_id: str, obligation_type: str) -> ObligationRecord:
        obligation = self._get_obligation(case_id, obligation_type)
        if obligation.status == "SATISFIED":
            return obligation
        obligation.status = "SATISFIED"
        obligation.satisfied_at = datetime.now(timezone.utc)
        self.record_event(
            case_id,
            "OBLIGATION_SATISFIED",
            "orchestrator",
            payload={
                "obligation_type": obligation_type,
                "status": obligation.status,
                "satisfied_at": obligation.satisfied_at.isoformat(),
            },
        )
        self.session.flush()
        return obligation

    def _get_obligation(self, case_id: str, obligation_type: str) -> ObligationRecord:
        obligation = self.session.scalar(
            select(ObligationRecord).where(
                ObligationRecord.case_id == case_id,
                ObligationRecord.obligation_type == obligation_type,
            )
        )
        if obligation is None:
            raise ValueError(f"Missing obligation {obligation_type} for case {case_id}")
        return obligation

    # -----------------------------------------------------------------------
    # Single-Owner Lease (AUTOMATION vs HUMAN)
    # -----------------------------------------------------------------------
    def acquire_lease(
        self,
        case_id: str,
        owner: str,
        owner_type: str | None = None,
        now: datetime | None = None,
        ttl: timedelta = timedelta(minutes=5),
        resource_id: str | None = None,
    ) -> LeaseRecord:
        """Atomically acquire or renew a lease; never overwrite an active owner."""

        from tandem.domain.errors import LeaseConflictError

        acquired_at = now or datetime.now(timezone.utc)
        expires_at = acquired_at + ttl
        normalized_type = owner_type or (
            "AUTOMATION" if owner.startswith("AUTOMATION") else "HUMAN"
        )
        if normalized_type not in {"AUTOMATION", "HUMAN"}:
            raise ValueError("owner_type must be AUTOMATION or HUMAN")
        if ttl <= timedelta(0):
            raise ValueError("Lease TTL must be positive")

        insert_result = self.session.execute(
            sqlite_insert(LeaseRecord)
            .values(
                case_id=case_id,
                resource_id=resource_id or case_id,
                owner_type=normalized_type,
                owner_id=owner,
                fencing_token=1,
                version=1,
                acquired_at=acquired_at,
                heartbeat_at=acquired_at,
                expires_at=expires_at,
                released_at=None,
            )
            .on_conflict_do_nothing(index_elements=["case_id"])
        )
        if getattr(insert_result, "rowcount", 0) == 1:
            self.record_event(
                case_id,
                "CONTROL_LEASE_ACQUIRED",
                "ownership",
                actor=normalized_type,
                payload={
                    "owner_type": normalized_type,
                    "owner_id": owner,
                    "fencing_token": 1,
                    "resource_id": resource_id or case_id,
                    "expires_at": expires_at.isoformat(),
                },
            )
            lease = self.get_lease(case_id)
            assert lease is not None
            return lease

        lease = self.get_lease(case_id)
        assert lease is not None
        if self._lease_is_active(lease, acquired_at):
            if lease.owner_id != owner or lease.owner_type != normalized_type:
                raise LeaseConflictError(
                    f"Resource {case_id} lease is held by {lease.owner_type} "
                    f"owner '{lease.owner_id}' until {lease.expires_at.isoformat()}"
                )
            return self.heartbeat_lease(
                case_id,
                owner,
                lease.fencing_token,
                now=acquired_at,
                ttl=ttl,
            )

        prior_version = lease.version
        prior_token = lease.fencing_token
        takeover = self.session.execute(
            update(LeaseRecord)
            .where(
                LeaseRecord.case_id == case_id,
                LeaseRecord.version == prior_version,
                (LeaseRecord.released_at.is_not(None))
                | (LeaseRecord.expires_at <= acquired_at),
            )
            .values(
                resource_id=resource_id or case_id,
                owner_type=normalized_type,
                owner_id=owner,
                fencing_token=prior_token + 1,
                version=prior_version + 1,
                acquired_at=acquired_at,
                heartbeat_at=acquired_at,
                expires_at=expires_at,
                released_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(takeover, "rowcount", 0) != 1:
            raise LeaseConflictError(f"Resource {case_id} lease changed during acquisition")
        self.record_event(
            case_id,
            "CONTROL_LEASE_ACQUIRED",
            "ownership",
            actor=normalized_type,
            payload={
                "owner_type": normalized_type,
                "owner_id": owner,
                "fencing_token": prior_token + 1,
                "resource_id": resource_id or case_id,
                "expires_at": expires_at.isoformat(),
            },
        )
        acquired = self.get_lease(case_id)
        assert acquired is not None
        return acquired

    def get_lease(self, case_id: str) -> Optional[LeaseRecord]:
        stmt = (
            select(LeaseRecord)
            .where(LeaseRecord.case_id == case_id)
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(stmt)

    def validate_lease_token(
        self,
        case_id: str,
        owner_id: str,
        fencing_token: int,
        now: datetime | None = None,
    ) -> bool:
        """Return whether this actor still holds the current unexpired fence."""

        checked_at = now or datetime.now(timezone.utc)
        lease = self.get_lease(case_id)
        return bool(
            lease
            and lease.owner_id == owner_id
            and lease.fencing_token == fencing_token
            and self._lease_is_active(lease, checked_at)
        )

    def heartbeat_lease(
        self,
        case_id: str,
        owner_id: str,
        fencing_token: int,
        now: datetime | None = None,
        ttl: timedelta = timedelta(minutes=5),
    ) -> LeaseRecord:
        """Extend a live lease only when owner and fence still match."""

        from tandem.domain.errors import LeaseConflictError

        heartbeat_at = now or datetime.now(timezone.utc)
        result = self.session.execute(
            update(LeaseRecord)
            .where(
                LeaseRecord.case_id == case_id,
                LeaseRecord.owner_id == owner_id,
                LeaseRecord.fencing_token == fencing_token,
                LeaseRecord.released_at.is_(None),
                LeaseRecord.expires_at > heartbeat_at,
            )
            .values(
                heartbeat_at=heartbeat_at,
                expires_at=heartbeat_at + ttl,
                version=LeaseRecord.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            raise LeaseConflictError(
                f"Lease token {fencing_token} for {case_id} is stale or expired"
            )
        lease = self.get_lease(case_id)
        assert lease is not None
        return lease

    def release_lease(
        self,
        case_id: str,
        owner_id: str | None = None,
        fencing_token: int | None = None,
        now: datetime | None = None,
    ) -> Optional[LeaseRecord]:
        """Release a lease, optionally requiring an exact owner/fence match."""

        from tandem.domain.errors import LeaseConflictError

        released_at = now or datetime.now(timezone.utc)
        lease = self.get_lease(case_id)
        if lease is None:
            return None
        conditions = [LeaseRecord.case_id == case_id, LeaseRecord.released_at.is_(None)]
        if owner_id is not None:
            conditions.append(LeaseRecord.owner_id == owner_id)
        if fencing_token is not None:
            conditions.append(LeaseRecord.fencing_token == fencing_token)
        result = self.session.execute(
            update(LeaseRecord)
            .where(*conditions)
            .values(
                released_at=released_at,
                expires_at=released_at,
                heartbeat_at=released_at,
                version=LeaseRecord.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        rowcount = getattr(result, "rowcount", 0)
        if rowcount != 1 and (owner_id is not None or fencing_token is not None):
            raise LeaseConflictError(f"Lease for {case_id} changed before release")
        if rowcount == 1:
            self.record_event(
                case_id,
                "CONTROL_LEASE_RELEASED",
                "ownership",
                actor=lease.owner_type,
                payload={
                    "owner_type": lease.owner_type,
                    "owner_id": lease.owner_id,
                    "fencing_token": lease.fencing_token,
                    "released_at": released_at.isoformat(),
                },
            )
        return self.get_lease(case_id)

    @staticmethod
    def _lease_is_active(lease: LeaseRecord, now: datetime) -> bool:
        expires_at = lease.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        checked_at = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        return lease.released_at is None and expires_at > checked_at

    # -----------------------------------------------------------------------
    # Human Handoff
    # -----------------------------------------------------------------------
    def register_browser_session(
        self,
        case_id: str,
        session_id: str,
        current_url: str = "about:blank",
    ) -> BrowserSessionRecord:
        """Persist a stable ID for a browser context owned by a worker."""

        now = datetime.now(timezone.utc)
        record = BrowserSessionRecord(
            session_id=session_id,
            case_id=case_id,
            status="ACTIVE",
            current_url=current_url,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        self.session.flush()
        self.record_event(
            case_id,
            "BROWSER_SESSION_OPENED",
            "browser_session",
            payload={
                "session_id": session_id,
                "status": "ACTIVE",
                "current_url": current_url,
                "created_at": now.isoformat(),
            },
        )
        return record

    def get_browser_session(self, session_id: str) -> Optional[BrowserSessionRecord]:
        return self.session.scalar(
            select(BrowserSessionRecord).where(BrowserSessionRecord.session_id == session_id)
        )

    def update_browser_session(
        self,
        session_id: str,
        current_url: str,
        status: str = "ACTIVE",
    ) -> BrowserSessionRecord:
        record = self.get_browser_session(session_id)
        if record is None:
            raise ValueError(f"Unknown browser session '{session_id}'")
        record.current_url = current_url
        record.status = status
        record.updated_at = datetime.now(timezone.utc)
        if status == "CLOSED":
            record.closed_at = record.updated_at
        self.session.flush()
        return record

    def record_handoff(
        self, case_id: str, reason: str, operator_id: str, action_taken: str
    ) -> HumanHandoffRecord:
        handoff = HumanHandoffRecord(
            case_id=case_id,
            reason=reason,
            operator_id=operator_id,
            action_taken=action_taken,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        self.session.add(handoff)
        self.session.flush()
        return handoff
