"""Explicit finite state machine for Regulation E dispute processing."""

from enum import Enum
from typing import Set


class RegEState(str, Enum):
    RECEIVED = "RECEIVED"
    MEMBER_VERIFIED = "MEMBER_VERIFIED"
    TRANSACTION_VERIFIED = "TRANSACTION_VERIFIED"
    DUPLICATE_CHECKED = "DUPLICATE_CHECKED"
    CHARGEBACK_FILED = "CHARGEBACK_FILED"
    PROVISIONAL_CREDIT_POSTED = "PROVISIONAL_CREDIT_POSTED"
    NOTICE_PENDING = "NOTICE_PENDING"
    NOTICE_SENT = "NOTICE_SENT"
    WAITING_RESOLUTION = "WAITING_RESOLUTION"
    RESOLVED = "RESOLVED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    UNCERTAIN_EFFECT = "UNCERTAIN_EFFECT"
    FAILED = "FAILED"


VALID_TRANSITIONS: dict[RegEState, Set[RegEState]] = {
    RegEState.RECEIVED: {RegEState.MEMBER_VERIFIED, RegEState.FAILED, RegEState.NEEDS_HUMAN},
    RegEState.MEMBER_VERIFIED: {
        RegEState.TRANSACTION_VERIFIED,
        RegEState.FAILED,
        RegEState.NEEDS_HUMAN,
    },
    RegEState.TRANSACTION_VERIFIED: {
        RegEState.DUPLICATE_CHECKED,
        RegEState.FAILED,
        RegEState.NEEDS_HUMAN,
    },
    RegEState.DUPLICATE_CHECKED: {
        RegEState.CHARGEBACK_FILED,
        RegEState.PROVISIONAL_CREDIT_POSTED,
        RegEState.RESOLVED,
        RegEState.NEEDS_HUMAN,
        RegEState.FAILED,
    },
    RegEState.CHARGEBACK_FILED: {
        RegEState.PROVISIONAL_CREDIT_POSTED,
        RegEState.NEEDS_HUMAN,
        RegEState.UNCERTAIN_EFFECT,
        RegEState.FAILED,
    },
    RegEState.PROVISIONAL_CREDIT_POSTED: {
        RegEState.NOTICE_PENDING,
        RegEState.NOTICE_SENT,
        RegEState.NEEDS_HUMAN,
        RegEState.UNCERTAIN_EFFECT,
        RegEState.FAILED,
    },
    RegEState.NOTICE_PENDING: {RegEState.NOTICE_SENT, RegEState.NEEDS_HUMAN, RegEState.FAILED},
    RegEState.NOTICE_SENT: {RegEState.WAITING_RESOLUTION, RegEState.RESOLVED},
    RegEState.WAITING_RESOLUTION: {RegEState.RESOLVED, RegEState.NEEDS_HUMAN},
    RegEState.NEEDS_HUMAN: {
        RegEState.MEMBER_VERIFIED,
        RegEState.TRANSACTION_VERIFIED,
        RegEState.DUPLICATE_CHECKED,
        RegEState.CHARGEBACK_FILED,
        RegEState.PROVISIONAL_CREDIT_POSTED,
        RegEState.NOTICE_PENDING,
        RegEState.NOTICE_SENT,
        RegEState.WAITING_RESOLUTION,
        RegEState.RESOLVED,
        RegEState.FAILED,
    },
    RegEState.UNCERTAIN_EFFECT: {RegEState.NEEDS_HUMAN, RegEState.RESOLVED, RegEState.FAILED},
    RegEState.RESOLVED: set(),
    RegEState.FAILED: set(),
}


def can_transition(current: RegEState, target: RegEState) -> bool:
    """Validate whether state machine transition is permitted."""
    if current == target:
        return True
    return target in VALID_TRANSITIONS.get(current, set())
