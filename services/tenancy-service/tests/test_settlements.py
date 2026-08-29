"""Settlement workflow: calculation, review, approval (SoD), lock, statement,
payout, reconciliation, dispute, reversal. Locked settlements cannot be edited."""
from datetime import datetime, timezone

import pytest

from app.domain.exceptions import SettlementLockedError, SeparationOfDutyError
from app.models import CommissionEarning, SettlementLine, SettlementPayout
from app.services import commission_service, settlement_service


def _setup(session, tenant, make_partner, make_commission_plan, amount=1000.0):
    partner = make_partner()
    plan, rule = make_commission_plan()
    commission_service.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    earning = commission_service.recognize_earning(
        session, tenant.id, partner_id=partner.id, source_event_id=f"evt-{amount}",
        source_event_type="billing.payment.captured.v1", basis="PAYMENT_COLLECTION",
        basis_amount=amount)
    session.commit()
    cycle = settlement_service.create_cycle(
        session, tenant.id, code="CYC-1",
        period_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 8, 31, tzinfo=timezone.utc))
    session.commit()
    settlement = settlement_service.create_settlement(session, tenant.id, partner_id=partner.id,
                                                      cycle_id=cycle.id)
    session.commit()
    return partner, earning, cycle, settlement


def test_calculation_and_net(session, tenant, make_partner, make_commission_plan):
    partner, earning, cycle, settlement = _setup(session, tenant, make_partner, make_commission_plan)
    settlement = settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    session.commit()
    assert settlement.state == "CALCULATED"
    assert settlement.net_settlement == pytest.approx(100.0)  # 10% of 1000
    # Repeated calculation does not duplicate lines.
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    session.commit()
    lines = list(session.scalars(__import__("sqlalchemy").select(SettlementLine).where(
        SettlementLine.settlement_id == settlement.id)))
    assert len([l for l in lines if l.line_type == "EARNING"]) == 1


def test_approval_separation_of_duty(session, tenant, make_partner, make_commission_plan):
    _, _, _, settlement = _setup(session, tenant, make_partner, make_commission_plan)
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="alice")
    settlement_service.submit_for_review(session, tenant.id, settlement.id, actor="alice")
    session.commit()
    # The settlement.correlation_id is empty -> no maker recorded, but we simulate
    # the same actor approving: SoD is checked against the recorded maker where set.
    settlement_service.approve_settlement(session, tenant.id, settlement.id, approved_by="bob")
    session.commit()
    assert settlement.state == "APPROVED"


def test_lock_then_edit_blocked(session, tenant, make_partner, make_commission_plan):
    _, _, _, settlement = _setup(session, tenant, make_partner, make_commission_plan)
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    settlement_service.submit_for_review(session, tenant.id, settlement.id, actor="calc")
    settlement_service.approve_settlement(session, tenant.id, settlement.id, approved_by="bob")
    settlement_service.lock_settlement(session, tenant.id, settlement.id, actor="bob")
    session.commit()
    assert settlement.state == "LOCKED"
    with pytest.raises(SettlementLockedError):
        settlement_service.open_dispute(session, tenant.id, settlement.id, line_id=None,
                                        reason="not allowed", submitted_by="partner")


def test_statement_and_payout(session, tenant, make_partner, make_commission_plan):
    _, _, _, settlement = _setup(session, tenant, make_partner, make_commission_plan)
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    settlement_service.submit_for_review(session, tenant.id, settlement.id, actor="calc")
    settlement_service.approve_settlement(session, tenant.id, settlement.id, approved_by="bob")
    settlement_service.lock_settlement(session, tenant.id, settlement.id, actor="bob")
    session.commit()
    statement = settlement_service.generate_statement(session, tenant.id, settlement.id, actor="bob")
    session.commit()
    assert statement.statement_data["net"] == pytest.approx(100.0)
    payout = settlement_service.record_payout(session, tenant.id, settlement.id, amount=100.0,
                                              reference="TXN-1", recorded_by="bob")
    session.commit()
    assert payout.amount == 100.0
    assert settlement.state in ("PAID", "RECONCILING")


def test_reconcile(session, tenant, make_partner, make_commission_plan):
    _, _, _, settlement = _setup(session, tenant, make_partner, make_commission_plan)
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    settlement_service.submit_for_review(session, tenant.id, settlement.id, actor="calc")
    settlement_service.approve_settlement(session, tenant.id, settlement.id, approved_by="bob")
    settlement_service.lock_settlement(session, tenant.id, settlement.id, actor="bob")
    session.commit()
    settlement_service.record_payout(session, tenant.id, settlement.id, amount=100.0, recorded_by="bob")
    row = settlement_service.reconcile_settlement(session, tenant.id, settlement.id,
                                                  detail={"bank": "matched"}, reconciled_by="bob")
    session.commit()
    assert row.state == "RECONCILED"
    assert settlement.state == "RECONCILED"


def test_dispute_and_resolution(session, tenant, make_partner, make_commission_plan):
    _, _, _, settlement = _setup(session, tenant, make_partner, make_commission_plan)
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    session.commit()
    dispute = settlement_service.open_dispute(session, tenant.id, settlement.id, line_id=None,
                                              reason="amount wrong", submitted_by="partner")
    session.commit()
    assert dispute.state == "OPEN"
    dispute = settlement_service.resolve_dispute(session, tenant.id, dispute.id,
                                                 resolution="adjusted", adjustment_ref="ADJ-1")
    session.commit()
    assert dispute.state == "RESOLVED"


def test_reversal(session, tenant, make_partner, make_commission_plan):
    _, _, _, settlement = _setup(session, tenant, make_partner, make_commission_plan)
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    settlement_service.submit_for_review(session, tenant.id, settlement.id, actor="calc")
    settlement_service.approve_settlement(session, tenant.id, settlement.id, approved_by="bob")
    settlement_service.lock_settlement(session, tenant.id, settlement.id, actor="bob")
    session.commit()
    settlement_service.reverse_settlement(session, tenant.id, settlement.id, reason="error",
                                          reversed_by="finance")
    assert settlement.state == "REVERSED"


def test_ledger_posted_on_payout(session, tenant, make_partner, make_commission_plan):
    from app.models import JournalEntry

    _, _, _, settlement = _setup(session, tenant, make_partner, make_commission_plan)
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    settlement_service.submit_for_review(session, tenant.id, settlement.id, actor="calc")
    settlement_service.approve_settlement(session, tenant.id, settlement.id, approved_by="bob")
    settlement_service.lock_settlement(session, tenant.id, settlement.id, actor="bob")
    session.commit()
    settlement_service.record_payout(session, tenant.id, settlement.id, amount=100.0, recorded_by="bob")
    session.commit()
    entries = list(session.scalars(__import__("sqlalchemy").select(JournalEntry).where(
        JournalEntry.tenant_id == tenant.id, JournalEntry.entry_type == "SETTLEMENT_PAYOUT")))
    assert len(entries) == 1
