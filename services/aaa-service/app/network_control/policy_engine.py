"""Deterministic policy precedence and explainable decisions (spec §7).

Precedence model:
- REJECT restrictions short-circuit in strict order: security block,
  regulatory, administrative suspension, fraud, billing suspension, OSS state.
  A lower-priority restriction can never silently override a higher one.
- Effective value merge (lowest -> highest): default -> add-on boost ->
  plan entitlement -> congestion throttle -> FUP throttle -> temporary override.
  Throttle/restriction layers override plan speeds; temporary overrides win.
Every decision records which rules were evaluated, which won and which were
rejected, so operators can explain any subscriber's resulting policy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import POLICY_PRECEDENCE

REJECT_LAYERS = (
    ("security", "security_block", "REJECT_SECURITY_BLOCK", "emergency security block"),
    ("regulatory", "regulatory_block", "REJECT_REGULATORY", "regulatory or compliance restriction"),
    ("administrative", "admin_suspended", "REJECT_ACCOUNT_DISABLED", "administrative suspension"),
    ("fraud", "fraud", "REJECT_FRAUD", "fraud restriction"),
    ("billing", "billing_suspended", "REJECT_BILLING_SUSPENSION", "billing suspension"),
    ("oss", "oss_suspended", "REJECT_OSS_SERVICE_STATE", "OSS service state"),
)


@dataclass
class PolicyFacts:
    tenant_id: Any
    subscriber_id: Any | None = None
    customer_ref: str | None = None
    subscription_ref: str | None = None
    session_ref: str | None = None
    nas_id: Any | None = None
    policy_version_id: Any | None = None
    default_policy: dict = field(default_factory=dict)
    plan_policy: dict = field(default_factory=dict)
    addon_policies: list[dict] = field(default_factory=list)
    subscriber_policy: dict | None = None
    temporary_override: dict | None = None
    temporary_expired: bool = False
    fup_tier: dict | None = None
    congestion_tier: dict | None = None
    billing_suspended: bool = False
    oss_suspended: bool = False
    fraud: bool = False
    admin_suspended: bool = False
    security_block: bool = False
    regulatory_block: bool = False
    now: datetime | None = None


@dataclass
class EvaluationResult:
    decision: str
    reason_code: str
    explanation: str
    policy: dict
    provenance: dict[str, str]
    rules_evaluated: list[str]
    winning_rules: list[dict]
    rejected_rules: list[dict]

    def reply_attributes(self) -> dict:
        from .radius_compiler import compile_radius_attributes

        return compile_radius_attributes(self.policy)


def _merge_policy(target: dict, layer: dict, layer_name: str, provenance: dict[str, str], rules_evaluated: list[str], winning: list[dict], rejected: list[dict]) -> None:
    for key, value in layer.items():
        if value is None:
            continue
        rules_evaluated.append(f"{layer_name}:{key}")
        if layer_name == "addon" and key in ("upload_kbps", "download_kbps"):
            # Add-on boost is additive on top of the plan/default entitlement.
            previous = int(target.get(key, 0) or 0)
            target[key] = previous + int(value)
            if key in provenance:
                rejected.append({"layer": layer_name, "key": key, "value": value, "reason": f"applied additively over {provenance.get(key)}"})
            else:
                provenance[key] = layer_name
            winning.append({"layer": layer_name, "key": key, "value": value, "additive": True})
            continue
        if key in target:
            rejected.append({"layer": layer_name, "key": key, "value": value, "reason": f"overridden by higher-precedence {provenance.get(key)}"})
        target[key] = value
        provenance[key] = layer_name
        winning.append({"layer": layer_name, "key": key, "value": value})


def evaluate_policy(facts: PolicyFacts) -> EvaluationResult:
    now = facts.now or datetime.now()
    rules_evaluated: list[str] = []
    winning_rules: list[dict] = []
    rejected_rules: list[dict] = []

    # 1) Reject restrictions in strict order.
    for layer_name, fact_key, reason_code, label in REJECT_LAYERS:
        active = getattr(facts, fact_key)
        if active:
            winning_rules.append({"layer": layer_name, "rule": label, "decision": reason_code})
            return EvaluationResult(
                decision=reason_code,
                reason_code=reason_code,
                explanation=f"rejected because {label} is active",
                policy={},
                provenance={},
                rules_evaluated=[f"{layer_name}:{label}"],
                winning_rules=winning_rules,
                rejected_rules=rejected_rules,
            )
        rules_evaluated.append(f"{layer_name}:{label}=inactive")

    # 2) Effective value merge, lowest -> highest precedence.
    policy: dict[str, Any] = {}
    provenance: dict[str, str] = {}

    layers: list[tuple[str, dict]] = []
    if facts.default_policy:
        layers.append(("default", facts.default_policy))
    if facts.plan_policy:
        layers.append(("plan", facts.plan_policy))
    # Add-on boost is additive on top of the plan/default entitlement.
    boost = {"upload_kbps": 0, "download_kbps": 0}
    for addon in facts.addon_policies:
        boost["upload_kbps"] += int(addon.get("upload_boost_kbps") or 0)
        boost["download_kbps"] += int(addon.get("download_boost_kbps") or 0)
        rules_evaluated.append(f"addon:{addon.get('code', '?')}")
    if boost["upload_kbps"] or boost["download_kbps"]:
        layers.append(("addon", boost))
    if facts.congestion_tier:
        layers.append(("congestion", facts.congestion_tier))
    if facts.fup_tier:
        layers.append(("fup", facts.fup_tier))
    if facts.temporary_override and not facts.temporary_expired:
        layers.append(("temporary", facts.temporary_override))
    if facts.temporary_expired:
        rejected_rules.append({"layer": "temporary", "reason": "temporary override expired"})
        rules_evaluated.append("temporary:expired")

    for layer_name, layer in layers:
        _merge_policy(policy, layer, layer_name, provenance, rules_evaluated, winning_rules, rejected_rules)

    provenance_values = set(provenance.values())
    reason_code = (
        "TEMPORARY_OVERRIDE_APPLIED"
        if "temporary" in provenance_values
        else "FUP_THROTTLE_APPLIED"
        if "fup" in provenance_values
        else "CONGESTION_THROTTLE_APPLIED"
        if "congestion" in provenance_values
        else "PLAN_ENTITLEMENT"
        if "plan" in provenance_values
        else "DEFAULT_POLICY"
    )
    explanation = _explain(reason_code, provenance)
    return EvaluationResult(
        decision="ACCEPT",
        reason_code=reason_code,
        explanation=explanation,
        policy=policy,
        provenance=provenance,
        rules_evaluated=rules_evaluated,
        winning_rules=winning_rules,
        rejected_rules=rejected_rules,
    )


def _explain(reason_code: str, provenance: dict[str, str]) -> str:
    parts = [f"decision={reason_code}"]
    order = list(POLICY_PRECEDENCE)
    ranked = sorted(provenance.items(), key=lambda item: (order.index(item[1]) if item[1] in order else 99, item[0]))
    for key, layer in ranked:
        parts.append(f"{key} from {layer}")
    return "; ".join(parts)
