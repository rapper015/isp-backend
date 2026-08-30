"""Milestone 3 — Advanced Network Control: enums and constants.

Conventions:
- Policy precedence follows the spec §7 ordering (highest wins).
- Session states extend the existing AAA session registry states.
- Control actions distinguish CoA vs Disconnect and record outcomes."""
from __future__ import annotations

POLICY_STATES = (
    "DRAFT",
    "UNDER_REVIEW",
    "APPROVED",
    "SCHEDULED",
    "ACTIVE",
    "SUPERSEDED",
    "DISABLED",
    "ARCHIVED",
)

POLICY_SOURCES = ("tenant", "plan", "addon", "subscriber", "temporary", "fup", "congestion", "billing", "oss", "crm", "security", "regulatory", "default")

# Spec §7 precedence — higher index = higher priority, applied in order.
POLICY_PRECEDENCE = (
    "default",
    "congestion",
    "fup",
    "addon",
    "plan",
    "temporary",
    "oss",
    "billing",
    "fraud",
    "administrative",
    "regulatory",
    "security",
)

SESSION_STATES_EXT = (
    "STARTING",
    "ACTIVE",
    "STALE",
    "DISCONNECT_REQUESTED",
    "TERMINATING",
    "STOPPED",
    "ORPHANED",
    "UNKNOWN",
)

CONTROL_ACTION_TYPES = ("COA", "DISCONNECT")

CONTROL_ACTION_STATUS = (
    "PENDING",
    "SENT",
    "RETRYING",
    "ACK",
    "NAK",
    "TIMEOUT",
    "FAILED",
    "SUCCEEDED",
    "CANCELLED",
)

ENFORCEMENT_STRATEGIES = (
    "RADIUS_DYNAMIC_QUEUE",
    "PCQ_SHARED",
    "QUEUE_TREE",
    "SIMPLE_QUEUE",
    "EXTERNAL_ENFORCEMENT",
    "UNSUPPORTED",
)

MISMATCH_CLASSIFICATIONS = (
    "INFORMATIONAL",
    "REPAIRABLE",
    "REQUIRES_POLICY_REAPPLY",
    "REQUIRES_DISCONNECT",
    "REQUIRES_MANUAL_INTERVENTION",
    "SECURITY_CRITICAL",
)

DEVICE_OBJECT_KINDS = ("queue_type", "queue_tree", "simple_queue", "pcq", "mangle_rule", "address_list", "packet_mark", "connection_mark")

# RouterOS operations allowlist (typed; arbitrary console commands are prohibited).
ROUTEROS_OPERATIONS = (
    "test_connection",
    "discover_router",
    "read_radius_configuration",
    "read_radius_incoming",
    "read_ppp_aaa",
    "read_hotspot_profiles",
    "read_active_ppp_sessions",
    "read_active_hotspot_sessions",
    "read_ip_pools",
    "read_queues",
    "read_queue_types",
    "read_queue_trees",
    "read_mangle_rules",
    "read_address_lists",
    "create_managed_queue_type",
    "create_managed_queue_tree",
    "create_managed_pcq",
    "create_managed_mangle",
    "create_managed_address_list",
    "remove_managed_object",
    "disconnect_session",
    "verify_applied_policy",
)

# Operations that must never be exposed/automated by default.
PROHIBITED_OPERATIONS = (
    "factory_reset",
    "reboot",
    "package_remove",
    "user_delete",
    "certificate_delete",
    "firewall_replace",
    "config_import",
    "run_script",
    "delete_unowned_queue",
    "console_command",
)

MANAGED_TAG = "managed-by=isp-platform"

REASON_CODES = (
    "ACCEPT",
    "REJECT_UNKNOWN_SUBSCRIBER",
    "REJECT_ACCOUNT_DISABLED",
    "REJECT_ACCOUNT_EXPIRED",
    "REJECT_BILLING_SUSPENSION",
    "REJECT_OSS_SERVICE_STATE",
    "REJECT_FRAUD",
    "REJECT_SECURITY_BLOCK",
    "REJECT_REGULATORY",
    "REJECT_SIMULTANEOUS_LIMIT",
    "REJECT_QUOTA_EXHAUSTED",
    "FUP_THROTTLE_APPLIED",
    "CONGESTION_THROTTLE_APPLIED",
    "TEMPORARY_OVERRIDE_APPLIED",
    "PLAN_ENTITLEMENT",
    "DEFAULT_POLICY",
)
