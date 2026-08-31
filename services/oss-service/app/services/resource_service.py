"""Deterministic, conflict-free resource reservation.

Allocation is protected by database compare-and-set on ResourceInventory.status
(atomic single-row UPDATE ... WHERE status='AVAILABLE'), so concurrent workers
cannot double-allocate. Redis is an optional assist only. Expiry releases stale
reservations."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..events import publish_outbox
from ..models import ResourceInventory, ResourceReservation

DEFAULT_TTL_SECONDS = 15 * 60  # 15 minutes


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResourceUnavailable(Exception):
    pass


class InvalidReservation(Exception):
    pass


class ResourceService:
    def __init__(self, session: Session):
        self.session = session

    # -- inventory ----------------------------------------------------------
    def register(self, tenant_id: uuid.UUID, resource_type: str, resource_key: str, metadata: dict | None = None) -> ResourceInventory:
        existing = self.session.scalar(
            select(ResourceInventory).where(
                ResourceInventory.tenant_id == tenant_id,
                ResourceInventory.resource_type == resource_type,
                ResourceInventory.resource_key == resource_key,
            )
        )
        if existing is not None:
            return existing
        row = ResourceInventory(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_key=resource_key,
            status="AVAILABLE",
            extra_metadata=metadata or {},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def seed(self, tenant_id: uuid.UUID, resource_type: str, keys: Iterable[str], metadata: dict | None = None) -> int:
        count = 0
        for key in keys:
            self.register(tenant_id, resource_type, key, metadata)
            count += 1
        return count

    def capacity(self, tenant_id: uuid.UUID, resource_type: str | None = None) -> dict:
        stmt = select(ResourceInventory.status, ResourceInventory.resource_type).where(ResourceInventory.tenant_id == tenant_id)
        if resource_type:
            stmt = stmt.where(ResourceInventory.resource_type == resource_type)
        counts: dict[str, dict[str, int]] = {}
        for status, rtype in self.session.execute(stmt):
            bucket = counts.setdefault(rtype, {})
            bucket[status] = bucket.get(status, 0) + 1
        return counts

    # -- reservation --------------------------------------------------------
    def reserve(
        self,
        tenant_id: uuid.UUID,
        order_id: uuid.UUID,
        resource_type: str,
        count: int = 1,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        actor: str | None = None,
        reason: str | None = None,
        prefer_keys: list[str] | None = None,
    ) -> list[ResourceReservation]:
        """Atomically reserve `count` resources of type `resource_type`.

        Deterministic selection (ORDER BY resource_key) so concurrent callers
        converge; each row is claimed with compare-and-set."""
        if count < 1:
            raise ValueError("count must be >= 1")
        candidates = list(
            self.session.scalars(
                select(ResourceInventory)
                .where(
                    ResourceInventory.tenant_id == tenant_id,
                    ResourceInventory.resource_type == resource_type,
                    ResourceInventory.status == "AVAILABLE",
                )
                .order_by(ResourceInventory.resource_key)
            )
        )
        if prefer_keys:
            preferred = {c for c in candidates if c.resource_key in prefer_keys}
            candidates = list(preferred) + [c for c in candidates if c not in preferred]

        claimed: list[ResourceInventory] = []
        reservations: list[ResourceReservation] = []
        for candidate in candidates[: count * 2]:
            if len(claimed) >= count:
                break
            token = uuid.uuid4().hex
            result = self.session.execute(
                update(ResourceInventory)
                .where(ResourceInventory.id == candidate.id, ResourceInventory.status == "AVAILABLE")
                .values(status="RESERVED", reservation_token=token)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:
                continue  # lost the race; try next deterministic candidate
            reservation = ResourceReservation(
                tenant_id=tenant_id,
                resource_type=resource_type,
                resource_key=candidate.resource_key,
                order_id=order_id,
                reservation_token=token,
                state="RESERVED",
                expires_at=_now() + timedelta(seconds=ttl_seconds),
                reason=reason,
                actor=actor,
            )
            self.session.add(reservation)
            claimed.append(candidate)
            reservations.append(reservation)
            publish_outbox(
                self.session,
                "oss.resource.reserved.v1",
                {"resource_type": resource_type, "resource_key": candidate.resource_key, "reservation_token": token, "order_id": str(order_id)},
                tenant_id,
            )
        if len(reservations) < count:
            # Release any partial claim to keep the ledger clean.
            self._release_partial(claimed, reservations)
            raise ResourceUnavailable(f"only {len(reservations)} of {count} resources of type {resource_type} available")
        self.session.flush()
        return reservations

    def allocate(self, reservation_token: str) -> ResourceReservation:
        reservation = self._get(reservation_token)
        if reservation.state != "RESERVED":
            raise InvalidReservation(f"reservation {reservation_token} not in RESERVED state")
        reservation.state = "ALLOCATED"
        reservation.allocated_at = _now()
        self.session.execute(
            update(ResourceInventory)
            .where(ResourceInventory.reservation_token == reservation_token)
            .values(status="ALLOCATED")
            .execution_options(synchronize_session=False)
        )
        publish_outbox(self.session, "oss.resource.allocated.v1", {"reservation_token": reservation_token, "resource_key": reservation.resource_key, "resource_type": reservation.resource_type}, reservation.tenant_id)
        self.session.flush()
        return reservation

    def release(self, reservation_token: str, reason: str | None = None) -> ResourceReservation:
        reservation = self._get(reservation_token)
        reservation.state = "RELEASED"
        reservation.released_at = _now()
        self.session.execute(
            update(ResourceInventory)
            .where(ResourceInventory.reservation_token == reservation_token)
            .values(status="AVAILABLE", reservation_token=None)
            .execution_options(synchronize_session=False)
        )
        publish_outbox(self.session, "oss.resource.released.v1", {"reservation_token": reservation_token, "resource_key": reservation.resource_key, "resource_type": reservation.resource_type}, reservation.tenant_id)
        self.session.flush()
        return reservation

    def expire(self, reservation_token: str, reason: str = "reservation ttl expired") -> ResourceReservation:
        reservation = self._get(reservation_token)
        if reservation.state in ("RESERVED",):
            reservation.state = "RELEASED"
            reservation.released_at = _now()
            self.session.execute(
                update(ResourceInventory)
                .where(ResourceInventory.reservation_token == reservation_token)
                .values(status="AVAILABLE", reservation_token=None)
                .execution_options(synchronize_session=False)
            )
            publish_outbox(self.session, "oss.resource.expired.v1", {"reservation_token": reservation_token, "resource_key": reservation.resource_key}, reservation.tenant_id)
            self.session.flush()
        return reservation

    def expire_due(self, now: datetime | None = None) -> list[ResourceReservation]:
        now = now or _now()
        due = list(
            self.session.scalars(
                select(ResourceReservation).where(
                    ResourceReservation.state == "RESERVED",
                    ResourceReservation.expires_at.is_not(None),
                    ResourceReservation.expires_at <= now,
                )
            )
        )
        for reservation in due:
            self.expire(reservation.reservation_token)
        return due

    def for_order(self, order_id: uuid.UUID) -> list[ResourceReservation]:
        return list(self.session.scalars(select(ResourceReservation).where(ResourceReservation.order_id == order_id).order_by(ResourceReservation.reserved_at)))

    # -- helpers ------------------------------------------------------------
    def _get(self, reservation_token: str) -> ResourceReservation:
        reservation = self.session.scalar(select(ResourceReservation).where(ResourceReservation.reservation_token == reservation_token))
        if reservation is None:
            raise InvalidReservation(f"unknown reservation {reservation_token}")
        return reservation

    def _release_partial(self, claimed: list[ResourceInventory], reservations: list[ResourceReservation]) -> None:
        for reservation in reservations:
            self.session.execute(
                update(ResourceInventory)
                .where(ResourceInventory.reservation_token == reservation.reservation_token)
                .values(status="AVAILABLE", reservation_token=None)
                .execution_options(synchronize_session=False)
            )
            reservation.state = "RELEASED"
            reservation.released_at = _now()
