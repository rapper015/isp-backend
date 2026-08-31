"""Feature store: compute offline features from analytical records with
point-in-time correctness; refresh online features."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.features import apply_transform, has_transform
from ..domain.statistics import clamp
from ..models import AnalyticalRecord, FeatureDefinition, FeatureValue, OnlineFeatureValue


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _feature_rows(session: Session, tenant_id, entity_type: str, entity_ref: str,
                  as_of: datetime, lookback_days: int = 90) -> list[AnalyticalRecord]:
    since = as_of - timedelta(days=lookback_days)
    return list(session.scalars(select(AnalyticalRecord).where(
        AnalyticalRecord.tenant_id == tenant_id,
        AnalyticalRecord.entity_type == entity_type,
        AnalyticalRecord.entity_ref == entity_ref,
        AnalyticalRecord.event_time <= as_of,
        AnalyticalRecord.event_time >= since).order_by(AnalyticalRecord.event_time)))


def compute_features(session: Session, *, tenant_id, entity_type: str, entity_ref: str,
                     as_of: datetime | None = None, feature_names: list[str] | None = None,
                     version: str = "v1") -> dict:
    """Point-in-time correct feature computation: only records observed at or
    before `as_of` are used. Prevents label/feature leakage."""
    as_of = as_of or _now()
    rows = _feature_rows(session, tenant_id, entity_type, entity_ref, as_of)
    records = [dict(r.normalized, contract=r.contract, event_time=r.event_time) for r in rows]
    names = feature_names or [f.name for f in session.scalars(
        select(FeatureDefinition).where(FeatureDefinition.is_active.is_(True)))]
    out: dict = {}
    for name in names:
        if not has_transform(name, version):
            continue
        value = apply_transform(name, version, records)
        if value is not None:
            out[name] = value
    return out


def store_feature_values(session: Session, *, tenant_id, entity_type: str, entity_ref: str,
                         values: dict, as_of: datetime | None = None, version: str = "v1") -> int:
    as_of = as_of or _now()
    count = 0
    for name, value in values.items():
        numeric = None
        str_value = None
        if isinstance(value, bool):
            numeric = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            numeric = float(value)
        else:
            str_value = str(value)
        row = FeatureValue(tenant_id=tenant_id, entity_ref=entity_ref, feature_name=name,
                           version=version, value=numeric, str_value=str_value,
                           event_time=as_of, processing_time=_now(), quality="FRESH")
        session.add(row)
        online = session.scalars(select(OnlineFeatureValue).where(
            OnlineFeatureValue.tenant_id == tenant_id,
            OnlineFeatureValue.entity_ref == entity_ref,
            OnlineFeatureValue.feature_name == name)).first()
        if online is None:
            online = OnlineFeatureValue(tenant_id=tenant_id, entity_ref=entity_ref,
                                        feature_name=name)
            session.add(online)
        online.value = numeric
        online.str_value = str_value
        online.version = version
        online.computed_at = _now()
        online.quality = "FRESH"
        count += 1
    session.flush()
    return count


def online_feature_vector(session: Session, tenant_id, entity_ref: str,
                          feature_names: list[str]) -> dict:
    rows = session.scalars(select(OnlineFeatureValue).where(
        OnlineFeatureValue.tenant_id == tenant_id,
        OnlineFeatureValue.entity_ref == entity_ref,
        OnlineFeatureValue.feature_name.in_(feature_names))).all()
    vector = {}
    by_name = {r.feature_name: r for r in rows}
    for name in feature_names:
        row = by_name.get(name)
        if row is not None and row.value is not None:
            vector[name] = row.value
        else:
            vector[name] = None  # missing feature handled by model default
    return vector


def mark_stale_features(session: Session, tenant_id, *, max_age_seconds: int = 86400) -> int:
    cutoff = _now() - timedelta(seconds=max_age_seconds)
    rows = list(session.scalars(select(OnlineFeatureValue).where(
        OnlineFeatureValue.tenant_id == tenant_id,
        OnlineFeatureValue.computed_at < cutoff)))
    for row in rows:
        row.quality = "STALE"
    session.flush()
    return len(rows)


def apply_missing_defaults(vector: dict, definitions: list[FeatureDefinition]) -> dict:
    """Fill missing features with each definition's default_value."""
    out = dict(vector)
    for definition in definitions:
        if definition.name not in out or out[definition.name] is None:
            default = definition.default_value
            if default is None:
                default = definition.valid_range.get("min", 0.0) if isinstance(
                    definition.valid_range, dict) else 0.0
            out[definition.name] = clamp(float(default))
    return out
