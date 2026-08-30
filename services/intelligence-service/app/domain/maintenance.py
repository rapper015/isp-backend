"""Predictive maintenance degradation scoring and capacity risk."""
from __future__ import annotations

from .statistics import clamp


def maintenance_recommendation(failure_probability: float) -> str:
    if failure_probability >= 0.7:
        return "REPLACE"
    if failure_probability >= 0.45:
        return "DIAGNOSE"
    if failure_probability >= 0.25:
        return "INSPECT"
    if failure_probability >= 0.12:
        return "CONFIG_VALIDATE"
    return "MONITOR"


def degradation_band(probability: float) -> str:
    if probability >= 0.6:
        return "HIGH"
    if probability >= 0.3:
        return "MEDIUM"
    if probability >= 0.12:
        return "LOW"
    return "NONE"


def capacity_risk(utilization: float, forecast_peak: float) -> str:
    """Risk band from current utilization + forecasted peak utilization."""
    peak = max(utilization, forecast_peak)
    if peak >= 0.9:
        return "CRITICAL"
    if peak >= 0.8:
        return "HIGH"
    if peak >= 0.7:
        return "MEDIUM"
    return "LOW"


def weighted_failure_score(signals: dict, weights: dict) -> float:
    """Combine normalized signals (0..1) into a failure probability."""
    num = den = 0.0
    for name, weight in (weights or {}).items():
        value = float(signals.get(name, 0.0) or 0.0)
        num += weight * clamp(value)
        den += weight
    return round(num / den if den else 0.0, 4)
