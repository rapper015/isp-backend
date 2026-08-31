"""Workforce integration adapter (field installation scheduling + status)."""
from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from .base import Adapter, ok_result, register

_COUNTER = itertools.count(1)


@register
class WorkforceClient(Adapter):
    name = "workforce"

    def schedule_installation(self, tenant_id, service_location_id, requested_date=None, order_reference=None) -> dict:
        index = next(_COUNTER)
        target = requested_date or (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
        return {
            "job_reference": f"job-{index:06d}",
            "status": "SCHEDULED",
            "scheduled_date": target,
            "order_reference": order_reference,
        }

    def get_installation_status(self, job_reference) -> dict:
        # Deterministic: any job scheduled by this fake is completed on the
        # first polling call (tests can override via fail_next).
        return {"job_reference": job_reference, "status": "COMPLETED"}

    def cancel_job(self, tenant_id, job_reference) -> dict:
        return ok_result({"job_reference": job_reference, "cancelled": True})
