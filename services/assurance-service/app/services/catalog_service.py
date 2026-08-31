"""Default catalogue seeding: services, SLIs, KPIs, alert routes."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import (AlertRoute, KpiDefinition, ServiceDefinition, SlIDefinition)

SERVICE_CATALOGUE = [
    {"code": "portal", "name": "Customer Portal", "criticality": "HIGH", "tier": "TIER_1",
     "owner_team": "BSS", "status": "ACTIVE"},
    {"code": "crm", "name": "CRM", "criticality": "HIGH", "tier": "TIER_1",
     "owner_team": "BSS", "status": "ACTIVE"},
    {"code": "billing", "name": "Billing", "criticality": "HIGH", "tier": "TIER_1",
     "owner_team": "BSS", "status": "ACTIVE"},
    {"code": "support", "name": "Support & Ticketing", "criticality": "MEDIUM", "tier": "TIER_2",
     "owner_team": "BSS", "status": "ACTIVE"},
    {"code": "provisioning", "name": "Provisioning", "criticality": "HIGH", "tier": "TIER_1",
     "owner_team": "OSS", "status": "ACTIVE"},
    {"code": "aaa", "name": "AAA / RADIUS", "criticality": "CRITICAL", "tier": "TIER_1",
     "owner_team": "NETWORK", "status": "ACTIVE"},
    {"code": "radius", "name": "RADIUS Gateway", "criticality": "CRITICAL", "tier": "TIER_1",
     "owner_team": "NETWORK", "status": "ACTIVE"},
    {"code": "routeros", "name": "RouterOS Integration", "criticality": "HIGH", "tier": "TIER_1",
     "owner_team": "NETWORK", "status": "ACTIVE"},
    {"code": "genieacs", "name": "TR-069 ACS", "criticality": "MEDIUM", "tier": "TIER_2",
     "owner_team": "NETWORK", "status": "ACTIVE"},
    {"code": "workforce", "name": "Workforce / Field Ops", "criticality": "MEDIUM", "tier": "TIER_2",
     "owner_team": "WORKFORCE", "status": "ACTIVE"},
    {"code": "notification", "name": "Notification Provider", "criticality": "MEDIUM", "tier": "TIER_2",
     "owner_team": "PLATFORM", "status": "ACTIVE"},
    {"code": "object-storage", "name": "Object Storage", "criticality": "MEDIUM", "tier": "TIER_2",
     "owner_team": "PLATFORM", "status": "ACTIVE"},
    {"code": "dns", "name": "DNS", "criticality": "HIGH", "tier": "TIER_1",
     "owner_team": "NETWORK", "status": "ACTIVE"},
]

SLI_CATALOGUE = [
    {"code": "sli_login_success_rate", "name": "Login success rate", "service_code": "portal",
     "measurement_source": "auth", "good_event_definition": "login.succeeded", "valid_event_definition": "login.*",
     "unit": "ratio", "owner": "BSS"},
    {"code": "sli_portal_availability", "name": "Portal availability", "service_code": "portal",
     "measurement_source": "synthetic", "good_event_definition": "probe == UP", "valid_event_definition": "probe executed",
     "unit": "ratio", "owner": "BSS"},
    {"code": "sli_radius_auth_success", "name": "RADIUS auth success rate", "service_code": "radius",
     "measurement_source": "radius", "good_event_definition": "Access-Accept", "valid_event_definition": "Access-Request",
     "unit": "ratio", "owner": "NETWORK"},
    {"code": "sli_aaa_availability", "name": "AAA availability", "service_code": "aaa",
     "measurement_source": "synthetic", "good_event_definition": "probe == UP", "valid_event_definition": "probe executed",
     "unit": "ratio", "owner": "NETWORK"},
    {"code": "sli_provisioning_success", "name": "Provisioning success rate", "service_code": "provisioning",
     "measurement_source": "provisioning", "good_event_definition": "job.completed OK", "valid_event_definition": "job.*",
     "unit": "ratio", "owner": "OSS"},
    {"code": "sli_routeros_readiness", "name": "RouterOS readiness", "service_code": "routeros",
     "measurement_source": "synthetic", "good_event_definition": "probe == UP", "valid_event_definition": "probe executed",
     "unit": "ratio", "owner": "NETWORK"},
    {"code": "sli_payment_success", "name": "Payment success rate", "service_code": "billing",
     "measurement_source": "payments", "good_event_definition": "payment.captured", "valid_event_definition": "payment.*",
     "unit": "ratio", "owner": "BSS"},
    {"code": "sli_support_response", "name": "Support first response", "service_code": "support",
     "measurement_source": "tickets", "good_event_definition": "first_response <= target", "valid_event_definition": "ticket.created",
     "unit": "ratio", "owner": "BSS"},
]

KPI_CATALOGUE = [
    {"code": "kpi_active_radius_sessions", "name": "Active RADIUS sessions", "formula": "count(session.active)",
     "unit": "number", "owner": "NETWORK"},
    {"code": "kpi_auth_attempts", "name": "RADIUS auth attempts", "formula": "count(auth.request)",
     "unit": "number", "owner": "NETWORK"},
    {"code": "kpi_auth_success_rate", "name": "RADIUS auth success rate", "formula": "auth.accept / auth.request",
     "unit": "ratio", "owner": "NETWORK"},
    {"code": "kpi_login_attempts", "name": "Portal login attempts", "formula": "count(login.*)",
     "unit": "number", "owner": "BSS"},
    {"code": "kpi_login_success_rate", "name": "Portal login success rate", "formula": "login.succeeded / login.*",
     "unit": "ratio", "owner": "BSS"},
    {"code": "kpi_provisioning_jobs", "name": "Provisioning jobs", "formula": "count(job.*)",
     "unit": "number", "owner": "OSS"},
    {"code": "kpi_provisioning_success_rate", "name": "Provisioning success rate", "formula": "job.completed OK / job.*",
     "unit": "ratio", "owner": "OSS"},
    {"code": "kpi_open_tickets", "name": "Open support tickets", "formula": "count(ticket.open)",
     "unit": "number", "owner": "BSS"},
    {"code": "kpi_payment_success_rate", "name": "Payment success rate", "formula": "payment.captured / payment.*",
     "unit": "ratio", "owner": "BSS"},
    {"code": "kpi_device_offline", "name": "CPE devices offline", "formula": "count(device.offline)",
     "unit": "number", "owner": "NETWORK"},
]

ROUTE_DEFAULTS = [
    {"name": "NOC_DASHBOARD", "match_labels": {"severity": "CRITICAL"}, "channel": "NOC_DASHBOARD",
     "recipients": ["noc"], "escalation_policy": {"steps": [{"wait": 300, "target": "noc-oncall"}]},
     "fallback_route": "DEFAULT", "is_active": True},
    {"name": "DEFAULT", "match_labels": {}, "channel": "STATUS_PAGE", "recipients": [],
     "escalation_policy": {"steps": []}, "fallback_route": None, "is_active": True},
]


def ensure_defaults(session: Session) -> None:
    services = {s.code: s for s in session.query(ServiceDefinition).all()}
    for entry in SERVICE_CATALOGUE:
        code = entry["code"]
        if code not in services:
            services[code] = ServiceDefinition(**entry)
            session.add(services[code])
    session.flush()
    slis = {s.code: s for s in session.query(SlIDefinition).all()}
    for entry in SLI_CATALOGUE:
        code = entry["code"]
        if code in slis:
            continue
        row_entry = dict(entry)
        service_code = row_entry.pop("service_code")
        svc = services.get(service_code)
        slis[code] = SlIDefinition(service_id=svc.id if svc else None, **row_entry)
        session.add(slis[code])
    session.flush()
    kpis = {k.code: k for k in session.query(KpiDefinition).all()}
    for entry in KPI_CATALOGUE:
        code = entry["code"]
        if code not in kpis:
            kpis[code] = KpiDefinition(**entry)
            session.add(kpis[code])
    routes = {r.name for r in session.query(AlertRoute).all()}
    for entry in ROUTE_DEFAULTS:
        if entry["name"] not in routes:
            session.add(AlertRoute(**entry))
    session.flush()
