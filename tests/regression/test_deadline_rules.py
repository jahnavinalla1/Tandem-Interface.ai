"""Deterministic deadline-rule regressions for configured Reg E simulation."""

from datetime import datetime
from zoneinfo import ZoneInfo


def _deadline_api():
    from tandem.workflow.deadlines import DeadlineRuleSet, evaluate_deadlines

    return DeadlineRuleSet, evaluate_deadlines


def _eastern() -> ZoneInfo:
    return ZoneInfo("America/New_York")


def test_business_deadline_skips_weekend_and_federal_holiday() -> None:
    DeadlineRuleSet, _ = _deadline_api()
    EASTERN = _eastern()
    rules = DeadlineRuleSet(timezone=EASTERN)
    opened = datetime(2026, 9, 4, 10, 0, tzinfo=EASTERN)  # Friday before Labor Day
    assert rules.add_business_days(opened, 1) == datetime(2026, 9, 8, 17, 0, tzinfo=EASTERN)


def test_business_deadline_crosses_month_boundary() -> None:
    DeadlineRuleSet, _ = _deadline_api()
    EASTERN = _eastern()
    rules = DeadlineRuleSet(timezone=EASTERN)
    opened = datetime(2026, 4, 30, 10, 0, tzinfo=EASTERN)
    assert rules.add_business_days(opened, 1) == datetime(2026, 5, 1, 17, 0, tzinfo=EASTERN)


def test_business_deadline_crosses_year_and_new_year_holiday() -> None:
    DeadlineRuleSet, _ = _deadline_api()
    EASTERN = _eastern()
    rules = DeadlineRuleSet(timezone=EASTERN)
    opened = datetime(2026, 12, 31, 10, 0, tzinfo=EASTERN)
    assert rules.add_business_days(opened, 1) == datetime(2027, 1, 4, 17, 0, tzinfo=EASTERN)


def test_extended_case_includes_90_calendar_day_resolution() -> None:
    DeadlineRuleSet, _ = _deadline_api()
    EASTERN = _eastern()
    rules = DeadlineRuleSet(timezone=EASTERN)
    opened = datetime(2026, 9, 11, 9, 0, tzinfo=EASTERN)
    deadlines = rules.calculate(opened, extended_case=True)
    assert "FINAL_RESOLUTION_90_DAY" in deadlines
    assert "FINAL_RESOLUTION_45_DAY" not in deadlines


def test_injected_clock_marks_past_due_obligation_overdue() -> None:
    _, evaluate_deadlines = _deadline_api()
    EASTERN = _eastern()
    due = datetime(2026, 9, 10, 17, 0, tzinfo=EASTERN)
    now = datetime(2026, 9, 11, 9, 0, tzinfo=EASTERN)
    assert evaluate_deadlines({"NOTICE_2_DAY": due}, now=now) == {"NOTICE_2_DAY": "OVERDUE"}
