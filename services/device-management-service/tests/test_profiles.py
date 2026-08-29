"""Vendor-neutral versioned profiles: immutability, TR-098/TR-181 compilation,
unsupported parameters, sensitive masking, assignment rules and explainable
decisions."""
import uuid

import pytest

from app.domain.exceptions import ProfileError
from app.models import ProfileAssignmentDecision, ProfileParameter
from app.services import catalog_service, profile_service
from conftest import variant_for


def test_profile_create_and_version_immutable(session, tenant_id, make_profile):
    profile, version = make_profile(code="IMMUTABLE")
    assert version.state == "ACTIVE"
    original_definition = dict(version.definition)
    original_checksum = version.checksum
    # Publishing is immutable: creating a new version must not mutate the active one.
    v2 = profile_service.create_version(session, tenant_id, profile.id,
                                        definition={"WIFI_SSID_24GHZ": {"value": "changed"}}, actor="test")
    session.commit()
    session.refresh(version)
    assert v2.version == 2
    assert version.version == 1
    assert version.definition == original_definition
    assert version.checksum == original_checksum


def test_profile_version_numbering(session, tenant_id, defaults):
    profile = profile_service.create_profile(session, tenant_id, code="VERSIONED", name="Versioned")
    session.commit()
    v1 = profile_service.create_version(session, tenant_id, profile.id,
                                        definition={"WIFI_SSID_24GHZ": {"value": "a"}}, actor="test")
    v2 = profile_service.create_version(session, tenant_id, profile.id,
                                        definition={"WIFI_SSID_24GHZ": {"value": "b"}}, actor="test")
    assert v1.version == 1 and v2.version == 2
    assert v1.state == "DRAFT" and v2.state == "DRAFT"


def test_activate_supersedes_prior_versions(session, tenant_id, defaults):
    profile = profile_service.create_profile(session, tenant_id, code="SUPERSEDE", name="Supersede")
    session.commit()
    v1 = profile_service.create_version(session, tenant_id, profile.id,
                                        definition={"WIFI_SSID_24GHZ": {"value": "a"}}, actor="test")
    profile_service.approve_version(session, tenant_id, v1.id, actor="test")
    profile_service.activate_version(session, tenant_id, v1.id, actor="test")
    session.commit()
    v2 = profile_service.create_version(session, tenant_id, profile.id,
                                        definition={"WIFI_SSID_24GHZ": {"value": "b"}}, actor="test")
    profile_service.approve_version(session, tenant_id, v2.id, actor="test")
    profile_service.activate_version(session, tenant_id, v2.id, actor="test")
    session.commit()
    session.refresh(v1)
    session.refresh(v2)
    assert v1.state == "SUPERSEDED"
    assert v2.state == "ACTIVE"


def test_tr181_compilation(session, tenant_id, defaults):
    """Compile a vendor-neutral profile against the FiberHome TR-181 variant."""
    variant = variant_for(session, data_model_family="TR181")
    assert variant is not None
    # Build a profile version directly and compile it.
    profile = profile_service.create_profile(session, tenant_id, code="COMPILE181", name="Compile")
    session.commit()
    v = profile_service.create_version(session, tenant_id, profile.id, definition={
        "WIFI_SSID_24GHZ": {"value": "TestNet"},
        "VLAN_ID": {"value": 100},
        "PERIODIC_INFORM_INTERVAL": {"value": 60},
    }, actor="test")
    session.commit()
    preview = profile_service.compile_preview(session, tenant_id, v.id,
                                              model_variant_id=variant.id, data_model_family="TR181")
    assert "Device.WiFi.SSID.1.SSID" in preview["compiled"]
    assert "Device.Ethernet.VLANTermination.1.VLANID" in preview["compiled"]
    assert "Device.ManagementServer.PeriodicInformInterval" in preview["compiled"]
    assert preview["unsupported"] == []


def test_tr098_compilation(session, tenant_id, defaults):
    variant = variant_for(session, data_model_family="TR098")
    assert variant is not None
    profile = profile_service.create_profile(session, tenant_id, code="COMPILE098", name="Compile98")
    session.commit()
    v = profile_service.create_version(session, tenant_id, profile.id, definition={
        "WIFI_SSID_24GHZ": {"value": "TestNet"},
        "PERIODIC_INFORM_INTERVAL": {"value": 60},
    }, actor="test")
    session.commit()
    preview = profile_service.compile_preview(session, tenant_id, v.id,
                                              model_variant_id=variant.id, data_model_family="TR098")
    assert "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID" in preview["compiled"]
    assert "InternetGatewayDevice.ManagementServer.PeriodicInformInterval" in preview["compiled"]


def test_unsupported_parameter_flagged(session, tenant_id, defaults):
    profile = profile_service.create_profile(session, tenant_id, code="UNSUPPORTED", name="Unsupported")
    session.commit()
    v = profile_service.create_version(session, tenant_id, profile.id, definition={
        "WIFI_SSID_24GHZ": {"value": "x"},
        "VENDOR_ONLY_PARAM": {"value": "y"},
    }, actor="test")
    session.commit()
    variant = variant_for(session, data_model_family="TR181")
    preview = profile_service.compile_preview(session, tenant_id, v.id,
                                              model_variant_id=variant.id, data_model_family="TR181")
    assert "VENDOR_ONLY_PARAM" in preview["unsupported"]


def test_sensitive_parameter_stored_as_reference(session, tenant_id, make_profile):
    _, version = make_profile(code="SENSITIVE")
    params = {p.code: p for p in session.query(ProfileParameter).filter_by(version_id=version.id)}
    assert params["WIFI_PASSWORD_24GHZ"].sensitive is True
    assert params["WIFI_PASSWORD_24GHZ"].secret_ref is not None
    assert params["WIFI_PASSWORD_24GHZ"].value is None


def test_assignment_rule_resolves_profile(session, tenant_id, defaults, make_profile, make_device):
    profile, version = make_profile(code="ASSIGN_A")
    variant = variant_for(session, model_name="AN5506-04-F1")
    device, _ = make_device(serial="SN-ASSIGN", product_class="AN5506")
    from app.services import device_service as ds

    ds.assign_device(session, tenant_id, device.id, service_location_id="LOC-1", actor="test")
    device.model_variant_id = variant.id
    session.commit()
    profile_service.add_assignment_rule(session, tenant_id, profile.id,
                                        facts={"service_location_id": "LOC-1"}, priority=10, actor="test")
    session.commit()
    selected_profile, selected_version, decision = profile_service.resolve_profile_for_device(
        session, tenant_id, device)
    assert selected_profile is not None and selected_profile.id == profile.id
    assert decision["rule_version"] == 1
    record = session.query(ProfileAssignmentDecision).filter_by(cpe_id=device.id).first()
    assert record is not None and record.selected_profile_id == profile.id


def test_no_matching_rule_returns_none(session, tenant_id, make_profile, make_device):
    make_profile(code="NO_MATCH")
    device, _ = make_device(serial="SN-NOMATCH", product_class="AN5506")
    profile, version, decision = profile_service.resolve_profile_for_device(session, tenant_id, device)
    assert profile is None and version is None
    assert "no matching rule" in decision["reason"]
