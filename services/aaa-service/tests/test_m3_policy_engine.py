"""M3 policy engine: deterministic precedence, reject restrictions, temporary
overrides, FUP/congestion throttling, add-on boosts, explainable decisions."""
import pytest

from app.network_control.policy_engine import PolicyFacts, evaluate_policy

PLAN = {"upload_kbps": 10000, "download_kbps": 50000, "priority": "normal", "ipv4_pool": "pppoe-pool"}
FUP_TIER = {"label": "tier-2", "upload_kbps": 2048, "download_kbps": 8192}
CONGESTION = {"upload_kbps": 5000, "download_kbps": 20000}


def _facts(**overrides) -> PolicyFacts:
    base = dict(
        tenant_id="t1",
        subscriber_id="s1",
        default_policy={"upload_kbps": 1000, "download_kbps": 4000},
        plan_policy=dict(PLAN),
    )
    base.update(overrides)
    return PolicyFacts(**base)


def test_plan_entitlement_wins_over_default():
    result = evaluate_policy(_facts())
    assert result.decision == "ACCEPT"
    assert result.policy["upload_kbps"] == 10000
    assert result.policy["download_kbps"] == 50000
    assert result.provenance["upload_kbps"] == "plan"
    assert result.reason_code == "PLAN_ENTITLEMENT"


def test_security_block_short_circuits_everything():
    result = evaluate_policy(_facts(security_block=True, plan_policy=dict(PLAN)))
    assert result.decision == "REJECT_SECURITY_BLOCK"
    assert result.policy == {}


def test_regulatory_overrides_lower_restrictions_order():
    # billing is lower precedence than regulatory: regulatory wins if both.
    result = evaluate_policy(_facts(regulatory_block=True, billing_suspended=True))
    assert result.decision == "REJECT_REGULATORY"


def test_billing_suspension_rejects_despite_plan():
    result = evaluate_policy(_facts(billing_suspended=True, plan_policy=dict(PLAN)))
    assert result.decision == "REJECT_BILLING_SUSPENSION"


def test_oss_suspended_rejects():
    result = evaluate_policy(_facts(oss_suspended=True))
    assert result.decision == "REJECT_OSS_SERVICE_STATE"


def test_fup_throttle_overrides_plan_speed():
    result = evaluate_policy(_facts(fup_tier=dict(FUP_TIER)))
    assert result.policy["upload_kbps"] == 2048
    assert result.policy["download_kbps"] == 8192
    assert result.provenance["upload_kbps"] == "fup"
    assert result.reason_code == "FUP_THROTTLE_APPLIED"


def test_congestion_throttle_applies():
    result = evaluate_policy(_facts(congestion_tier=dict(CONGESTION)))
    assert result.policy["upload_kbps"] == 5000
    assert result.provenance["upload_kbps"] == "congestion"


def test_temporary_override_wins_over_fup_and_plan():
    temp = {"upload_kbps": 20000, "download_kbps": 100000}
    result = evaluate_policy(_facts(fup_tier=dict(FUP_TIER), temporary_override=temp))
    assert result.policy["upload_kbps"] == 20000
    assert result.policy["download_kbps"] == 100000
    assert result.reason_code == "TEMPORARY_OVERRIDE_APPLIED"


def test_expired_temporary_override_ignored():
    temp = {"upload_kbps": 20000}
    result = evaluate_policy(_facts(temporary_override=temp, temporary_expired=True))
    assert result.policy["upload_kbps"] == 10000  # plan wins
    assert any(rule["layer"] == "temporary" for rule in result.rejected_rules)


def test_addon_boost_is_additive_to_plan():
    result = evaluate_policy(_facts(addon_policies=[{"code": "boost-5", "upload_boost_kbps": 5000, "download_boost_kbps": 10000}]))
    assert result.policy["upload_kbps"] == 15000
    assert result.policy["download_kbps"] == 60000


def test_explainable_decision_records_rules():
    result = evaluate_policy(_facts(fup_tier=dict(FUP_TIER)))
    assert result.rules_evaluated
    assert any(rule["layer"] == "fup" for rule in result.winning_rules)
    assert any(rule["layer"] == "plan" for rule in result.rejected_rules)


def test_reply_attributes_compile_from_decision():
    result = evaluate_policy(_facts())
    reply = result.reply_attributes()
    assert reply["Mikrotik-Rate-Limit"] == "50M/10M"
    assert reply["Framed-Pool"] == "pppoe-pool"
