"""Vendor-neutral configuration profiles: create, version (immutable published),
validate, compile preview, assignment rules and explainable assignment decisions."""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ProfileError, ValidationError
from ..domain.secrets import encrypt_secret
from ..models import (
    DeviceConfigurationProfile,
    DeviceConfigurationProfileVersion,
    ProfileAssignmentDecision,
    ProfileAssignmentRule,
    ProfileParameter,
)
from . import catalog_service
from .audit_service import audit, correlation


def _checksum(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def get_profile_or_404(session: Session, tenant_id, profile_id: uuid.UUID) -> DeviceConfigurationProfile:
    profile = session.get(DeviceConfigurationProfile, profile_id)
    if profile is None or profile.tenant_id != tenant_id:
        raise NotFoundError("profile not found")
    return profile


def get_version_or_404(session: Session, tenant_id, version_id: uuid.UUID) -> DeviceConfigurationProfileVersion:
    version = session.get(DeviceConfigurationProfileVersion, version_id)
    if version is None or version.tenant_id != tenant_id:
        raise NotFoundError("profile version not found")
    return version


def create_profile(session: Session, tenant_id: uuid.UUID, *, code: str, name: str,
                   description: str | None = None, actor: str | None = None) -> DeviceConfigurationProfile:
    existing = session.scalars(select(DeviceConfigurationProfile).where(
        DeviceConfigurationProfile.tenant_id == tenant_id, DeviceConfigurationProfile.code == code)).first()
    if existing is not None:
        raise ProfileError(f"profile {code!r} already exists")
    profile = DeviceConfigurationProfile(tenant_id=tenant_id, code=code, name=name,
                                         description=description, state="DRAFT", created_by=actor)
    session.add(profile)
    session.flush()
    audit(session, tenant_id, "device.profile.created", "device_configuration_profile", str(profile.id),
          actor=actor, correlation_id=correlation(None), payload={"code": code, "name": name})
    return profile


def create_version(session: Session, tenant_id: uuid.UUID, profile_id: uuid.UUID, *, definition: dict,
                   change_summary: str | None = None, actor: str | None = None) -> DeviceConfigurationProfileVersion:
    profile = get_profile_or_404(session, tenant_id, profile_id)
    _validate_definition(definition)
    version_number = (session.scalar(select(DeviceConfigurationProfileVersion.version).where(
        DeviceConfigurationProfileVersion.profile_id == profile.id).order_by(
        DeviceConfigurationProfileVersion.version.desc()).limit(1))) or 0
    version = DeviceConfigurationProfileVersion(
        tenant_id=tenant_id, profile_id=profile.id, version=version_number + 1, state="DRAFT",
        definition=definition, change_summary=change_summary, checksum=_checksum(definition))
    session.add(version)
    session.flush()
    for code, spec in definition.items():
        sensitive = bool(spec.get("sensitive"))
        secret_ref = spec.get("secret_ref") if sensitive else None
        if sensitive and not secret_ref and spec.get("value") is not None:
            secret_ref = encrypt_secret(str(spec["value"]))
        session.add(ProfileParameter(version_id=version.id, code=code,
                                     value=spec.get("value") if not sensitive else None,
                                     secret_ref=secret_ref,
                                     sensitive=sensitive,
                                     writable=bool(spec.get("writable", True))))
    profile.current_version_id = version.id
    session.flush()
    audit(session, tenant_id, "device.profile.version_created", "device_configuration_profile_version",
          str(version.id), actor=actor, correlation_id=correlation(None),
          payload={"profile": profile.code, "version": version.version})
    return version


def _validate_definition(definition: dict) -> None:
    if not isinstance(definition, dict) or not definition:
        raise ProfileError("profile definition must be a non-empty map of parameter code -> spec")
    for code, spec in definition.items():
        if not isinstance(code, str) or not code:
            raise ProfileError("parameter code must be a non-empty string")
        if not isinstance(spec, dict):
            raise ProfileError(f"parameter {code!r} spec must be an object")


def submit_for_approval(session: Session, tenant_id, version_id: uuid.UUID, *, actor: str = "system") -> DeviceConfigurationProfileVersion:
    version = get_version_or_404(session, tenant_id, version_id)
    if version.state != "DRAFT":
        raise ProfileError(f"version cannot be submitted from state {version.state}")
    version.state = "REVIEW"
    session.flush()
    return version


def approve_version(session: Session, tenant_id, version_id: uuid.UUID, *, actor: str = "system") -> DeviceConfigurationProfileVersion:
    from datetime import datetime, timezone

    version = get_version_or_404(session, tenant_id, version_id)
    if version.state not in ("DRAFT", "REVIEW"):
        raise ProfileError(f"version cannot be approved from state {version.state}")
    version.state = "APPROVED"
    version.approved_by = actor
    version.approved_at = datetime.now(timezone.utc)
    session.flush()
    return version


def activate_version(session: Session, tenant_id, version_id: uuid.UUID, *, actor: str = "system") -> DeviceConfigurationProfileVersion:
    from datetime import datetime, timezone

    version = get_version_or_404(session, tenant_id, version_id)
    profile = session.get(DeviceConfigurationProfile, version.profile_id)
    # Supersede prior approved/active versions of the profile.
    for other in session.scalars(select(DeviceConfigurationProfileVersion).where(
            DeviceConfigurationProfileVersion.profile_id == profile.id)):
        if other.id != version.id and other.state in ("APPROVED", "ACTIVE"):
            other.state = "SUPERSEDED"
    version.state = "ACTIVE"
    version.activated_at = datetime.now(timezone.utc)
    profile.state = "ACTIVE"
    profile.current_version_id = version.id
    session.flush()
    audit(session, tenant_id, "device.profile.activated", "device_configuration_profile_version", str(version.id),
          actor=actor, correlation_id=correlation(None), payload={"version": version.version})
    return version


def add_assignment_rule(session: Session, tenant_id, profile_id: uuid.UUID, *, facts: dict, priority: int = 100,
                        reason: str | None = None, actor: str | None = None) -> ProfileAssignmentRule:
    profile = get_profile_or_404(session, tenant_id, profile_id)
    rule_version = (session.scalar(select(ProfileAssignmentRule.rule_version).where(
        ProfileAssignmentRule.profile_id == profile.id).order_by(
        ProfileAssignmentRule.rule_version.desc()).limit(1))) or 0
    rule = ProfileAssignmentRule(tenant_id=tenant_id, profile_id=profile.id, rule_version=rule_version + 1,
                                 facts=facts, priority=priority, reason=reason, is_active=True)
    session.add(rule)
    session.flush()
    audit(session, tenant_id, "device.profile.assignment_rule_added", "device_configuration_profile",
          str(profile.id), actor=actor, correlation_id=correlation(None), payload={"facts": facts})
    return rule


def resolve_profile_for_device(session: Session, tenant_id, device, *, correlation_id: str | None = None
                               ) -> tuple[DeviceConfigurationProfile | None, DeviceConfigurationProfileVersion | None, dict]:
    """Explainable profile selection from assignment rules against device facts."""
    request_id = correlation(correlation_id)
    facts = {
        "tenant_id": str(device.tenant_id),
        "model": device.model_name,
        "hardware_version": device.hardware_version,
        "firmware_version": device.firmware_version,
        "product_class": device.product_class,
        "service_type": device.plan_code if hasattr(device, "plan_code") else None,
        "service_location_id": device.service_location_id,
    }
    rules = list(session.scalars(select(ProfileAssignmentRule).where(
        ProfileAssignmentRule.tenant_id == tenant_id, ProfileAssignmentRule.is_active.is_(True))
        .order_by(ProfileAssignmentRule.priority.asc(), ProfileAssignmentRule.created_at.asc())))
    for rule in rules:
        if _facts_match(rule.facts, facts):
            profile = session.get(DeviceConfigurationProfile, rule.profile_id)
            version_id = profile.current_version_id if profile else None
            version = session.get(DeviceConfigurationProfileVersion, version_id) if version_id else None
            session.add(ProfileAssignmentDecision(
                tenant_id=tenant_id, cpe_id=device.id, input_facts=facts, rule_version=rule.rule_version,
                selected_profile_id=profile.id if profile else None,
                selected_profile_version_id=version.id if version else None,
                reason=rule.reason or f"rule v{rule.rule_version}", correlation_id=request_id))
            session.flush()
            return profile, version, {"rule_version": rule.rule_version, "reason": rule.reason}
    session.add(ProfileAssignmentDecision(tenant_id=tenant_id, cpe_id=device.id, input_facts=facts,
                                          rule_version=None, selected_profile_id=None,
                                          selected_profile_version_id=None, reason="no matching rule",
                                          correlation_id=request_id))
    session.flush()
    return None, None, {"rule_version": None, "reason": "no matching rule"}


def _facts_match(rule_facts: dict, device_facts: dict) -> bool:
    for key, expected in (rule_facts or {}).items():
        actual = device_facts.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif expected is not None and actual != expected:
            return False
    return True


def compile_preview(session: Session, tenant_id, version_id: uuid.UUID, *, model_variant_id: uuid.UUID | None = None,
                    data_model_family: str | None = None) -> dict:
    """Compile a profile version to device-specific paths (impact preview)."""
    version = get_version_or_404(session, tenant_id, version_id)
    mappings = catalog_service.mappings_for_variant(session, model_variant_id) if model_variant_id else []
    family = data_model_family or "TR181"
    if mappings:
        from ..domain.parameters import compile_parameters

        compiled, unsupported = compile_parameters(mappings, version.definition, data_model_family=family)
        return {"compiled": compiled, "unsupported": unsupported, "family": family,
                "mapping_version": max((m["mapping_version"] for m in mappings), default=1)}
    return {"compiled": version.definition, "unsupported": [], "family": family, "mapping_version": 0}
