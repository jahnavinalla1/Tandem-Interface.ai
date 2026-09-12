"""Unit tests for business-day calculations and state machine transitions."""

from datetime import datetime, timezone

from tandem.workflow.deadlines import add_business_days, calculate_reg_e_deadlines
from tandem.workflow.state_machine import RegEState, can_transition


def test_business_days_within_same_week():
    # Wednesday 2026-09-02 10:00 UTC
    wednesday = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
    friday_due = add_business_days(wednesday, 2)
    assert friday_due.year == 2026
    assert friday_due.month == 9
    assert friday_due.day == 4
    assert friday_due.weekday() == 4  # Friday
    assert friday_due.hour == 17  # 5:00 PM cutoff


def test_business_days_skips_weekend_and_federal_holiday():
    # Friday 2026-09-04 14:00 UTC (Labor Day, Monday Sep 7 2026, is a federal holiday)
    friday = datetime(2026, 9, 4, 14, 0, 0, tzinfo=timezone.utc)
    wednesday_due = add_business_days(friday, 2)
    # Skipping Saturday (Sep 5), Sunday (Sep 6), and Labor Day (Mon Sep 7):
    # Day 1: Tuesday (Sep 8), Day 2: Wednesday (Sep 9)
    assert wednesday_due.year == 2026
    assert wednesday_due.month == 9
    assert wednesday_due.day == 9
    assert wednesday_due.weekday() == 2  # Wednesday
    assert wednesday_due.hour == 17


def test_calculate_reg_e_statutory_deadlines():
    start = datetime(2026, 9, 2, 9, 0, 0, tzinfo=timezone.utc)
    deadlines = calculate_reg_e_deadlines(start)

    assert "NOTICE_2_DAY" in deadlines
    assert "INVESTIGATION_10_DAY" in deadlines
    assert "FINAL_RESOLUTION_45_DAY" in deadlines

    # 10 business days from Wednesday Sep 2, skipping the weekend AND Labor Day (Mon Sep 7):
    # Sep 3(Th), Sep 4(Fr), Sep 8(Tu), Sep 9(We), Sep 10(Th),
    # Sep 11(Fr), Sep 14(Mo), Sep 15(Tu), Sep 16(We), Sep 17(Th)
    assert deadlines["INVESTIGATION_10_DAY"].day == 17
    assert deadlines["INVESTIGATION_10_DAY"].month == 9


def test_state_machine_transitions():
    assert can_transition(RegEState.RECEIVED, RegEState.MEMBER_VERIFIED) is True
    assert can_transition(RegEState.MEMBER_VERIFIED, RegEState.TRANSACTION_VERIFIED) is True
    assert can_transition(RegEState.DUPLICATE_CHECKED, RegEState.CHARGEBACK_FILED) is True
    assert can_transition(RegEState.PROVISIONAL_CREDIT_POSTED, RegEState.NOTICE_PENDING) is True

    # Illegal jump: RECEIVED directly to RESOLVED without verification
    assert can_transition(RegEState.RECEIVED, RegEState.RESOLVED) is False
