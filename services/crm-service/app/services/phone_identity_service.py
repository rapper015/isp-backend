"""Database-backed, CRM-wide phone-number uniqueness."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PhoneIdentity
from ..validation import normalize_phone


class PhoneConflict(ValueError):
    pass


def replace_owner_numbers(
    session: Session,
    owner_type: str,
    owner_id: UUID,
    values: dict[str, str | None],
) -> None:
    """Atomically replace every registered number for one domain owner.

    Normalization makes formatting irrelevant. The primary key on
    ``normalized_phone`` is the final race-safe uniqueness guard.
    """
    wanted: dict[str, str] = {}
    for field_name, raw in values.items():
        normalized = normalize_phone(raw)
        if normalized:
            previous_field = wanted.get(normalized)
            if previous_field and previous_field != field_name:
                raise PhoneConflict(f"{field_name} duplicates {previous_field}")
            wanted[normalized] = field_name

    existing = list(session.scalars(select(PhoneIdentity).where(
        PhoneIdentity.owner_type == owner_type,
        PhoneIdentity.owner_id == owner_id,
    )))
    existing_by_number = {row.normalized_phone: row for row in existing}

    for normalized, field_name in wanted.items():
        claimed = session.get(PhoneIdentity, normalized)
        if claimed and (claimed.owner_type != owner_type or claimed.owner_id != owner_id):
            raise PhoneConflict(f"{field_name} is already used by another record")

    for normalized, row in existing_by_number.items():
        if normalized not in wanted:
            session.delete(row)
    for normalized, field_name in wanted.items():
        row = existing_by_number.get(normalized)
        if row:
            row.field_name = field_name
        else:
            session.add(PhoneIdentity(
                normalized_phone=normalized,
                owner_type=owner_type,
                owner_id=owner_id,
                field_name=field_name,
            ))


def profile_numbers(profile: dict | None) -> dict[str, str | None]:
    profile = profile or {}
    return {
        field: profile.get(field)
        for field in ("mobile", "landline", "whatsapp")
    }
