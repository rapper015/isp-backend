"""Geospatial helpers and maps-provider abstraction.

A maps provider computes distances and travel estimates. The default is a
deterministic FakeMapsProvider (haversine + fixed-speed estimate) so tests and
normal operation never depend on live map APIs or hardcoded credentials. If a
real provider is configured it is used; when unavailable, the deterministic
fallback is used WITHOUT pretending the estimate is accurate (the source is
recorded)."""
from __future__ import annotations

import math
import os
from typing import Any

from ..models import ServiceArea


def haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def point_in_polygon(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon. polygon = [[lat, lng], ...]."""
    if not polygon or len(polygon) < 3:
        return False
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        lat_i, lng_i = polygon[i]
        lat_j, lng_j = polygon[j]
        if ((lng_i > lng) != (lng_j > lng)) and (lat < (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i) + lat_i):
            inside = not inside
        j = i
    return inside


def point_in_bounds(lat: float, lng: float, bounds: dict) -> bool:
    return (bounds["min_lat"] <= lat <= bounds["max_lat"]) and (bounds["min_lng"] <= lng <= bounds["max_lng"])


def location_in_service_area(area: ServiceArea, lat: float, lng: float) -> bool:
    geometry = area.geometry or {}
    polygon = geometry.get("polygon") or []
    bounds = geometry.get("bounds")
    if polygon:
        return point_in_polygon(lat, lng, polygon)
    if bounds:
        return point_in_bounds(lat, lng, bounds)
    return False


class MapsProvider:
    name = "base"

    def travel_estimate(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> dict:
        """Return {'distance_m': float, 'duration_s': float, 'source': str}."""
        raise NotImplementedError

    def eta(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> dict:
        return self.travel_estimate(from_lat, from_lng, to_lat, to_lng)


class FakeMapsProvider(MapsProvider):
    """Deterministic fallback: haversine distance, fixed 30 km/h estimate."""

    name = "fake"

    def travel_estimate(self, from_lat, from_lng, to_lat, to_lng) -> dict:
        distance = haversine_distance_m(from_lat, from_lng, to_lat, to_lng)
        # 30 km/h urban average; clearly labeled as an estimate.
        duration = distance / (30.0 * 1000.0 / 3600.0)
        return {"distance_m": round(distance, 1), "duration_s": round(duration), "source": "fake-estimate"}


class GoogleMapsProvider(MapsProvider):
    name = "google"

    def travel_estimate(self, from_lat, from_lng, to_lat, to_lng) -> dict:
        import httpx

        key = os.getenv("GOOGLE_MAPS_API_KEY", "")
        if not key:
            return FakeMapsProvider().travel_estimate(from_lat, from_lng, to_lat, to_lng)
        url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        params = {
            "origins": f"{from_lat},{from_lng}",
            "destinations": f"{to_lat},{to_lng}",
            "key": key,
            "units": "metric",
        }
        try:
            response = httpx.get(url, params=params, timeout=3)
            data = response.json()
            element = data["rows"][0]["elements"][0]
            if element.get("status") == "OK":
                return {"distance_m": element["distance"]["value"],
                        "duration_s": element["duration"]["value"], "source": "google"}
        except Exception:  # noqa: BLE001 — never fail dispatch on maps outage
            pass
        return FakeMapsProvider().travel_estimate(from_lat, from_lng, to_lat, to_lng)


class AlternativeMapsProvider(MapsProvider):
    name = "alternative"

    def travel_estimate(self, from_lat, from_lng, to_lat, to_lng) -> dict:
        base = os.getenv("MAPS_PROVIDER_BASE_URL", "")
        if not base:
            return FakeMapsProvider().travel_estimate(from_lat, from_lng, to_lat, to_lng)
        import httpx

        try:
            response = httpx.get(f"{base}/route", params={"from": f"{from_lat},{from_lng}",
                                                          "to": f"{to_lat},{to_lng}"}, timeout=3)
            data = response.json()
            return {"distance_m": data["distance_m"], "duration_s": data["duration_s"], "source": "alternative"}
        except Exception:  # noqa: BLE001
            return FakeMapsProvider().travel_estimate(from_lat, from_lng, to_lat, to_lng)


def get_maps_provider() -> MapsProvider:
    name = os.getenv("MAPS_PROVIDER", "fake")
    providers: dict[str, MapsProvider] = {
        "fake": FakeMapsProvider(),
        "google": GoogleMapsProvider(),
        "alternative": AlternativeMapsProvider(),
    }
    return providers.get(name, FakeMapsProvider())
