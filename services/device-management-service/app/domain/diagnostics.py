"""Diagnostic result normalization and evaluation. Timeouts caused by an
offline device are distinguished from a completed failed test."""
from __future__ import annotations

from ..enums import DIAGNOSTIC_EVALUATIONS


def normalize_diagnostic_result(diagnostic_type: str, raw: dict | None) -> dict:
    raw = raw or {}
    if diagnostic_type == "PING":
        return {
            "success": bool(raw.get("success")),
            "packet_loss_percent": raw.get("packet_loss_percent"),
            "average_rtt_ms": raw.get("average_rtt_ms"),
            "host": raw.get("host"),
        }
    if diagnostic_type in ("UPTIME",):
        return {"uptime_seconds": raw.get("uptime_seconds")}
    if diagnostic_type in ("CPU", "MEMORY"):
        return {"percent": raw.get("percent")}
    if diagnostic_type in ("OPTICAL_RX_TX",):
        return {"rx_dbm": raw.get("rx_dbm"), "tx_dbm": raw.get("tx_dbm")}
    if diagnostic_type == "WAN_STATUS":
        return {"status": raw.get("status"), "wan_ip": raw.get("wan_ip")}
    if diagnostic_type == "CONNECTED_HOSTS":
        return {"hosts": raw.get("hosts", [])}
    return raw


def evaluate_diagnostic(diagnostic_type: str, normalized: dict, *, thresholds: dict | None = None) -> str:
    thresholds = thresholds or {}
    if diagnostic_type == "PING":
        if normalized.get("success") is False:
            return "FAIL"
        packet_loss = normalized.get("packet_loss_percent")
        if packet_loss is not None and packet_loss > (thresholds.get("max_packet_loss", 20)):
            return "WARN"
        return "PASS"
    if diagnostic_type in ("CPU", "MEMORY"):
        percent = normalized.get("percent")
        if percent is None:
            return "UNKNOWN"
        if percent > (thresholds.get("high_watermark", 90)):
            return "FAIL"
        if percent > (thresholds.get("warn_watermark", 75)):
            return "WARN"
        return "PASS"
    if diagnostic_type == "OPTICAL_RX_TX":
        rx = normalized.get("rx_dbm")
        if rx is None:
            return "UNKNOWN"
        if not (-30 <= rx <= 0):
            return "WARN"
        return "PASS"
    return "UNKNOWN"


def is_valid_evaluation(value: str) -> bool:
    return value in DIAGNOSTIC_EVALUATIONS
