"""GenieACS adapter (fake): device search, parameter retrieval, task lifecycle
(created vs queued vs completed vs faulted), connection request outcomes,
circuit breaker and file/preset management."""
import pytest

from app.integrations.acs import FakeACSClient, get_acs_client
from app.integrations.base import RetryableAdapterError


@pytest.fixture
def client():
    return get_acs_client({"provider": "fake"})


def test_search_devices_by_query(client):
    client.seed_device(serial_number="SN-1", product_class="AN5506")
    client.seed_device(serial_number="SN-2", product_class="HN8255W")
    found = client.search_devices(query="AN5506")
    assert len(found) == 1
    assert found[0]["serialNumber"] == "SN-1"
    assert client.search_devices(query="SN-2")[0]["productClass"] == "HN8255W"


def test_get_parameters(client):
    device_id = client.seed_device(serial_number="SN-3", parameters={
        "Device.DeviceInfo.SoftwareVersion": "V1.0"})
    values = client.get_parameters(device_id, ["Device.DeviceInfo.SoftwareVersion"])
    assert values["Device.DeviceInfo.SoftwareVersion"] == "V1.0"


def test_set_parameters_creates_queued_task_not_completed(client):
    device_id = client.seed_device(serial_number="SN-4")
    task_id = client.set_parameters(device_id, {"Device.WiFi.SSID.1.SSID": "NewNet"})
    task = client.get_task(task_id)
    # Created/queued is NOT proof the device applied it.
    assert task["state"] == "QUEUED"
    # Only after the fake simulates device completion does the state change.
    client.complete_task(task_id, state="COMPLETED")
    assert client.get_task(task_id)["state"] == "COMPLETED"


def test_faulted_task(client):
    device_id = client.seed_device(serial_number="SN-5")
    task_id = client.set_parameters(device_id, {"Device.WiFi.SSID.1.SSID": "X"})
    client.complete_task(task_id, state="FAULTED", result={"fault": "9003"})
    assert client.get_task(task_id)["state"] == "FAULTED"


def test_connection_request_outcome_controllable(client):
    device_id = client.seed_device(serial_number="SN-6")
    assert client.trigger_connection_request(device_id) == "ACCEPTED"
    client.set_connection_request_outcome("UNREACHABLE")
    assert client.trigger_connection_request(device_id) == "UNREACHABLE"


def test_acs_unavailable_raises_retryable(client):
    device_id = client.seed_device(serial_number="SN-7")
    client.fail_next("acs_down")
    with pytest.raises(RetryableAdapterError):
        client.health_check()
    # After clearing the failure the ACS is healthy again.
    assert client.health_check()["ok"] is True


def test_presets_provisions_virtual_parameters(client):
    client.manage_presets("default", config={"channel": 1})
    client.manage_provisions("setup", script="require('uci').save('wireless')")
    client.manage_virtual_parameters("wan_ip", config={"path": "Device.IP.Interface.1.IPv4Address.1.IPAddress"})
    from app.integrations.acs import FakeACSClient

    state = FakeACSClient._state
    assert "default" in state["presets"]
    assert "setup" in state["provisions"]
    assert "wan_ip" in state["virtual_parameters"]


def test_upload_and_delete_file(client):
    client.upload_file("fw-1.bin", b"\x00\x01firmware")
    from app.integrations.acs import FakeACSClient

    assert "fw-1.bin" in FakeACSClient._state["files"]
    client.delete_file("fw-1.bin")
    assert "fw-1.bin" not in FakeACSClient._state["files"]


def test_reset_is_in_place():
    client = FakeACSClient()
    client.seed_device(serial_number="SN-R")
    assert len(FakeACSClient._state["devices"]) == 1
    FakeACSClient.reset()
    assert len(FakeACSClient._state["devices"]) == 0
