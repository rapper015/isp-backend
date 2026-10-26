"""Manage normalized franchise resource entitlements.

The JSON profile arrays are maintained as a temporary compatibility projection
for older clients.  New authorization decisions must use these tables.
"""
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Franchise,
    FranchiseIpPoolAssignment,
    FranchiseNasAssignment,
    FranchisePackageAssignment,
)


ASSIGNMENT_TYPES = {
    "package_ids": (FranchisePackageAssignment, "package_id"),
    "nas_ids": (FranchiseNasAssignment, "nas_id"),
    "ip_pool_ids": (FranchiseIpPoolAssignment, "ip_pool_id"),
}


def _unique_ids(values: Iterable[UUID | str] | None) -> list[UUID]:
    return sorted({UUID(str(value)) for value in values or []}, key=str)


def replace_assignments(
    session: Session,
    franchise: Franchise,
    profile: dict | None,
    actor: str | None = None,
) -> dict[str, list[str]]:
    """Replace every resource entitlement and refresh the legacy projection."""
    source = dict(profile or {})
    projection: dict[str, list[str]] = {}
    for key, (model, resource_field) in ASSIGNMENT_TYPES.items():
        existing = list(session.scalars(select(model).where(
            model.tenant_id == franchise.tenant_id,
            model.franchise_id == franchise.id,
        )))
        values = _unique_ids(source.get(key))
        desired = set(values)
        existing_by_resource = {getattr(row, resource_field): row for row in existing}
        for resource_id, row in existing_by_resource.items():
            if resource_id not in desired:
                session.delete(row)
        for resource_id in desired - set(existing_by_resource):
            session.add(model(
                tenant_id=franchise.tenant_id,
                franchise_id=franchise.id,
                **{resource_field: resource_id},
                created_by=actor,
            ))
        projection[key] = [str(value) for value in values]

    # Keep the old response/write contract during the rollout.  Assign a new
    # dict so SQLAlchemy detects the JSON column change.
    franchise.profile = {**source, **projection}
    return projection


def load_assignment_map(
    session: Session,
    tenant_id: UUID,
    franchise_ids: Iterable[UUID],
) -> dict[UUID, dict[str, list[str]]]:
    franchise_ids = set(franchise_ids)
    result = {
        franchise_id: {key: [] for key in ASSIGNMENT_TYPES}
        for franchise_id in franchise_ids
    }
    if not franchise_ids:
        return result
    for key, (model, resource_field) in ASSIGNMENT_TYPES.items():
        rows = session.scalars(select(model).where(
            model.tenant_id == tenant_id,
            model.franchise_id.in_(franchise_ids),
        ))
        for row in rows:
            result[row.franchise_id][key].append(str(getattr(row, resource_field)))
    for assignments in result.values():
        for values in assignments.values():
            values.sort()
    return result


def assignments_for(session: Session, franchise: Franchise) -> dict[str, list[str]]:
    return load_assignment_map(session, franchise.tenant_id, [franchise.id])[franchise.id]


def profile_with_assignments(profile: dict | None, assignments: dict[str, list[str]]) -> dict:
    """Return the compatibility profile with relational values as authority."""
    return {**dict(profile or {}), **assignments}
