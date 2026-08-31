"""Assignment and routing: round-robin, least-loaded, skill routing, fallback
queues and loop prevention."""
from app.services import assignment_service, catalog_service, ticket_service
from app.models import RoutingRule


def _add_agents(session, tenant_id, agents):
    for agent_id, name, skills in agents:
        catalog_service.add_agent(session, tenant_id, "L1_TEAM", agent_id, name=name, skills=skills)


def _l1_rule(session, tenant_id, *, strategy="ROUND_ROBIN", fallback=None, skills=None):
    return catalog_service.add_routing_rule(
        session, tenant_id, name="L1 rule", target_queue_code="L1_SUPPORT", strategy=strategy,
        fallback_queue_code=fallback, required_skills=skills)


def test_round_robin_distributes(session, tenant_id, defaults, make_ticket):
    _add_agents(session, tenant_id, [("a1", "Agent A", []), ("a2", "Agent B", []), ("a3", "Agent C", [])])
    _l1_rule(session, tenant_id)
    session.commit()

    t1 = make_ticket()
    t2 = make_ticket()
    t3 = make_ticket()
    assigned = {t1.assigned_agent_id, t2.assigned_agent_id, t3.assigned_agent_id}
    assert assigned == {"a1", "a2", "a3"}
    assert t1.assigned_agent_id is not None
    assert t1.assigned_queue_id is not None


def test_least_loaded_picks_lightest(session, tenant_id, defaults, make_ticket):
    _add_agents(session, tenant_id, [("a1", "Agent A", []), ("a2", "Agent B", [])])
    _l1_rule(session, tenant_id, strategy="LEAST_LOADED")
    session.commit()
    # Pre-load agent a1 with two open tickets (direct assignment).
    for _ in range(2):
        ticket = make_ticket()
        ticket_service.assign(session, tenant_id, ticket.id, agent_id="a1", actor="test")
    session.commit()
    fresh = make_ticket()
    assert fresh.assigned_agent_id == "a2"


def test_skill_based_routing(session, tenant_id, defaults, make_ticket):
    _add_agents(session, tenant_id, [("a1", "Agent A", ["routers"]), ("a2", "Agent B", [])])
    _l1_rule(session, tenant_id, strategy="SKILL_BASED", skills=["routers"])
    session.commit()
    ticket = make_ticket()
    assert ticket.assigned_agent_id == "a1"


def test_fallback_queue_when_no_agents(session, tenant_id, defaults, make_ticket):
    _add_agents(session, tenant_id, [("a1", "Agent A", [])])
    catalog_service.add_routing_rule(session, tenant_id, name="empty queue", target_queue_code="NOC",
                                     strategy="ROUND_ROBIN", fallback_queue_code="L1_SUPPORT")
    session.commit()
    ticket = make_ticket()
    # NOC queue has no agents; falls back to L1_SUPPORT which has agent a1.
    assert ticket.assigned_agent_id == "a1"


def test_routing_loop_prevention(session, tenant_id, defaults, make_ticket):
    _add_agents(session, tenant_id, [("a1", "Agent A", [])])
    # Target and fallback are the same queue — must not loop forever.
    catalog_service.add_routing_rule(session, tenant_id, name="loop rule", target_queue_code="L1_SUPPORT",
                                     strategy="ROUND_ROBIN", fallback_queue_code="L1_SUPPORT")
    session.commit()
    ticket = make_ticket()  # must terminate
    assert ticket.assigned_agent_id == "a1"


def test_manual_strategy_leaves_agent_blank(session, tenant_id, defaults, make_ticket):
    _add_agents(session, tenant_id, [("a1", "Agent A", [])])
    _l1_rule(session, tenant_id, strategy="MANUAL")
    session.commit()
    ticket = make_ticket()
    assert ticket.assigned_queue_id is not None
    assert ticket.assigned_agent_id is None


def test_detect_orphan_assignments(session, tenant_id, defaults, make_ticket):
    _add_agents(session, tenant_id, [("a1", "Agent A", [])])
    _l1_rule(session, tenant_id)
    session.commit()
    ticket = make_ticket()
    assert ticket.assigned_agent_id == "a1"
    # Deactivate the agent's membership (scoped to this tenant).
    from app.models import SupportAgentMembership
    from sqlalchemy import select

    membership = session.scalars(select(SupportAgentMembership).where(
        SupportAgentMembership.tenant_id == tenant_id, SupportAgentMembership.agent_id == "a1")).first()
    membership.is_active = False
    session.commit()
    orphans = assignment_service.detect_orphan_assignments(session, tenant_id)
    assert any(o["ticket_id"] == str(ticket.id) for o in orphans)
