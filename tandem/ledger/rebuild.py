"""Rebuild mutable procedure projections from immutable procedure events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tandem.domain.errors import LedgerIntegrityError
from tandem.domain.money import parse_money
from tandem.ledger.models import (
    CapabilityExecutionRecord,
    DeadlineRecord,
    EffectClaimRecord,
    EffectIntentRecord,
    ObligationRecord,
    ProcedureCaseRecord,
)
from tandem.ledger.repository import LedgerRepository


def rebuild_case(session: Session, case_id: str) -> None:
    """Replace mutable case projections with values derived only from its events."""
    repo = LedgerRepository(session)
    verification = repo.verify_event_chain(case_id)
    if not verification.valid:
        raise LedgerIntegrityError(
            f"Cannot rebuild {case_id}: event chain failed at "
            f"sequence {verification.broken_sequence}: {verification.message}"
        )
    events = repo.get_events_for_case(case_id)
    if not events or events[0].event_type != "CASE_CREATED":
        raise LedgerIntegrityError(f"Cannot rebuild {case_id}: CASE_CREATED event is missing")

    for model in (
        CapabilityExecutionRecord,
        EffectIntentRecord,
        EffectClaimRecord,
        DeadlineRecord,
        ObligationRecord,
    ):
        session.execute(delete(model).where(model.case_id == case_id))

    case = session.get(ProcedureCaseRecord, case_id)
    if case is None:
        raise LedgerIntegrityError(f"Cannot rebuild {case_id}: protected case anchor is missing")

    executions: dict[int, CapabilityExecutionRecord] = {}
    intents: dict[str, EffectIntentRecord] = {}
    claims: dict[str, EffectClaimRecord] = {}
    deadlines: dict[str, DeadlineRecord] = {}
    obligations: dict[str, ObligationRecord] = {}

    for event in events:
        payload = _payload(event.payload)
        event_type = event.event_type
        if event_type == "CASE_CREATED":
            case.member_id = str(payload["member_id"])
            case.amount = parse_money(payload["amount"])
            case.currency = str(payload["currency"])
            case.procedure_name = str(payload["procedure_name"])
            case.status = str(payload["status"])
            case.money_moved = bool(payload["money_moved"])
            case.opened_at = _datetime(payload["opened_at"])
            case.updated_at = _datetime(payload["updated_at"])
        elif event_type == "CASE_STATUS_UPDATED":
            case.status = str(payload["status"])
            case.money_moved = bool(payload["money_moved"])
            case.updated_at = _datetime(payload["updated_at"])
        elif event_type == "MONEY_MOVED":
            case.money_moved = True
        elif event_type == "CAPABILITY_EXECUTION_STARTED":
            source_id = int(payload["execution_id"])
            execution_record = CapabilityExecutionRecord(
                case_id=case_id,
                capability_id=str(payload["capability_id"]),
                capability_version=str(payload["capability_version"]),
                effect_class=str(payload["effect_class"]),
                idempotency_key=_optional_str(payload.get("idempotency_key")),
                status=str(payload["status"]),
                expected_entity=_optional_str(payload.get("expected_entity")),
                expected_amount=_optional_money(payload.get("expected_amount")),
                actor=str(payload["actor"]),
                browser_session_id=_optional_str(payload.get("browser_session_id")),
                started_at=_datetime(payload["started_at"]),
            )
            session.add(execution_record)
            executions[source_id] = execution_record
        elif event_type == "CAPABILITY_EXECUTION_COMPLETED":
            source_id = int(payload["execution_id"])
            completed_record = executions.get(source_id)
            if completed_record is None:
                raise LedgerIntegrityError(
                    f"Execution completion {source_id} has no start event"
                )
            completed_record.status = str(payload["status"])
            completed_record.observed_entity = _optional_str(payload.get("observed_entity"))
            completed_record.observed_amount = _optional_money(payload.get("observed_amount"))
            completed_record.failure_category = _optional_str(payload.get("failure_category"))
            completed_record.audit_ref = _optional_str(payload.get("audit_ref"))
            completed_record.money_moved = bool(payload["money_moved"])
            completed_record.completed_at = _datetime(payload["completed_at"])
        elif event_type == "INTENT_STAGED":
            key = str(payload["idempotency_key"])
            intent_record = EffectIntentRecord(
                case_id=case_id,
                idempotency_key=key,
                capability_id=str(payload["capability_id"]),
                intent_status=str(payload["intent_status"]),
                created_at=_datetime(payload["created_at"]),
            )
            session.add(intent_record)
            intents[key] = intent_record
        elif event_type == "INTENT_COMMITTED":
            key = str(payload["idempotency_key"])
            if key not in intents:
                raise LedgerIntegrityError(f"Committed intent {key} has no staged event")
            intents[key].intent_status = "COMMITTED"
        elif event_type == "EFFECT_CLAIMED":
            key = str(payload["idempotency_key"])
            claim_record = claims.get(key)
            values = {
                "institution_id": str(payload["institution_id"]),
                "procedure_id": str(payload["procedure_id"]),
                "case_id": case_id,
                "capability_id": str(payload["capability_id"]),
                "member_id": str(payload["member_id"]),
                "account_id": str(payload["account_id"]),
                "amount": parse_money(payload["amount"]),
                "currency": str(payload["currency"]),
                "business_reference": str(payload["business_reference"]),
                "status": str(payload["status"]),
                "owner_id": str(payload["owner_id"]),
                "fencing_token": int(payload["fencing_token"]),
                "claimed_at": _datetime(payload["claimed_at"]),
                "heartbeat_at": _datetime(payload["heartbeat_at"]),
                "expires_at": _datetime(payload["expires_at"]),
                "updated_at": _datetime(payload["updated_at"]),
            }
            if claim_record is None:
                claim_record = EffectClaimRecord(idempotency_key=key, **values)
                session.add(claim_record)
                claims[key] = claim_record
            else:
                for name, value in values.items():
                    setattr(claim_record, name, value)
        elif event_type == "EFFECT_CLAIM_TRANSITIONED":
            key = str(payload["idempotency_key"])
            if key not in claims:
                raise LedgerIntegrityError(f"Transitioned claim {key} has no claim event")
            claim_record = claims[key]
            claim_record.status = str(payload["status"])
            claim_record.owner_id = str(payload["owner_id"])
            claim_record.fencing_token = int(payload["fencing_token"])
            claim_record.heartbeat_at = _datetime(payload["heartbeat_at"])
            claim_record.updated_at = _datetime(payload["updated_at"])
        elif event_type == "DEADLINE_CREATED":
            deadline_type = str(payload["deadline_type"])
            deadline_record = DeadlineRecord(
                case_id=case_id,
                deadline_type=deadline_type,
                due_at=_datetime(payload["due_at"]),
                status=str(payload["status"]),
            )
            session.add(deadline_record)
            deadlines[deadline_type] = deadline_record
        elif event_type == "DEADLINE_RESOLVED":
            deadline_type = str(payload["deadline_type"])
            if deadline_type not in deadlines:
                raise LedgerIntegrityError(
                    f"Resolved deadline {deadline_type} has no creation event"
                )
            deadlines[deadline_type].status = str(payload["status"])
            deadlines[deadline_type].resolved_at = _datetime(payload["resolved_at"])
        elif event_type == "OBLIGATION_CREATED":
            obligation_type = str(payload["obligation_type"])
            obligation_record = ObligationRecord(
                case_id=case_id,
                obligation_type=obligation_type,
                due_at=_datetime(payload["due_at"]),
                status=str(payload["status"]),
                created_at=_datetime(payload["created_at"]),
            )
            session.add(obligation_record)
            obligations[obligation_type] = obligation_record
        elif event_type == "OBLIGATION_ACTIVATED":
            obligation_type = str(payload["obligation_type"])
            if obligation_type not in obligations:
                raise LedgerIntegrityError(
                    f"Activated obligation {obligation_type} has no creation event"
                )
            obligations[obligation_type].status = str(payload["status"])
            obligations[obligation_type].activated_at = _datetime(payload["activated_at"])
        elif event_type == "OBLIGATION_SATISFIED":
            obligation_type = str(payload["obligation_type"])
            if obligation_type not in obligations:
                raise LedgerIntegrityError(
                    f"Satisfied obligation {obligation_type} has no creation event"
                )
            obligations[obligation_type].status = str(payload["status"])
            obligations[obligation_type].satisfied_at = _datetime(payload["satisfied_at"])
    session.flush()


def snapshot_projections(session: Session, case_id: str) -> dict[str, Any]:
    """Return a deterministic business snapshot for rebuild equivalence tests."""
    case = session.get(ProcedureCaseRecord, case_id)
    if case is None:
        raise ValueError(f"Case {case_id} does not exist")
    executions = session.scalars(
        select(CapabilityExecutionRecord)
        .where(CapabilityExecutionRecord.case_id == case_id)
        .order_by(CapabilityExecutionRecord.id)
    ).all()
    intents = session.scalars(
        select(EffectIntentRecord)
        .where(EffectIntentRecord.case_id == case_id)
        .order_by(EffectIntentRecord.id)
    ).all()
    claims = session.scalars(
        select(EffectClaimRecord)
        .where(EffectClaimRecord.case_id == case_id)
        .order_by(EffectClaimRecord.id)
    ).all()
    deadlines = session.scalars(
        select(DeadlineRecord)
        .where(DeadlineRecord.case_id == case_id)
        .order_by(DeadlineRecord.id)
    ).all()
    obligations = session.scalars(
        select(ObligationRecord)
        .where(ObligationRecord.case_id == case_id)
        .order_by(ObligationRecord.id)
    ).all()
    return {
        "case": {
            "case_id": case.case_id,
            "member_id": case.member_id,
            "procedure_name": case.procedure_name,
            "amount": str(case.amount),
            "currency": case.currency,
            "status": case.status,
            "money_moved": case.money_moved,
        },
        "executions": [
            {
                "capability_id": item.capability_id,
                "capability_version": item.capability_version,
                "effect_class": item.effect_class,
                "idempotency_key": item.idempotency_key,
                "status": item.status,
                "expected_entity": item.expected_entity,
                "observed_entity": item.observed_entity,
                "expected_amount": _money_text(item.expected_amount),
                "observed_amount": _money_text(item.observed_amount),
                "failure_category": item.failure_category,
                "audit_ref": item.audit_ref,
                "money_moved": item.money_moved,
                "actor": item.actor,
                "browser_session_id": item.browser_session_id,
                "started_at": _datetime_text(item.started_at),
                "completed_at": _datetime_text(item.completed_at),
            }
            for item in executions
        ],
        "intents": [
            {
                "idempotency_key": item.idempotency_key,
                "capability_id": item.capability_id,
                "intent_status": item.intent_status,
                "created_at": _datetime_text(item.created_at),
            }
            for item in intents
        ],
        "claims": [
            {
                "idempotency_key": item.idempotency_key,
                "institution_id": item.institution_id,
                "procedure_id": item.procedure_id,
                "capability_id": item.capability_id,
                "member_id": item.member_id,
                "account_id": item.account_id,
                "amount": str(item.amount),
                "currency": item.currency,
                "business_reference": item.business_reference,
                "status": item.status,
                "owner_id": item.owner_id,
                "fencing_token": item.fencing_token,
                "claimed_at": _datetime_text(item.claimed_at),
                "heartbeat_at": _datetime_text(item.heartbeat_at),
                "expires_at": _datetime_text(item.expires_at),
                "updated_at": _datetime_text(item.updated_at),
            }
            for item in claims
        ],
        "deadlines": [
            {
                "deadline_type": item.deadline_type,
                "due_at": _datetime_text(item.due_at),
                "status": item.status,
                "resolved_at": _datetime_text(item.resolved_at),
            }
            for item in deadlines
        ],
        "obligations": [
            {
                "obligation_type": item.obligation_type,
                "due_at": _datetime_text(item.due_at),
                "status": item.status,
                "created_at": _datetime_text(item.created_at),
                "activated_at": _datetime_text(item.activated_at),
                "satisfied_at": _datetime_text(item.satisfied_at),
            }
            for item in obligations
        ],
    }


def _payload(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise LedgerIntegrityError("Event payload is not a JSON object")
    return loaded


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_money(value: object) -> Decimal | None:
    return None if value is None else parse_money(value)


def _money_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
