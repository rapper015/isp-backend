"""Default catalogue seeding: data contracts, feature definitions, fraud rules,
remediation policies, kill-switch, baseline models."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import (DataContract, FeatureDefinition, FraudRule, KillSwitch, MlModel,
                      RemediationPolicy)

CONTRACTS = [
    {"event_name": "crm.customer.created.v1", "producer": "crm-service", "retention_days": 730,
     "required_fields": ["customer_id"], "optional_fields": ["name", "segment"], "pii_fields": ["name"]},
    {"event_name": "crm.customer.activated.v1", "producer": "crm-service", "retention_days": 730,
     "required_fields": ["customer_id"], "optional_fields": ["plan_code"], "pii_fields": []},
    {"event_name": "oss.order.created.v1", "producer": "oss-service", "retention_days": 730,
     "required_fields": ["customer_id"], "optional_fields": ["order_id", "plan_code"], "pii_fields": []},
    {"event_name": "oss.order.activated.v1", "producer": "oss-service", "retention_days": 730,
     "required_fields": ["customer_id"], "optional_fields": ["order_id", "service_id"], "pii_fields": []},
    {"event_name": "billing.invoice.issued.v1", "producer": "bss-service", "retention_days": 730,
     "required_fields": ["customer_id"], "optional_fields": ["invoice_id", "amount", "due_date"], "pii_fields": []},
    {"event_name": "billing.payment.captured.v1", "producer": "bss-service", "retention_days": 730,
     "required_fields": ["customer_id"], "optional_fields": ["payment_id", "amount"], "pii_fields": []},
    {"event_name": "billing.payment.failed.v1", "producer": "bss-service", "retention_days": 730,
     "required_fields": ["customer_id"], "optional_fields": ["payment_id", "reason"], "pii_fields": []},
    {"event_name": "billing.account_delinquent.v1", "producer": "bss-service", "retention_days": 730,
     "required_fields": ["customer_id"], "optional_fields": ["days_overdue"], "pii_fields": []},
    {"event_name": "aaa.session.established.v1", "producer": "aaa-service", "retention_days": 365,
     "required_fields": ["subscriber_id"], "optional_fields": ["session_id", "nas_id", "mac"], "pii_fields": ["mac"]},
    {"event_name": "aaa.session.stale.v1", "producer": "aaa-service", "retention_days": 365,
     "required_fields": ["subscriber_id"], "optional_fields": ["session_id", "reason"], "pii_fields": ["mac"]},
    {"event_name": "aaa.session.terminated.v1", "producer": "aaa-service", "retention_days": 365,
     "required_fields": ["subscriber_id"], "optional_fields": ["session_id", "reason", "reset"], "pii_fields": ["mac"]},
    {"event_name": "nas.health_changed.v1", "producer": "aaa-service", "retention_days": 365,
     "required_fields": ["nas_id"], "optional_fields": ["status", "latency_ms"], "pii_fields": []},
    {"event_name": "network.identity_assigned.v1", "producer": "aaa-service", "retention_days": 365,
     "required_fields": ["subscriber_id"], "optional_fields": ["ip", "vlan"], "pii_fields": ["ip"]},
    {"event_name": "device.cpe.online.v1", "producer": "device-management-service", "retention_days": 730,
     "required_fields": ["cpe_id"], "optional_fields": ["firmware"], "pii_fields": []},
    {"event_name": "device.cpe.offline.v1", "producer": "device-management-service", "retention_days": 730,
     "required_fields": ["cpe_id"], "optional_fields": ["reason"], "pii_fields": []},
    {"event_name": "device.cpe.diagnostics.v1", "producer": "device-management-service", "retention_days": 730,
     "required_fields": ["cpe_id"], "optional_fields": ["latency_ms", "errors"], "pii_fields": []},
    {"event_name": "tenancy.tenant.provisioned.v1", "producer": "tenancy-service", "retention_days": 730,
     "required_fields": ["tenant_id"], "optional_fields": ["tier"], "pii_fields": []},
    {"event_name": "assurance.alert_normalized.v1", "producer": "assurance-service", "retention_days": 365,
     "required_fields": ["service", "alert_name"], "optional_fields": ["severity", "resource"], "pii_fields": []},
    {"event_name": "assurance.incident_created.v1", "producer": "assurance-service", "retention_days": 365,
     "required_fields": ["incident_id"], "optional_fields": ["severity", "is_major"], "pii_fields": []},
    {"event_name": "assurance.customer_impact_detected.v1", "producer": "assurance-service", "retention_days": 365,
     "required_fields": ["incident_id"], "optional_fields": ["impact_kind", "estimated_subscribers"], "pii_fields": []},
]

FEATURES = [
    {"name": "payment_failure_rate", "source_contract": "billing.payment.*", "entity_key": "customer",
     "data_type": "FLOAT", "freshness_seconds": 86400, "pii_class": "NONE", "owner": "BSS"},
    {"name": "recent_payment_failures", "source_contract": "billing.payment.failed.v1", "entity_key": "customer",
     "data_type": "INTEGER", "freshness_seconds": 86400, "pii_class": "NONE", "owner": "BSS"},
    {"name": "auth_failure_rate", "source_contract": "aaa.session.*", "entity_key": "subscriber",
     "data_type": "FLOAT", "freshness_seconds": 3600, "pii_class": "NONE", "owner": "NETWORK"},
    {"name": "session_reset_rate", "source_contract": "aaa.session.*", "entity_key": "subscriber",
     "data_type": "FLOAT", "freshness_seconds": 3600, "pii_class": "NONE", "owner": "NETWORK"},
    {"name": "concurrent_session_count", "source_contract": "aaa.session.*", "entity_key": "subscriber",
     "data_type": "FLOAT", "freshness_seconds": 3600, "pii_class": "NONE", "owner": "NETWORK"},
    {"name": "mac_churn_count", "source_contract": "aaa.session.*", "entity_key": "subscriber",
     "data_type": "INTEGER", "freshness_seconds": 86400, "pii_class": "SENSITIVE", "owner": "NETWORK"},
    {"name": "usage_gb", "source_contract": "aaa.session.*", "entity_key": "subscriber",
     "data_type": "FLOAT", "freshness_seconds": 3600, "pii_class": "NONE", "owner": "BSS"},
    {"name": "usage_vs_plan_ratio", "source_contract": "aaa.session.*", "entity_key": "subscriber",
     "data_type": "FLOAT", "freshness_seconds": 3600, "pii_class": "NONE", "owner": "BSS"},
    {"name": "support_ticket_count", "source_contract": "ticket.created.v1", "entity_key": "customer",
     "data_type": "INTEGER", "freshness_seconds": 86400, "pii_class": "NONE", "owner": "SUPPORT"},
    {"name": "sla_breach_count", "source_contract": "ticket.created.v1", "entity_key": "customer",
     "data_type": "INTEGER", "freshness_seconds": 86400, "pii_class": "NONE", "owner": "SUPPORT"},
    {"name": "outage_exposure_count", "source_contract": "assurance.customer_impact_detected.v1", "entity_key": "customer",
     "data_type": "INTEGER", "freshness_seconds": 86400, "pii_class": "NONE", "owner": "OBSERVABILITY"},
    {"name": "device_offline_ratio", "source_contract": "device.cpe.*", "entity_key": "cpe",
     "data_type": "FLOAT", "freshness_seconds": 3600, "pii_class": "NONE", "owner": "DEVICE"},
    {"name": "latency_avg_ms", "source_contract": "nas.health_changed.v1", "entity_key": "nas",
     "data_type": "FLOAT", "freshness_seconds": 3600, "pii_class": "NONE", "owner": "NETWORK"},
    {"name": "error_rate", "source_contract": "nas.health_changed.v1", "entity_key": "nas",
     "data_type": "FLOAT", "freshness_seconds": 3600, "pii_class": "NONE", "owner": "NETWORK"},
    {"name": "tenure_days", "source_contract": "crm.customer.created.v1", "entity_key": "customer",
     "data_type": "FLOAT", "freshness_seconds": 86400, "pii_class": "NONE", "owner": "CRM"},
]

FRAUD_RULES = [
    {"code": "fraud_repeated_auth_failures", "name": "Repeated auth failures",
     "condition": {"field": "auth_failure_rate", "op": "gte", "value": 0.8},
     "severity": "HIGH", "risk_weight": 0.9},
    {"code": "fraud_concurrent_sessions", "name": "Improbable concurrent sessions",
     "condition": {"field": "concurrent_session_count", "op": "gte", "value": 5},
     "severity": "HIGH", "risk_weight": 0.85},
    {"code": "fraud_mac_churn", "name": "Rapid MAC churn",
     "condition": {"field": "mac_churn_count", "op": "gte", "value": 3},
     "severity": "MEDIUM", "risk_weight": 0.6},
    {"code": "fraud_payment_failures", "name": "Repeated payment failures",
     "condition": {"field": "recent_payment_failures", "op": "gte", "value": 3},
     "severity": "MEDIUM", "risk_weight": 0.7},
    {"code": "fraud_usage_spike", "name": "Usage spike vs plan",
     "condition": {"field": "usage_vs_plan_ratio", "op": "gte", "value": 3.0},
     "severity": "LOW", "risk_weight": 0.5},
]

REMEDIATION_POLICIES = [
    {"code": "retry_telemetry_collection", "action_type": "RETRY_TELEMETRY_COLLECTION",
     "autonomy_level": "L3", "approval_required": False, "action_budget": 20, "rate_limit_per_hour": 5,
     "cooldown_seconds": 600, "max_blast_radius": 1, "tenant_scope": "TENANT",
     "preconditions": [], "timeout_seconds": 60, "reversible": True,
     "circuit_breaker": {"threshold": 3, "window": 3600}, "retry_policy": {"max_attempts": 2}},
    {"code": "refresh_analytical_cache", "action_type": "REFRESH_CACHE",
     "autonomy_level": "L3", "approval_required": False, "action_budget": 50, "rate_limit_per_hour": 20,
     "cooldown_seconds": 300, "max_blast_radius": 10, "tenant_scope": "TENANT",
     "preconditions": [], "timeout_seconds": 30, "reversible": True,
     "circuit_breaker": {"threshold": 5, "window": 3600}, "retry_policy": {"max_attempts": 2}},
    {"code": "rerun_readonly_diagnostic", "action_type": "RERUN_READONLY_DIAGNOSTIC",
     "autonomy_level": "L3", "approval_required": False, "action_budget": 20, "rate_limit_per_hour": 5,
     "cooldown_seconds": 900, "max_blast_radius": 1, "tenant_scope": "TENANT",
     "preconditions": [{"field": "device_reachable", "op": "eq", "value": True}],
     "timeout_seconds": 120, "reversible": True,
     "circuit_breaker": {"threshold": 3, "window": 3600}, "retry_policy": {"max_attempts": 2}},
    {"code": "request_bandwidth_adjustment", "action_type": "REQUEST_BANDWIDTH_ADJUSTMENT",
     "autonomy_level": "L2", "approval_required": True, "action_budget": 10, "rate_limit_per_hour": 2,
     "cooldown_seconds": 3600, "max_blast_radius": 1, "tenant_scope": "TENANT",
     "preconditions": [], "timeout_seconds": 300, "reversible": True,
     "circuit_breaker": {"threshold": 2, "window": 3600}, "retry_policy": {"max_attempts": 1}},
    {"code": "recommend_ont_replacement", "action_type": "RECOMMEND_ONT_REPLACEMENT",
     "autonomy_level": "L1", "approval_required": False, "action_budget": 100, "rate_limit_per_hour": 100,
     "cooldown_seconds": 60, "max_blast_radius": 1, "tenant_scope": "TENANT",
     "preconditions": [], "timeout_seconds": 30, "reversible": True,
     "circuit_breaker": {"threshold": 10, "window": 3600}, "retry_policy": {"max_attempts": 1}},
]

BASELINE_MODELS = [
    {"model_code": "churn_baseline_30d", "name": "Churn baseline (30d)", "use_case": "CHURN",
     "algorithm": "WEIGHTED_LOGIT", "parameters": {"intercept": -1.2, "weights": {
         "recent_payment_failures": 0.5, "support_ticket_count": 0.3, "outage_exposure_count": 0.4,
         "session_reset_rate": 0.6, "tenure_days": -0.01}},
     "feature_names": ["recent_payment_failures", "support_ticket_count", "outage_exposure_count",
                       "session_reset_rate", "tenure_days"],
     "decision_threshold": 0.5, "applicable_scope": "GLOBAL_BASELINE"},
    {"model_code": "fraud_baseline", "name": "Fraud baseline (hybrid rules)", "use_case": "FRAUD",
     "algorithm": "RULE_ENGINE", "parameters": {"weights": {
         "fraud_repeated_auth_failures": 0.9, "fraud_concurrent_sessions": 0.85,
         "fraud_mac_churn": 0.6, "fraud_payment_failures": 0.7, "fraud_usage_spike": 0.5}},
     "feature_names": [], "decision_threshold": 0.5, "applicable_scope": "GLOBAL_BASELINE"},
    {"model_code": "maintenance_baseline", "name": "Maintenance baseline", "use_case": "MAINTENANCE",
     "algorithm": "WEIGHTED_FAILURE", "parameters": {"weights": {
         "error_rate": 0.5, "latency_avg_ms": 0.3, "device_offline_ratio": 0.7}},
     "feature_names": ["error_rate", "latency_avg_ms", "device_offline_ratio"],
     "decision_threshold": 0.3, "applicable_scope": "GLOBAL_BASELINE"},
]


def ensure_defaults(session: Session) -> None:
    contracts = {c.event_name for c in session.query(DataContract).all()}
    for entry in CONTRACTS:
        if entry["event_name"] not in contracts:
            session.add(DataContract(state="ACTIVE", **entry))
    session.flush()
    features = {(f.name, f.version) for f in session.query(FeatureDefinition).all()}
    for entry in FEATURES:
        if (entry["name"], "v1") in features:
            continue
        row_entry = dict(entry)
        row_entry["domain_owner"] = row_entry.pop("owner", None)
        session.add(FeatureDefinition(version="v1", transformation_version="v1",
                                      missing_behavior="DEFAULT", availability="TRAINING_AND_SERVING",
                                      valid_range={"min": 0.0, "max": 100000.0}, **row_entry))
    session.flush()
    rules = {(r.code, r.version) for r in session.query(FraudRule).all()}
    for entry in FRAUD_RULES:
        if (entry["code"], "v1") not in rules:
            session.add(FraudRule(version="v1", is_active=True, **entry))
    session.flush()
    policies = {p.code for p in session.query(RemediationPolicy).all()}
    for entry in REMEDIATION_POLICIES:
        if entry["code"] not in policies:
            session.add(RemediationPolicy(enabled=True, **entry))
    session.flush()
    models = {(m.model_code, m.version) for m in session.query(MlModel).all()}
    for entry in BASELINE_MODELS:
        if (entry["model_code"], 1) in models:
            continue
        session.add(MlModel(version=1, state="PRODUCTION", approval_status="APPROVED",
                            deployment_status="PRODUCTION", evaluation_metrics={}, baseline_metrics={},
                            calibration={}, training_window={}, artifact={}, owner="platform",
                            explainability_method="WEIGHTS", **entry))
    session.flush()
    if session.query(KillSwitch).filter(KillSwitch.scope == "GLOBAL").count() == 0:
        from datetime import datetime, timezone
        session.add(KillSwitch(scope="GLOBAL", tenant_id=None, enabled=False,
                               reason="initial state", set_by="system",
                               set_at=datetime.now(timezone.utc)))
    session.flush()
