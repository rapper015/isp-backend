"""M3 FUP: threshold tiers, progressive throttling, top-up, cycle reset,
idempotent threshold events."""
from app.models import EnforcementAction, FairUsagePolicy, FupCounter, UsageProjection
from app.network_control.fup import (
    active_throttle_tier,
    apply_topup,
    evaluate_fup,
    record_threshold_event,
    reset_cycle,
    usage_bytes,
)

THRESHOLDS = [
    {"label": "tier-1", "limit_bytes": 1_000_000_000, "upload_kbps": 4096, "download_kbps": 16384, "combined": True},
    {"label": "tier-2", "limit_bytes": 2_000_000_000, "upload_kbps": 2048, "download_kbps": 8192, "combined": True},
]


def _fup(tenant_id, thresholds=None) -> FairUsagePolicy:
    return FairUsagePolicy(
        tenant_id=tenant_id,
        code="fup-basic",
        name="Basic FUP",
        cycle="monthly",
        thresholds=thresholds or THRESHOLDS,
        reset_rule="cycle_start",
        grace_bytes=0,
    )


def _usage(session, tenant_id, subscriber_id, period, input_octets, output_octets):
    session.add(UsageProjection(tenant_id=tenant_id, subscriber_id=subscriber_id, period=period, input_octets=input_octets, output_octets=output_octets))


def test_active_throttle_tier_deterministic():
    fup = _fup("t")
    assert active_throttle_tier(fup, input_octets=1_500_000_000, output_octets=0) == {"label": "tier-1", "upload_kbps": 4096, "download_kbps": 16384}
    assert active_throttle_tier(fup, input_octets=2_500_000_000, output_octets=0)["label"] == "tier-2"
    assert active_throttle_tier(fup, input_octets=500_000_000, output_octets=0) is None


def test_topup_raises_effective_limit(session, tenant, subscriber):
    fup = _fup(tenant.id)
    period = "2026-08"
    _usage(session, tenant.id, subscriber.subscriber_id, period, 2_400_000_000, 0)
    counter = apply_topup(session, tenant.id, subscriber.subscriber_id, fup, 2_000_000_000)
    session.commit()
    # 2.4GB vs 2GB tier-2 limit + 2GB topup -> no tier applies.
    assert active_throttle_tier(fup, 2_400_000_000, 0, counter.topup_bytes) is None
    assert counter.topup_bytes == 2_000_000_000


def test_threshold_event_is_idempotent(session, tenant, subscriber):
    fup = _fup(tenant.id)
    _usage(session, tenant.id, subscriber.subscriber_id, "2026-08", 2_500_000_000, 0)
    session.commit()
    tier = {"label": "tier-2", "upload_kbps": 2048, "download_kbps": 8192}
    first = record_threshold_event(session, tenant.id, subscriber.subscriber_id, fup, tier)
    second = record_threshold_event(session, tenant.id, subscriber.subscriber_id, fup, tier)
    session.commit()
    assert first.id == second.id
    actions = session.query(EnforcementAction).filter(EnforcementAction.tenant_id == tenant.id).all()
    assert len(actions) == 1  # duplicate threshold event does not repeat the action


def test_reset_restores_normal(session, tenant, subscriber):
    fup = _fup(tenant.id)
    _usage(session, tenant.id, subscriber.subscriber_id, "2026-08", 2_500_000_000, 0)
    session.commit()
    record_threshold_event(session, tenant.id, subscriber.subscriber_id, fup, {"label": "tier-2", "upload_kbps": 2048, "download_kbps": 8192})
    session.commit()
    counter = session.query(FupCounter).filter(FupCounter.tenant_id == tenant.id, FupCounter.subscriber_id == subscriber.subscriber_id).one()
    assert counter.throttled is True
    reset_cycle(session, tenant.id, subscriber.subscriber_id, fup)
    session.commit()
    counter = session.get(FupCounter, counter.id)
    assert counter.throttled is False
    assert counter.active_tier is None


def test_usage_bytes_reads_projection(session, tenant, subscriber):
    _usage(session, tenant.id, subscriber.subscriber_id, "2026-08", 100, 200)
    session.commit()
    assert usage_bytes(session, tenant.id, subscriber.subscriber_id, "2026-08") == (100, 200)
    assert usage_bytes(session, tenant.id, subscriber.subscriber_id, "2026-09") == (0, 0)


def test_evaluate_fup_returns_none_below_threshold(session, tenant, subscriber):
    fup = _fup(tenant.id)
    _usage(session, tenant.id, subscriber.subscriber_id, "2026-08", 100, 200)
    session.commit()
    assert evaluate_fup(session, tenant.id, subscriber.subscriber_id, fup) is None
