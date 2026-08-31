"""Unit tests: CRM state machines (lead, lifecycle, KYC, CAF)."""
import pytest

from app.state_machine import caf_transition, kyc_transition, lead_transition, lifecycle_transition


def test_lead_transitions_are_validated():
    assert lead_transition("NEW", "ASSIGNED") == "ASSIGNED"
    assert lead_transition("QUALIFICATION", "FEASIBILITY_PENDING") == "FEASIBILITY_PENDING"
    assert lead_transition("WON", "CONVERTED") == "CONVERTED"
    with pytest.raises(ValueError):
        lead_transition("NEW", "CONVERTED")  # cannot skip stages
    with pytest.raises(ValueError):
        lead_transition("CONVERTED", "NEW")  # terminal


def test_lifecycle_transitions_are_validated():
    assert lifecycle_transition("PROSPECT", "ONBOARDING") == "ONBOARDING"
    assert lifecycle_transition("KYC_VERIFIED", "READY_FOR_SERVICE") == "READY_FOR_SERVICE"
    assert lifecycle_transition("ACTIVE", "SUSPENSION_PENDING") == "SUSPENSION_PENDING"
    with pytest.raises(ValueError):
        lifecycle_transition("PROSPECT", "ACTIVE")
    with pytest.raises(ValueError):
        lifecycle_transition("CLOSED", "ACTIVE")  # terminal


def test_kyc_transitions_are_validated():
    assert kyc_transition("SUBMITTED", "UNDER_REVIEW") == "UNDER_REVIEW"
    assert kyc_transition("UNDER_REVIEW", "VERIFIED") == "VERIFIED"
    with pytest.raises(ValueError):
        kyc_transition("NOT_STARTED", "VERIFIED")


def test_caf_transitions_are_validated():
    assert caf_transition("SUBMITTED", "UNDER_REVIEW") == "UNDER_REVIEW"
    assert caf_transition("VERIFIED", "APPROVED") == "APPROVED"
    with pytest.raises(ValueError):
        caf_transition("DRAFT", "APPROVED")
