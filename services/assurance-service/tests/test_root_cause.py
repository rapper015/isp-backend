"""Root-cause evidence framework: temporal coincidence never auto-confirms."""
import uuid

import pytest

from app.domain.exceptions import RootCauseError
from app.models import RootCauseHypothesis
from app.services import incident_service


def _incident(session, tenant_id):
    return incident_service.create_incident(session, tenant_id=tenant_id, title="RC incident")


def test_hypothesis_created_as_observation(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    h = incident_service.create_hypothesis(session, inc.id, hypothesis="Radius crash",
                                           confidence=0.3, created_by="ai-1", is_ai_suggestion=True)
    session.commit()
    assert h.state == "OBSERVATION"
    assert h.is_ai_suggestion is True


def test_temporal_coincidence_alone_cannot_confirm(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    h = incident_service.create_hypothesis(session, inc.id, hypothesis="happened at same time",
                                           confidence=0.5, created_by="ai-1", is_ai_suggestion=True)
    session.commit()
    # No evidence at all -> cannot confirm (temporal coincidence is not evidence)
    with pytest.raises(RootCauseError):
        incident_service.confirm_root_cause(session, h.id, confirmed_by="sre-1")
    session.commit()
    assert h.state != "CONFIRMED_ROOT_CAUSE"


def test_evidence_required_before_confirm(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    h = incident_service.create_hypothesis(session, inc.id, hypothesis="Core router reboot",
                                           created_by="sre-1")
    session.commit()
    with pytest.raises(RootCauseError):
        incident_service.confirm_root_cause(session, h.id, confirmed_by="sre-1")
    incident_service.add_evidence(session, h.id, evidence_type="TOPOLOGY_DEPENDENCY",
                                  evidence_ref="routeros-pop-1", supports=True,
                                  detail={"matched": True})
    incident_service.transition_hypothesis(session, h.id, "HYPOTHESIS")
    session.commit()
    confirmed = incident_service.confirm_root_cause(session, h.id, confirmed_by="sre-1")
    session.commit()
    assert confirmed.state == "CONFIRMED_ROOT_CAUSE"
    assert confirmed.confirmed_by == "sre-1"


def test_contradicting_evidence_blocks_confirm(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    h = incident_service.create_hypothesis(session, inc.id, hypothesis="Firmware bug",
                                           created_by="sre-1")
    session.commit()
    incident_service.add_evidence(session, h.id, evidence_type="SERVICE_DEPENDENCY",
                                  evidence_ref="aaa", supports=True)
    incident_service.add_evidence(session, h.id, evidence_type="CHANGE_EVENT",
                                  evidence_ref="rollout-1", supports=False,
                                  detail={"reason": "no correlate"})
    session.commit()
    with pytest.raises(RootCauseError):
        incident_service.confirm_root_cause(session, h.id, confirmed_by="sre-1")


def test_hypothesis_transitions(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    h = incident_service.create_hypothesis(session, inc.id, hypothesis="Config drift",
                                           created_by="sre-1")
    session.commit()
    incident_service.transition_hypothesis(session, h.id, "HYPOTHESIS")
    incident_service.transition_hypothesis(session, h.id, "LIKELY_CAUSE")
    session.commit()
    assert h.state == "LIKELY_CAUSE"
    with pytest.raises(RootCauseError):
        incident_service.transition_hypothesis(session, h.id, "OBSERVATION")


def test_reject_hypothesis(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    h = incident_service.create_hypothesis(session, inc.id, hypothesis="Wrong guess",
                                           created_by="sre-1")
    session.commit()
    incident_service.transition_hypothesis(session, h.id, "REJECTED_HYPOTHESIS")
    session.commit()
    assert h.state == "REJECTED_HYPOTHESIS"


def test_ai_suggestion_never_auto_confirmed(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    h = incident_service.create_hypothesis(session, inc.id, hypothesis="AI guess",
                                           confidence=0.9, created_by="ai", is_ai_suggestion=True)
    session.commit()
    incident_service.add_evidence(session, h.id, evidence_type="TIME_PROXIMITY",
                                  evidence_ref="t-1", supports=True)
    session.commit()
    # Even with evidence, an AI suggestion requires explicit human confirmation
    confirmed = incident_service.confirm_root_cause(session, h.id, confirmed_by="human-sre")
    session.commit()
    assert confirmed.state == "CONFIRMED_ROOT_CAUSE"
    assert confirmed.confirmed_by == "human-sre"
