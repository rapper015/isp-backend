"""Pure-Python statistical primitives for the intelligence layer.

Deliberately dependency-free (no numpy/sklearn). Provides the baseline
statistical models the registry compares against, plus scoring/forecast/
calibration helpers used by inference.
"""
from __future__ import annotations

import hashlib
import json
import math


def checksum(value) -> str:
    """Stable SHA-256 checksum of a JSON-serializable structure (artifacts)."""
    raw = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def sigmoid(value: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-value))
    except OverflowError:
        return 1.0 if value > 0 else 0.0


def weighted_logit(features: dict, weights: dict, intercept: float = 0.0) -> float:
    """Simple logistic-style score: intercept + sum(w_i * x_i), sigmoid-squashed.

    Weights map feature name -> coefficient. Missing features use 0.0
    (models define their own missing-value handling upstream).
    """
    score = intercept
    for name, weight in (weights or {}).items():
        score += weight * float(features.get(name, 0.0) or 0.0)
    return sigmoid(score)


def zscore(value: float, mean: float, std: float) -> float:
    if std is None or std <= 1e-9:
        return 0.0
    return (value - mean) / std


def ewma(series: list[float], alpha: float = 0.3) -> list[float]:
    """Exponential weighted moving average (for trend / anomaly baselines)."""
    out = []
    prev = None
    for v in series:
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def moving_average_forecast(series: list[float], horizon: int, window: int = 7) -> list[float]:
    """Simple horizon forecast = last observed EMA level held constant (baseline)."""
    if not series:
        return [0.0] * horizon
    ema = ewma(series, alpha=0.3)
    level = ema[-1]
    return [level] * horizon


def linear_forecast(series: list[float], horizon: int) -> list[float]:
    """Least-squares line forecast (baseline with trend)."""
    n = len(series)
    if n < 2:
        return moving_average_forecast(series, horizon)
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(series) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, series))
    den = sum((x - x_mean) ** 2 for x in xs)
    slope = num / den if den else 0.0
    intercept = y_mean - slope * x_mean
    return [intercept + slope * (n + i) for i in range(horizon)]


def confidence_interval(points: list[float], z: float = 1.96) -> dict:
    """Approximate prediction interval around the mean of forecast points."""
    n = len(points)
    if n == 0:
        return {"lower": 0.0, "upper": 0.0, "mean": 0.0}
    mean = sum(points) / n
    var = sum((p - mean) ** 2 for p in points) / max(n - 1, 1)
    std = math.sqrt(var)
    return {"lower": mean - z * std, "upper": mean + z * std, "mean": mean}


def precision_recall_at_threshold(y_true: list[int], y_score: list[float],
                                  threshold: float) -> dict:
    tp = fp = fn = 0
    for y, s in zip(y_true, y_score):
        pred = 1 if s >= threshold else 0
        if pred == 1 and y == 1:
            tp += 1
        elif pred == 1 and y == 0:
            fp += 1
        elif pred == 0 and y == 1:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / max(fp + (sum(y_true) == 0) * (fp + 1), 1)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 4),
            "recall": round(recall, 4), "fpr": round(fpr, 4)}


def pr_auc(y_true: list[int], y_score: list[float], steps: int = 20) -> float:
    """Approximate PR-AUC via trapezoidal integration over thresholds."""
    pairs = sorted(zip(y_score, y_true), key=lambda p: -p[0])
    total_pos = max(sum(y_true), 1)
    score, prev_recall, prev_prec = 0.0, 0.0, 1.0
    tp = 0
    for i, (s, y) in enumerate(pairs):
        if y == 1:
            tp += 1
        precision = tp / (i + 1)
        recall = tp / total_pos
        score += (recall - prev_recall) * (prev_prec + precision) / 2
        prev_recall, prev_prec = recall, precision
    return round(score, 4)


def auc_roc(y_true: list[int], y_score: list[float], steps: int = 20) -> float:
    """Approximate ROC-AUC via rank method."""
    pairs = sorted(zip(y_score, [int(y) for y in y_true]), key=lambda p: p[0])
    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    rank_sum = 0.0
    for i, (_, y) in enumerate(pairs):
        if y == 1:
            rank_sum += i + 1
    return round((rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg), 4)


def expected_calibration_error(y_true: list[int], y_score: list[float], bins: int = 10) -> float:
    """Binned ECE for calibration monitoring."""
    if not y_true:
        return 0.0
    edges = [i / bins for i in range(bins + 1)]
    ece = 0.0
    n = len(y_true)
    for b in range(bins):
        lo, hi = edges[b], edges[b + 1]
        mask = [i for i, s in enumerate(y_score) if lo <= s < hi]
        if not mask:
            continue
        conf = sum(y_score[i] for i in mask) / len(mask)
        acc = sum(y_true[i] for i in mask) / len(mask)
        ece += (len(mask) / n) * abs(conf - acc)
    return round(ece, 4)


def pstability(series: list[float]) -> float:
    """Prediction-stability proxy: 1 - normalized std of recent scores."""
    if len(series) < 2:
        return 1.0
    mean = sum(series) / len(series)
    var = sum((v - mean) ** 2 for v in series) / (len(series) - 1)
    std = math.sqrt(var)
    return round(max(0.0, 1.0 - std / max(abs(mean), 1e-9)), 4)
