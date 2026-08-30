"""Fraud detection domain rules and signal scoring.

Hybrid: deterministic rules produce signals; an optional ML score can be fused.
A model score never auto-suspends service — it only creates signals and cases
that flow through review.
"""
from __future__ import annotations

from .exceptions import ContractError

# Rule condition operators evaluated against a normalized analytical record.
_OPS = {
    "gt": lambda v, t: v > t,
    "gte": lambda v, t: v >= t,
    "lt": lambda v, t: v < t,
    "lte": lambda v, t: v <= t,
    "eq": lambda v, t: v == t,
    "neq": lambda v, t: v != t,
    "in": lambda v, t: v in t,
    "contains": lambda v, t: t in str(v),
}


def eval_condition(record: dict, condition: dict) -> bool:
    """Evaluate a rule condition: {"field": "...", "op": "gt", "value": X}
    Optionally {"all": [...]} or {"any": [...]} for composition."""
    if "all" in condition:
        return all(eval_condition(record, c) for c in condition["all"])
    if "any" in condition:
        return any(eval_condition(record, c) for c in condition["any"])
    field = condition.get("field")
    op = condition.get("op", "gt")
    target = condition.get("value")
    if field is None:
        raise ContractError("rule condition missing field")
    value = record.get(field)
    if value is None:
        return False
    op_fn = _OPS.get(op)
    if op_fn is None:
        raise ContractError(f"unknown rule operator {op!r}")
    try:
        return bool(op_fn(value, target))
    except (TypeError, ValueError):
        return False


def score_signal(*, rule_hits: int, rule_weights: list[float], model_score: float | None = None,
                 model_weight: float = 0.0) -> tuple[float, list[str]]:
    """Combine deterministic rule hits with an optional ML score.

    Returns (risk_score 0..1, factors). Without a model, score is driven by
    rule hits and their weights.
    """
    factors = []
    if rule_hits > 0:
        base = min(1.0, sum(rule_weights) / max(len(rule_weights), 1) * (0.5 + 0.1 * rule_hits))
        factors.append(f"{rule_hits} rule hit(s)")
    else:
        base = 0.0
    if model_score is not None:
        base = (1 - model_weight) * base + model_weight * model_score
        factors.append(f"model_score={round(model_score, 3)}")
    return round(min(1.0, base), 4), factors


def severity_for(score: float) -> str:
    if score >= 0.85:
        return "CRITICAL"
    if score >= 0.65:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"


def decide(score: float, severity: str) -> str:
    """Deterministic first-pass decision for a fraud case (never auto-action)."""
    if score >= 0.8 or severity == "CRITICAL":
        return "ESCALATE"
    if score >= 0.5:
        return "REVIEW"
    return "MONITOR"
