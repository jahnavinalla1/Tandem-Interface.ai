"""SQLAlchemy 2.x relational models for the append-only procedure ledger."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ProcedureCaseRecord(Base):
    """Aggregated case record tracking overall dispute lifecycle and state."""

    __tablename__ = "procedure_cases"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(64), index=True)
    procedure_name: Mapped[str] = mapped_column(String(64), default="reg_e_dispute")
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(64), default="RECEIVED")
    money_moved: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    events: Mapped[list["ProcedureEventRecord"]] = relationship(
        back_populates="case", passive_deletes=True, order_by="ProcedureEventRecord.sequence"
    )
    executions: Mapped[list["CapabilityExecutionRecord"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="CapabilityExecutionRecord.id"
    )


class ProcedureEventRecord(Base):
    """Append-only audit event log for the procedure."""

    __tablename__ = "procedure_events"

    __table_args__ = (UniqueConstraint("case_id", "sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("procedure_cases.case_id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(
        String(64)
    )  # e.g. STEP_STARTED, STEP_COMPLETED, HANDOFF
    step_name: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(32), default="AUTOMATION")  # AUTOMATION or HUMAN
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    created_at: Mapped[str] = mapped_column(String(40))
    previous_event_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    case: Mapped["ProcedureCaseRecord"] = relationship(back_populates="events")


class EventStreamHeadRecord(Base):
    """Mutable coordination head used to serialize appends to one case stream."""

    __tablename__ = "event_stream_heads"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    last_event_hash: Mapped[str] = mapped_column(String(64))


class CapabilityExecutionRecord(Base):
    """Detailed record of each single capability execution."""

    __tablename__ = "capability_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("procedure_cases.case_id"), index=True
    )
    capability_id: Mapped[str] = mapped_column(String(128))
    capability_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    effect_class: Mapped[str] = mapped_column(String(16))  # READ, STAGE, COMMIT
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, index=True)

    status: Mapped[str] = mapped_column(
        String(32)
    )  # RUNNING, SUCCESS, BUSINESS_OUTCOME, FAILED, etc.
    expected_entity: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    observed_entity: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expected_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 2), nullable=True)
    observed_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 2), nullable=True)

    failure_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    audit_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    money_moved: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(String(32), default="AUTOMATION")
    browser_session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    case: Mapped["ProcedureCaseRecord"] = relationship(back_populates="executions")


class EffectIntentRecord(Base):
    """Staged intent prior to executing an irreversible COMMIT action."""

    __tablename__ = "effect_intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    capability_id: Mapped[str] = mapped_column(String(128))
    intent_status: Mapped[str] = mapped_column(
        String(32), default="STAGED"
    )  # STAGED, COMMITTED, RECONCILED
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class EffectClaimRecord(Base):
    """Atomic, fenced reservation for one immutable external effect."""

    __tablename__ = "effect_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    institution_id: Mapped[str] = mapped_column(String(64), index=True)
    procedure_id: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    capability_id: Mapped[str] = mapped_column(String(128))
    member_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 2))
    currency: Mapped[str] = mapped_column(String(3))
    business_reference: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="CLAIMED", index=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    fencing_token: Mapped[int] = mapped_column(Integer, default=1)
    claimed_at: Mapped[datetime] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class DeadlineRecord(Base):
    """Statutory and business-day deadlines attached to a case (e.g. 12 CFR 1005.11)."""

    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("procedure_cases.case_id"), index=True
    )
    deadline_type: Mapped[str] = mapped_column(
        String(64)
    )  # e.g. NOTICE_2_DAY, INVESTIGATION_10_DAY
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING, MET, OVERDUE
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ObligationRecord(Base):
    """Durable source fact from which regulated deadline projections are derived."""

    __tablename__ = "obligations"
    __table_args__ = (UniqueConstraint("case_id", "obligation_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("procedure_cases.case_id"), index=True
    )
    obligation_type: Mapped[str] = mapped_column(String(64), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(32), default="PLANNED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    satisfied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class LeaseRecord(Base):
    """Expiring, fenced single-owner lease for a case/browser resource."""

    __tablename__ = "case_leases"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_type: Mapped[str] = mapped_column(String(16))
    owner_id: Mapped[str] = mapped_column("owner", String(128))
    fencing_token: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    acquired_at: Mapped[datetime] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def owner(self) -> str:
        """Backward-compatible display name for the concrete owner ID."""

        return self.owner_id


class BrowserSessionRecord(Base):
    """Durable identity and last-known state for a worker-owned browser context."""

    __tablename__ = "browser_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    current_url: Mapped[str] = mapped_column(Text, default="about:blank")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class HumanHandoffRecord(Base):
    """Audit log of operator interventions during live session handoff."""

    __tablename__ = "human_handoffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(String(256))
    operator_id: Mapped[str] = mapped_column(String(64))
    action_taken: Mapped[str] = mapped_column(String(256))
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
