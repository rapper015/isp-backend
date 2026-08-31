"""Churn risk bands, drivers and retention-action mapping."""
from __future__ import annotations

BAND_EDGES = {
    "LOW": (0.0, 0.3),
    "MEDIUM": (0.3, 0.55),
    "HIGH": (0.55, 0.75),
    "CRITICAL": (0.75, 1.01),
}


def risk_band(score: float) -> str:
    for band, (lo, hi) in BAND_EDGES.items():
        if lo <= score < hi:
            return band
    return "CRITICAL"


def top_drivers(drivers: dict, limit: int = 5) -> list[dict]:
    """Sort driver contributions desc and return top-N with context."""
    items = [{"feature": k, "contribution": round(float(v), 4)} for k, v in (drivers or {}).items()]
    items.sort(key=lambda d: -abs(d["contribution"]))
    return items[:limit]


def retention_action(band: str, drivers: dict | None = None) -> str:
    """Map a risk band to a CRM retention action (never auto-executed)."""
    if band == "CRITICAL":
        return "PRIORITY_RETENTION_CALL"
    if band == "HIGH":
        return "OFFER_RETENTION_PLAN"
    if band == "MEDIUM":
        return "PROACTIVE_OUTREACH"
    return "MONITOR"
