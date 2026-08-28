"""Unit tests: validated state machines (lifecycle, job, registration, rotation)."""
import pytest

from app.nas_lifecycle import (LIFECYCLE, REGISTRATION, SECRET_ROTATION, job_transition, registration_transition, secret_rotation_transition, transition)


def test_lifecycle_cannot_skip_states():
    with pytest.raises(ValueError):
        transition("DRAFT", "ACTIVE")
    with pytest.raises(ValueError):
        transition("CONNECTION_TESTING", "DISCOVERED")
    assert transition("DRAFT", "CONNECTION_PENDING") == "CONNECTION_PENDING"


def test_lifecycle_terminates_at_decommissioned():
    state = "DRAFT"
    for target in ("DECOMMISSIONING", "DECOMMISSIONED"):
        state = transition(state, target)
    assert state == "DECOMMISSIONED"
    with pytest.raises(ValueError):
        transition("DECOMMISSIONED", "ACTIVE")


def test_lifecycle_reaches_active():
    states = ["CONNECTION_PENDING", "CONNECTION_TESTING", "CONNECTED", "DISCOVERING", "DISCOVERED", "CONFIGURATION_PENDING", "CONFIGURATION_PLANNED", "CONFIGURING", "VERIFYING", "CONFIGURED", "TESTING", "ACTIVE"]
    state = "DRAFT"
    for target in states:
        state = transition(state, target)
    assert state == "ACTIVE"
    assert "ACTIVE" in LIFECYCLE["TESTING"]


def test_job_state_machine():
    assert job_transition("QUEUED", "RUNNING") == "RUNNING"
    assert job_transition("RUNNING", "VERIFYING") == "VERIFYING"
    assert job_transition("VERIFYING", "SUCCEEDED") == "SUCCEEDED"
    with pytest.raises(ValueError):
        job_transition("SUCCEEDED", "RUNNING")
    with pytest.raises(ValueError):
        job_transition("QUEUED", "SUCCEEDED")


def test_registration_state_machine():
    assert registration_transition("MANUALLY_CONFIRMED", "VERIFICATION_PENDING") == "VERIFICATION_PENDING"
    assert registration_transition("VERIFICATION_PENDING", "VERIFIED") == "VERIFIED"
    with pytest.raises(ValueError):
        registration_transition("PENDING", "VERIFIED")
    assert "DETAILS_GENERATED" in REGISTRATION["PENDING"]


def test_secret_rotation_state_machine():
    assert secret_rotation_transition("FREERADIUS_UPDATE_CONFIRMED", "ROUTER_UPDATE_PENDING") == "ROUTER_UPDATE_PENDING"
    assert secret_rotation_transition("VERIFYING", "ACTIVE") == "ACTIVE"
    with pytest.raises(ValueError):
        secret_rotation_transition("ROTATION_DRAFT", "ACTIVE")
    assert "FAILED" in SECRET_ROTATION["VERIFYING"]
