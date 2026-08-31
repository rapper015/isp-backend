"""Order state machine behaviour: valid/invalid transitions, terminal states,
valid actions, saga/step transitions."""
import pytest

from app.state_machine import (
    ORDER_TRANSITIONS,
    order_terminal,
    order_transition,
    saga_transition,
    step_transition,
)


def test_order_valid_transitions_roundtrip():
    assert order_transition("DRAFT", "SUBMITTED") == "SUBMITTED"
    assert order_transition("SUBMITTED", "VALIDATING") == "VALIDATING"
    assert order_transition("VALIDATING", "READY_FOR_FULFILMENT") == "READY_FOR_FULFILMENT"
    assert order_transition("RESOURCE_RESERVATION", "FIELD_INSTALLATION_PENDING") == "FIELD_INSTALLATION_PENDING"
    assert order_transition("FIELD_INSTALLATION_PENDING", "PROVISIONING") == "PROVISIONING"
    assert order_transition("PROVISIONING", "VERIFYING") == "VERIFYING"
    assert order_transition("VERIFYING", "COMPLETED") == "COMPLETED"


def test_invalid_transition_rejected():
    with pytest.raises(ValueError):
        order_transition("DRAFT", "COMPLETED")
    with pytest.raises(ValueError):
        order_transition("COMPLETED", "SUBMITTED")


def test_terminal_states_are_terminal():
    for state in ("COMPLETED", "ROLLED_BACK", "CANCELLED"):
        assert order_terminal(state)
    assert not order_terminal("SUBMITTED")


def test_all_order_states_have_defined_transitions():
    from app.enums import ORDER_STATES

    for state in ORDER_STATES:
        assert state in ORDER_TRANSITIONS


def test_cancellation_paths():
    assert order_transition("SUBMITTED", "CANCELLATION_REQUESTED") == "CANCELLATION_REQUESTED"
    assert order_transition("CANCELLATION_REQUESTED", "CANCELLED") == "CANCELLED"
    assert order_transition("CANCELLATION_REQUESTED", "COMPENSATING") == "COMPENSATING"


def test_failure_and_recovery_paths():
    assert order_transition("VALIDATING", "VALIDATION_FAILED") == "VALIDATION_FAILED"
    assert order_transition("VALIDATION_FAILED", "SUBMITTED") == "SUBMITTED"
    assert order_transition("FAILED", "MANUAL_INTERVENTION_REQUIRED") == "MANUAL_INTERVENTION_REQUIRED"
    assert order_transition("MANUAL_INTERVENTION_REQUIRED", "SUBMITTED") == "SUBMITTED"
    assert order_transition("COMPENSATING", "ROLLED_BACK") == "ROLLED_BACK"


def test_saga_and_step_transitions():
    assert saga_transition("PENDING", "RUNNING") == "RUNNING"
    assert saga_transition("RUNNING", "COMPLETED") == "COMPLETED"
    assert saga_transition("RUNNING", "COMPENSATING") == "COMPENSATING"
    assert saga_transition("COMPENSATING", "COMPENSATED") == "COMPENSATED"
    assert saga_transition("RUNNING", "MANUAL_INTERVENTION") == "MANUAL_INTERVENTION"
    with pytest.raises(ValueError):
        saga_transition("COMPLETED", "RUNNING")
    assert step_transition("PENDING", "RUNNING") == "RUNNING"
    assert step_transition("RUNNING", "COMPLETED") == "COMPLETED"
    assert step_transition("COMPLETED", "COMPENSATED") == "COMPENSATED"
