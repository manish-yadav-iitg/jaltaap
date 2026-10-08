"""Google Flood Hub as a second opinion, through Google's official Flood Forecasting API.

- Free, data is CC BY 4.0 (we show "Flood forecasts: Google Flood Hub").
- Needs an API key: join the waitlist at
  https://developers.google.com/flood-forecasting  (Google emails you when approved),
  enable the API in your Google Cloud project, and put the key in .env as
  GOOGLE_FLOODHUB_API_KEY=...
- Without a key, everything else in JalTaap still works, and we still give a
  "see this spot on Flood Hub" link.

Docs: https://developers.google.com/flood-forecasting/rest
"""
from __future__ import annotations

import os
import re
import threading
import time

import numpy as np
import requests

BASE = "https://floodforecasting.googleapis.com/v1"
ATTRIBUTION = "Flood forecasts: Google Flood Hub (CC BY 4.0)"

# Google severity -> our levels
SEVERITY = {
    "EXTREME": ("red", "Extreme danger"),
    "SEVERE": ("red", "Danger"),
    "ABOVE_NORMAL": ("orange", "Warning"),
    "NO_FLOODING": ("green", "No flooding"),
    "UNKNOWN": ("unknown", "Unknown"),
    "SEVERITY_UNSPECIFIED": ("unknown", "Unknown"),
}
TREND = {"RISE": "rising", "FALL": "falling", "NO_CHANGE": "steady"}

_cache = {"t": 0.0, "data": None}
_lock = threading.Lock()
_TTL = 60 * 60  # Flood Hub updates a few times a day


def api_key() -> str | None:
    return os.environ.get("GOOGLE_FLOODHUB_API_KEY") or None


def enabled() -> bool:
    return api_key() is not None


def hub_link(lat: float, lon: float, zoom: int = 10) -> str:
    """Opens Google Flood Hub centred on this spot (works without a key)."""
    return f"https://sites.research.google/floods/l/{lat:.4f}/{lon:.4f}/{zoom}"


def _post(path: str, body: dict) -> dict:
    r = requests.post(f"{BASE}/{path}", params={"key": api_key()}, json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def _get(path: str, params: dict | list | None = None) -> dict:
    p = list(params.items()) if isinstance(params, dict) else list(params or [])
    p.append(("key", api_key()))
    r = requests.get(f"{BASE}/{path}", params=p, timeout=60)
    r.raise_for_status()
    return r.json()


def _clean(st: dict) -> dict:
    lvl, word = SEVERITY.get(st.get("severity", "UNKNOWN"), ("unknown", "Unknown"))
    loc = st.get("gaugeLocation") or {}
    change = (st.get("forecastChange") or {}).get("valueChange") or {}
    maps = ((st.get("inundationMapSet") or {}).get("inundationMaps")) or []
    return {
        "gauge_id": st.get("gaugeId"),
        "lat": loc.get("latitude"), "lon": loc.get("longitude"),
        "level": lvl, "severity": word,
        "trend": TREND.get(st.get("forecastTrend"), None),
        "issued": st.get("issuedTime"),
        "forecast_from": (st.get("forecastTimeRange") or {}).get("start"),
        "forecast_to": (st.get("forecastTimeRange") or {}).get("end"),
        "change_m": [change.get("lowerBound"), change.get("upperBound")] if change else None,
        "verified": bool(st.get("qualityVerified")),
        "flood_area_ids": {m.get("level", "").lower(): m.get("serializedPolygonId") for m in maps
                           if m.get("serializedPolygonId")},
        "alert_area_id": st.get("serializedNotificationPolygonId"),
    }


def india_statuses(force: bool = False) -> list[dict]:
    """Latest Flood Hub status for every gauge in India (cached 1 hour)."""
    if not enabled():
        return []
    with _lock:
        if not force and _cache["data"] is not None and time.time() - _cache["t"] < _TTL:
            return _cache["data"]
    out, token = [], None
    while True:
        body = {"regionCode": "IN", "pageSize": 5000}
        if token:
            body["pageToken"] = token
        d = _post("floodStatus:searchLatestFloodStatusByArea", body)
        out += [_clean(s) for s in d.get("floodStatuses", [])]
        token = d.get("nextPageToken")
        if not token:
            break
    out = [s for s in out if s["lat"] is not None]
    _add_names([s for s in out if s["level"] in ("orange", "red")])
    with _lock:
        _cache.update(t=time.time(), data=out)
    return out


def _add_names(statuses: list[dict]):
    """River + site names for the gauges that matter (the ones in warning)."""
    ids = [s["gauge_id"] for s in statuses][:500]
    if not ids:
        return
    try:
        d = _get("gauges:batchGet", [("names", f"gauges/{g}") for g in ids])
        info = {g.get("gaugeId"): g for g in d.get("gauges", [])}
        for s in statuses:
            g = info.get(s["gauge_id"], {})
            s["river"], s["site"] = g.get("river") or None, g.get("siteName") or None
    except Exception:
        pass


def nearest(lat: float, lon: float, km: float = 30) -> dict | None:
    """Closest Flood Hub gauge to a spot, if any within `km`."""
    sts = india_statuses()
    if not sts:
        return None
    la = np.radians([s["lat"] for s in sts])
    lo = np.radians([s["lon"] for s in sts])
    p1 = np.radians(lat)
    a = np.sin((la - p1) / 2) ** 2 + np.cos(p1) * np.cos(la) * np.sin((lo - np.radians(lon)) / 2) ** 2
    d = 2 * 6371 * np.arcsin(np.sqrt(a))
    i = int(np.argmin(d))
    if d[i] > km:
        return None
    return {**sts[i], "distance_km": round(float(d[i]), 1)}


def polygon_geojson(polygon_id: str) -> dict:
    """Flood Hub flood-extent / alert-area polygon (KML) -> GeoJSON for the map."""
    d = _get(f"serializedPolygons/{polygon_id}")
    return kml_to_geojson(d.get("kml", ""))


def kml_to_geojson(kml: str) -> dict:
    polys = []
    for poly in re.findall(r"<Polygon>(.*?)</Polygon>", kml, flags=re.S):
        rings = []
        for coords in re.findall(r"<coordinates>(.*?)</coordinates>", poly, flags=re.S):
            ring = []
            for pt in coords.split():
                parts = pt.split(",")
                if len(parts) >= 2:
                    ring.append([float(parts[0]), float(parts[1])])
            if len(ring) >= 3:
                rings.append(ring)
        if rings:
            polys.append(rings)
    return {"type": "Feature", "properties": {"source": ATTRIBUTION},
            "geometry": {"type": "MultiPolygon", "coordinates": polys}}


def compare(ours: str, google: str) -> str:
    """One line on whether JalTaap and Google agree."""
    if google == "unknown":
        return "Google has no clear reading here"
    rank = {"green": 0, "watch": 0, "orange": 1, "red": 2}
    a, b = rank.get(ours, 0), rank.get(google, 0)
    if a == b:
        return "JalTaap and Google Flood Hub agree"
    return "Google Flood Hub is more worried than JalTaap" if b > a else "JalTaap is more worried than Google Flood Hub"
