"""Statutory and business-day deadline service for Regulation E disputes (12 CFR 1005.11).

The original implementation computed every deadline as "add N days, skip Saturday/Sunday,
stamp 17:00:00 UTC" with no concept of a US federal holiday, no institution timezone, no
extended (new-account / point-of-sale / foreign-initiated) 20-business-day investigation
and 90-calendar-day resolution timeframe, and no way to tell whether a persisted deadline
had already lapsed. Root cause: the deadline function had no calendar provider, no injected
clock, no institution timezone, and no lifecycle evaluation step.

``DeadlineRuleSet`` fixes this with an injectable timezone, an injectable clock, a real US
federal holiday calendar (including weekend "in lieu of" observance), month/year-boundary
safe business-day arithmetic, and an extended-case flag. ``evaluate_deadlines`` turns a
due-date/now pair into a lifecycle status (``PENDING`` or ``OVERDUE``) so obligations can be
transitioned instead of silently going stale.

``add_business_days`` and ``calculate_reg_e_deadlines`` remain as module-level functions so
existing call sites (``tandem/workflow/reg_e.py`` and prior tests) keep working; they now
delegate to a default ``DeadlineRuleSet`` anchored to the institution's operating timezone
(``America/New_York``) with the federal holiday calendar applied, which is the actual fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Dict, Optional, Set
from zoneinfo import ZoneInfo

DEFAULT_INSTITUTION_TIMEZONE = ZoneInfo("America/New_York")


def _nth_weekday_of_month(year: int, month: int, weekday: int, occurrence: int) -> date:
    """The `occurrence`-th (1-based) `weekday` (Monday=0) of `month`/`year`."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """The last `weekday` (Monday=0) of `month`/`year`."""
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed(holiday: date) -> date:
    """Federal 'in lieu of' rule: a Saturday holiday is observed Friday, Sunday observed Monday."""
    if holiday.weekday() == 5:  # Saturday
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:  # Sunday
        return holiday + timedelta(days=1)
    return holiday


def federal_holidays(year: int) -> Set[date]:
    """US federal holidays observed by financial institutions for a given calendar year."""
    fixed = [
        date(year, 1, 1),  # New Year's Day
        date(year, 6, 19),  # Juneteenth National Independence Day
        date(year, 7, 4),  # Independence Day
        date(year, 11, 11),  # Veterans Day
        date(year, 12, 25),  # Christmas Day
    ]
    floating = [
        _nth_weekday_of_month(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday_of_month(year, 2, 0, 3),  # Washington's Birthday
        _last_weekday_of_month(year, 5, 0),  # Memorial Day
        _nth_weekday_of_month(year, 9, 0, 1),  # Labor Day
        _nth_weekday_of_month(year, 10, 0, 2),  # Columbus Day
        _nth_weekday_of_month(year, 11, 3, 4),  # Thanksgiving Day (Thursday)
    ]
    return {_observed(d) for d in fixed} | set(floating)


@dataclass
class DeadlineRuleSet:
    """Configurable business-day/calendar-day rules for Reg E statutory deadlines."""

    timezone: ZoneInfo = field(default_factory=lambda: DEFAULT_INSTITUTION_TIMEZONE)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    cutoff_hour: int = 17

    def _localize(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(self.timezone)

    def now(self) -> datetime:
        return self._localize(self.clock())

    def is_business_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in federal_holidays(day.year)

    def add_business_days(self, start: datetime, business_days: int) -> datetime:
        """Add N business days (skipping weekends and federal holidays), in `self.timezone`.

        The result is always stamped at `cutoff_hour`:00:00 local time on the final
        business day, matching the institution's end-of-business-day cutoff.
        """
        cursor = self._localize(start).date()
        added = 0
        while added < business_days:
            cursor += timedelta(days=1)
            if self.is_business_day(cursor):
                added += 1
        return datetime.combine(cursor, time(hour=self.cutoff_hour), tzinfo=self.timezone)

    def calculate(
        self,
        opened_at: Optional[datetime] = None,
        extended_case: bool = False,
    ) -> Dict[str, datetime]:
        """Calculate statutory deadlines under 12 CFR 1005.11.

        - 2-day Notice: 2 business days after provisional credit
        - Standard case: 10-business-day investigation/credit deadline, 45-calendar-day
          final resolution deadline
        - Extended case (new account, point-of-sale, or foreign-initiated transaction):
          20-business-day investigation/credit deadline, 90-calendar-day final resolution
        """
        base = self._localize(opened_at) if opened_at is not None else self.now()

        deadlines: Dict[str, datetime] = {
            "NOTICE_2_DAY": self.add_business_days(base, 2),
        }
        if extended_case:
            deadlines["INVESTIGATION_20_DAY"] = self.add_business_days(base, 20)
            deadlines["FINAL_RESOLUTION_90_DAY"] = base + timedelta(days=90)
        else:
            deadlines["INVESTIGATION_10_DAY"] = self.add_business_days(base, 10)
            deadlines["FINAL_RESOLUTION_45_DAY"] = base + timedelta(days=45)
        return deadlines


def evaluate_deadlines(
    deadlines: Dict[str, datetime], now: Optional[datetime] = None
) -> Dict[str, str]:
    """Evaluate each due date against `now` (defaulting to the real clock).

    Returns ``"OVERDUE"`` for any deadline whose due date has passed and ``"PENDING"``
    otherwise. This is the lifecycle evaluation step the original model never performed,
    so a lapsed statutory deadline was indistinguishable from one still on track.
    """
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    statuses: Dict[str, str] = {}
    for deadline_type, due_at in deadlines.items():
        due = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=timezone.utc)
        statuses[deadline_type] = "OVERDUE" if reference >= due else "PENDING"
    return statuses


# Backward-compatible module-level API anchored to the institution's default timezone
# and the real federal holiday calendar. This is the fix: previously these were free
# functions with a hardcoded UTC 17:00 cutoff and no holiday awareness.
_default_rules = DeadlineRuleSet()


def add_business_days(start_date: datetime, business_days: int) -> datetime:
    """Add business days to a timestamp, skipping weekends and US federal holidays."""
    return _default_rules.add_business_days(start_date, business_days)


def calculate_reg_e_deadlines(
    opened_at: Optional[datetime] = None,
    extended_case: bool = False,
) -> Dict[str, datetime]:
    """Calculate statutory deadlines under 12 CFR 1005.11 (see `DeadlineRuleSet.calculate`)."""
    return _default_rules.calculate(opened_at, extended_case=extended_case)
