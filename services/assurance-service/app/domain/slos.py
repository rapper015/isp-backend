"""SLO window and error-budget calculations (Milestone 9 §23–25).

Deterministic and reproducible: every calculation uses the window inputs that
were recorded (good/total, objective, window type/size) plus the policy
version. Maintenance exclusions never alter raw measurements — they only
exclude events from the contractual window."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class SloWindow:
    window_type: str  # ROLLING | CALENDAR
    window_seconds: int
    objective: float  # 0..1
    policy_version: int


@dataclass
class ErrorBudgetResult:
    sli_ratio: float
    good_events: int
    total_events: int
    objective: float
    allowed_bad: float
    consumed_bad: float
    remaining_budget: float
    burn_rate: float
    projected_exhaustion_hours: float | None
    status: str
    fast_burn: bool
    slow_burn: bool


def _status_for(remaining: float, burn_rate: float) -> str:
    if remaining <= 0:
        return "EXHAUSTED"
    if remaining < 0.10:
        return "BREACHED"
    if remaining < 0.25:
        return "AT_RISK"
    if burn_rate >= 5.0:
        return "AT_RISK"
    if burn_rate >= 1.0:
        return "WARNING"
    return "HEALTHY"


def calculate_error_budget(*, good: int, total: int, objective: float,
                           window_seconds: int, policy_version: int,
                           window_type: str = "ROLLING") -> ErrorBudgetResult:
    """Compute SLI ratio, allowed/consumed bad events, remaining budget and burn
    rate for a window. Burn rate = consumed_budget / elapsed_budget (1.0 = using
    budget exactly at objective pace)."""
    if objective <= 0 or objective > 1:
        raise ValueError("objective must be in (0, 1]")
    if total <= 0:
        return ErrorBudgetResult(sli_ratio=1.0, good_events=0, total_events=0, objective=objective,
                                 allowed_bad=0.0, consumed_bad=0.0, remaining_budget=1.0, burn_rate=0.0,
                                 projected_exhaustion_hours=None, status="HEALTHY",
                                 fast_burn=False, slow_burn=False)
    sli_ratio = good / total
    allowed_bad = max(0.0, (1.0 - objective) * total)
    consumed_bad = max(0, total - good)
    # Remaining budget as a fraction of the whole window's allowance.
    remaining = max(0.0, (allowed_bad - consumed_bad) / max(allowed_bad, 1e-9))
    # Burn rate relative to the allowed consumption pace.
    allowed_pace = allowed_bad / window_seconds if window_seconds > 0 else 1.0
    burn_rate = round((consumed_bad / max(allowed_bad, 1e-9)), 3) if allowed_bad > 0 else 0.0
    projected_exhaustion = None
    if burn_rate > 0 and remaining > 0:
        elapsed = window_seconds
        projected_exhaustion = elapsed / burn_rate if burn_rate > 0 else None
    return ErrorBudgetResult(
        sli_ratio=round(sli_ratio, 5), good_events=good, total_events=total, objective=objective,
        allowed_bad=allowed_bad, consumed_bad=consumed_bad, remaining_budget=round(remaining, 5),
        burn_rate=burn_rate, projected_exhaustion_hours=projected_exhaustion,
        status=_status_for(remaining, burn_rate),
        fast_burn=burn_rate >= 14.4,   # budget exhausted in < 5 days of a 30d window
        slow_burn=1.0 <= burn_rate < 14.4,
    )


def window_bounds(now: datetime | None, *, window_type: str, window_seconds: int) -> tuple[datetime, datetime]:
    """Return (start, end) for the window containing `now`."""
    now = now or datetime.now(timezone.utc)
    if window_type == "CALENDAR":
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        if start.month == 12:
            end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        return start, end
    end = now
    start = end.fromtimestamp(now.timestamp() - window_seconds)
    return start, end


def with_maintenance_excluded(good: int, total: int, excluded_good: int, excluded_total: int) -> tuple[int, int]:
    """Exclude approved maintenance events from the contractual window, but keep
    raw measurements intact (callers store both)."""
    return max(0, good - excluded_good), max(0, total - excluded_total)
