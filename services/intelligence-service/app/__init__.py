"""Intelligence Service — Milestone 10 (AI & Intelligence Layer).

Governed intelligence layer: governed ingestion + data quality, versioned
feature store, reproducible ML lifecycle (registry/deployment/monitoring),
fraud detection, churn prediction, predictive maintenance, capacity
forecasting, recommendations and safe remediation intents.

The AI layer never mutates domain state directly. Every operational change
must pass through the authoritative service, policy validation and approval
via a remediation intent (L0-L4 autonomy, kill switch, budget, cooldown).
"""
