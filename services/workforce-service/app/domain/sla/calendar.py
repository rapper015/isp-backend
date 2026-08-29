"""Business calendar arithmetic for field SLA timers.

Field SLA working time is measured in *business seconds* against a business
calendar: working hours per weekday, holidays, timezone. Pure and deterministic;
the database stores the resulting deadlines."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ...models import Holiday

_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_WORKING_DAYS = 730


def _as_tz(dt: datetime, tz) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(tz)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc)


def load_holidays(session: Session, calendar_id) -> set[date]:
    return {row.holiday_date for row in session.query(Holiday).filter(Holiday.calendar_id == calendar_id).all()}


def working_intervals(working_hours: dict, day: date, tz) -> list[tuple[datetime, datetime]]:
    key = _DAY_KEYS[day.weekday()]
    slots = working_hours.get(key) or []
    intervals = []
    for start_s, end_s in slots:
        start_dt = datetime.combine(day, time.fromisoformat(start_s), tzinfo=tz)
        if end_s == "24:00":
            end_dt = datetime.combine(day, time(23, 59, 59, 999999), tzinfo=tz)
        else:
            end_dt = datetime.combine(day, time.fromisoformat(end_s), tzinfo=tz)
        if end_dt > start_dt:
            intervals.append((start_dt, end_dt))
    return intervals


def business_seconds_between(
    session: Session,
    calendar_id,
    working_hours: dict,
    tz,
    holidays: set[date],
    start: datetime,
    end: datetime,
) -> int:
    start = _as_tz(start, tz)
    end = _as_tz(end, tz)
    if end <= start:
        return 0
    total = 0
    day = start.date()
    last = end.date()
    guard = 0
    while day <= last and guard <= MAX_WORKING_DAYS:
        guard += 1
        if day not in holidays:
            for s, e in working_intervals(working_hours, day, tz):
                clip_start = max(s, start)
                clip_end = min(e, end)
                if clip_end > clip_start:
                    total += int((clip_end - clip_start).total_seconds())
        day += timedelta(days=1)
    return total


def deadline_after(
    session: Session,
    calendar_id,
    working_hours: dict,
    tz,
    holidays: set[date],
    start: datetime,
    business_seconds: int,
) -> datetime:
    if business_seconds <= 0:
        return _utc(start)
    start = _as_tz(start, tz)
    remaining = business_seconds
    day = start.date()
    guard = 0
    while guard <= MAX_WORKING_DAYS:
        guard += 1
        if day not in holidays:
            for s, e in working_intervals(working_hours, day, tz):
                if e <= start:
                    continue
                window_start = max(s, start)
                if remaining <= 0:
                    return _utc(start)
                window_seconds = int((e - window_start).total_seconds())
                if window_seconds <= 0:
                    continue
                if remaining <= window_seconds:
                    return _utc(window_start + timedelta(seconds=remaining))
                remaining -= window_seconds
            start = datetime.combine(day, time(0, 0), tzinfo=tz)
        day += timedelta(days=1)
    return _utc(_as_tz(start, tz) + timedelta(seconds=business_seconds))


def default_working_hours() -> dict:
    return {
        "mon": [["09:00", "18:00"]],
        "tue": [["09:00", "18:00"]],
        "wed": [["09:00", "18:00"]],
        "thu": [["09:00", "18:00"]],
        "fri": [["09:00", "18:00"]],
        "sat": [["10:00", "14:00"]],
        "sun": [],
    }
