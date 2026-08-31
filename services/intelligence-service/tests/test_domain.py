"""Domain primitives: statistics, fraud rules, churn bands, maintenance,
remediation safety helpers."""
import pytest

from app.domain import churn, fraud, maintenance, remediation, statistics as stats
from app.domain.exceptions import (ApprovalError, CrossTenantActionError, KillSwitchEngagedError,
                                   RemediationSafetyError)


def test_weighted_logit_sigmoid():
    assert 0.0 < stats.weighted_logit({"a": 1.0}, {"a": 2.0}, intercept=0.0) < 1.0
    assert stats.weighted_logit({"a": 0.0}, {"a": 100.0}, intercept=-10.0) < 0.5


def test_checksum_stable_and_ordered():
    a = stats.checksum({"x": 1, "y": [2, 3]})
    b = stats.checksum({"y": [2, 3], "x": 1})
    assert a == b
    c = stats.checksum({"x": 2, "y": [2, 3]})
    assert a != c


def test_pr_auc_and_auc_roc():
    y = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
    s = [0.9, 0.1, 0.8, 0.7, 0.2, 0.85, 0.3, 0.05, 0.6, 0.75]
    assert 0.0 < stats.pr_auc(y, s) <= 1.0
    assert 0.0 < stats.auc_roc(y, s) <= 1.0


def test_ece():
    y = [1, 1, 0, 0]
    s = [0.9, 0.8, 0.3, 0.2]
    assert stats.expected_calibration_error(y, s) >= 0.0


def test_fraud_eval_condition():
    assert fraud.eval_condition({"x": 5}, {"field": "x", "op": "gt", "value": 4}) is True
    assert fraud.eval_condition({"x": 5}, {"field": "x", "op": "lt", "value": 4}) is False
    assert fraud.eval_condition({"y": "abc"}, {"field": "y", "op": "contains", "value": "b"}) is True
    assert fraud.eval_condition({"x": None}, {"field": "x", "op": "gt", "value": 1}) is False


def test_fraud_score_signal():
    score, factors = fraud.score_signal(rule_hits=2, rule_weights=[0.9, 0.8])
    assert score > 0.0
    assert "2 rule hit(s)" in factors


def test_fraud_never_auto_actions():
    # A high score maps to ESCALATE/REVIEW, never direct suspension.
    assert fraud.decide(0.9, "CRITICAL") == "ESCALATE"
    assert fraud.decide(0.5, "MEDIUM") == "REVIEW"


def test_churn_risk_bands():
    assert churn.risk_band(0.1) == "LOW"
    assert churn.risk_band(0.4) == "MEDIUM"
    assert churn.risk_band(0.6) == "HIGH"
    assert churn.risk_band(0.9) == "CRITICAL"


def test_churn_retention_action():
    assert churn.retention_action("CRITICAL") == "PRIORITY_RETENTION_CALL"
    assert churn.retention_action("LOW") == "MONITOR"


def test_maintenance_bands():
    assert maintenance.maintenance_recommendation(0.8) == "REPLACE"
    assert maintenance.maintenance_recommendation(0.05) == "MONITOR"
    assert maintenance.capacity_risk(0.95, 0.9) == "CRITICAL"


def test_autonomy_policy():
    assert remediation.autonomy_for_action("REQUEST_BANDWIDTH_ADJUSTMENT") == "L2"
    assert remediation.autonomy_for_action("RETRY_TELEMETRY_COLLECTION", "L3") == "L3"
    assert remediation.requires_approval("L2") is True
    assert remediation.requires_approval("L3") is False
    assert remediation.is_executable("L0") is False
    assert remediation.is_executable("L3") is True


def test_kill_switch_raises():
    with pytest.raises(KillSwitchEngagedError):
        remediation.check_kill_switch(True, False)
    with pytest.raises(KillSwitchEngagedError):
        remediation.check_kill_switch(False, True)
    remediation.check_kill_switch(False, False)  # no raise


def test_tenant_scope_enforced():
    with pytest.raises(CrossTenantActionError):
        remediation.check_tenant_scope("tenant-a", "TENANT", "tenant-b")
    remediation.check_tenant_scope("tenant-a", "TENANT", "tenant-a")


def test_budget_and_cooldown():
    with pytest.raises(RemediationSafetyError):
        remediation.check_budget(10, 10)
    with pytest.raises(RemediationSafetyError):
        remediation.check_cooldown(100, 3600, 100 + 60)


def test_circuit_breaker_and_blast_radius():
    with pytest.raises(RemediationSafetyError):
        remediation.check_circuit_breaker(5, 3)
    with pytest.raises(RemediationSafetyError):
        remediation.check_blast_radius(3, 1)


def test_verify_approval():
    with pytest.raises(ApprovalError):
        remediation.verify_approval([], "L2", "t")
    remediation.verify_approval([{"decision": "APPROVED"}], "L2", "t")
    remediation.verify_approval([], "L3", "t")  # no approval required
