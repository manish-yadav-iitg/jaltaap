"""Street waterlogging: will THIS spot fill with water, and why?

Rivers are only half the story in Indian cities. The other half is the
underpass, the low junction or the colony far from a working drain that
turns into a lake after one heavy shower. For any point we combine:

  terrain   Copernicus 90 m DEM (Open-Meteo elevation API), 7x7 grid ~1 km across:
            is it lower than its surroundings? flat? a bowl?
  drains    OpenStreetMap drains / canals / nalas / rivers (Overpass API): how far to the nearest?
  hotspots  underpasses and road tunnels nearby (classic ponding spots)
  rain      last 72 h (wet ground) + next 24 h + the worst single hour (Open-Meteo)

Each factor adds log-odds to a transparent logistic score, so the app can
show exactly why a spot is flagged ("a low point, 600 m from the nearest
drain, 90 mm expected tomorrow"). The weights are hand-set from published
urban-flood studies, not trained on local labels, so treat the result as a
ranked heads-up, not a measured probability.

It also ranks the critical places nearby (hospitals, schools, fire and
police stations, rail stations) by how badly flooding there would hurt,
and turns the level into standing instructions for each municipal team.
"""
from __future__ import annotations

import math

import numpy as np

from .http import get_json

ELEV_API = "https://api.open-meteo.com/v1/elevation"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
OVERPASS = "https://overpass-api.de/api/interpreter"

INTERCEPT = -3.4
FACTORS = {  # key: (coefficient, label)
    "low":      (1.10, "Lower than the ground around it"),
    "flat":     (0.55, "Very flat, water has nowhere to run"),
    "bowl":     (0.60, "A bowl: most neighbouring ground is higher"),
    "drain":    (0.80, "Far from any mapped drain or canal"),
    "underpass": (0.70, "Underpass or road tunnel close by"),
    "wet":      (0.55, "Ground already soaked by recent rain"),
    "rain":     (1.30, "Heavy rain expected in the next 24 h"),
    "burst":    (0.75, "Short, intense downpour expected"),
}
WEIGHTS = {"hospital": 1.0, "fire_station": 0.9, "station": 0.8, "school": 0.7, "clinic": 0.6, "police": 0.6,
           "college": 0.5, "bus_station": 0.5}
BUFFER_M = {"hospital": 500, "school": 500, "college": 500, "clinic": 400, "fire_station": 500, "police": 400,
            "station": 400, "bus_station": 350}

DIRECTIVES = {
    "green": {
        "Ward engineer": "Desilt drains and clear grates before the next spell; check pump sets run.",
        "Traffic police": "No action. Keep the diversion plan for known underpasses ready.",
        "Hospitals & schools": "Routine. Confirm generator fuel and a ground-floor move plan exist.",
        "Residents": "Keep drains outside your home clear of plastic and debris.",
    },
    "orange": {
        "Ward engineer": "Station mobile pumps at the flagged spots before the rain. Open sluice gates early. Put a crew on call.",
        "Traffic police": "Barricades ready at underpasses; divert heavy vehicles if water passes 15 cm.",
        "Hospitals & schools": "Move records and equipment off the ground floor. Schools: plan early dismissal.",
        "Residents": "Park vehicles on higher ground. Avoid underpasses. Keep phones charged.",
    },
    "red": {
        "Ward engineer": "Deploy every pump and suction truck to the ranked spots. Clear outfalls continuously. Report blocked drains live.",
        "Traffic police": "Close flagged underpasses now. Keep a green corridor open to the nearest hospital.",
        "Hospitals & schools": "Hospitals: activate flood plan, shift critical patients up, stock 72 h of supplies. Schools: close.",
        "Residents": "Don't walk or drive through moving water. Switch off power if water enters. Report flooding in the app.",
    },
}


def _hav_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def terrain(lat: float, lon: float, n: int = 7, step: float = 0.0015) -> dict:
    offs = (np.arange(n) - n // 2) * step
    pts = [(lat + a, lon + b) for a in offs for b in offs]
    d = get_json(ELEV_API, {"latitude": ",".join(f"{p[0]:.5f}" for p in pts),
                            "longitude": ",".join(f"{p[1]:.5f}" for p in pts)}, long_cache=True)
    z = np.array(d["elevation"], dtype=float).reshape(n, n)
    c = n // 2
    centre = z[c, c]
    ring = np.delete(z.flatten(), c * n + c)
    tpi = centre - float(np.mean(ring))  # negative = lower than surroundings
    dx = step * 111_000 * math.cos(math.radians(lat))
    dy = step * 111_000
    gx = (z[c, c + 1] - z[c, c - 1]) / (2 * dx)
    gy = (z[c + 1, c] - z[c - 1, c]) / (2 * dy)
    slope_pct = float(math.hypot(gx, gy) * 100)
    near = z[c - 1:c + 2, c - 1:c + 2].flatten()
    higher = float((np.delete(near, 4) > centre).mean())
    return {"elevation_m": round(float(centre), 1), "tpi_m": round(tpi, 2), "slope_pct": round(slope_pct, 2),
            "share_higher": round(higher, 2), "relief_m": round(float(z.max() - z.min()), 1)}


def osm_context(lat: float, lon: float, radius: int = 1500) -> dict:
    q = f"""[out:json][timeout:25];
(way["waterway"~"drain|canal|ditch|river|stream"](around:{radius},{lat},{lon}););
out geom;
(nwr["amenity"~"^(hospital|clinic|school|college|fire_station|police|bus_station)$"](around:{radius},{lat},{lon});
 nwr["railway"="station"](around:{radius},{lat},{lon});
 way["highway"]["tunnel"="yes"](around:{radius},{lat},{lon});
 way["highway"]["layer"~"^-"](around:{radius},{lat},{lon}););
out center tags;"""
    import requests
    r = requests.post(OVERPASS, data={"data": q}, timeout=40, headers={"User-Agent": "JalTaap/2 (hackathon)"})
    r.raise_for_status()
    els = r.json().get("elements", [])
    best_drain, drain_kind = None, None
    places, underpasses = [], []
    for e in els:
        tags = e.get("tags", {})
        if "waterway" in tags and "geometry" in e:
            for g in e["geometry"]:
                dd = _hav_m(lat, lon, g["lat"], g["lon"])
                if best_drain is None or dd < best_drain:
                    best_drain, drain_kind = dd, tags["waterway"]
            continue
        c = e.get("center") or ({"lat": e["lat"], "lon": e["lon"]} if "lat" in e else None)
        if not c:
            continue
        dist = _hav_m(lat, lon, c["lat"], c["lon"])
        if "highway" in tags:
            underpasses.append({"name": tags.get("name") or "Underpass", "lat": c["lat"], "lon": c["lon"],
                                "distance_m": round(dist)})
            continue
        kind = tags.get("amenity") or ("station" if tags.get("railway") == "station" else None)
        if kind:
            places.append({"name": tags.get("name") or kind.replace("_", " ").title(), "kind": kind,
                           "lat": c["lat"], "lon": c["lon"], "distance_m": round(dist)})
    underpasses.sort(key=lambda x: x["distance_m"])
    return {"drain_m": None if best_drain is None else round(best_drain), "drain_kind": drain_kind,
            "places": places, "underpasses": underpasses[:8]}


def rain_window(lat: float, lon: float) -> dict:
    d = get_json(FORECAST_API, {"latitude": lat, "longitude": lon, "hourly": "precipitation",
                                "past_days": 3, "forecast_days": 3, "timezone": "Asia/Kolkata"})
    import pandas as pd
    s = pd.Series(d["hourly"]["precipitation"], index=pd.to_datetime(d["hourly"]["time"])).fillna(0)
    now = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).floor("h")
    past, fut = s[s.index < now], s[s.index >= now]
    nxt6 = fut.iloc[:6]
    return {"past72_mm": round(float(past.iloc[-72:].sum()), 1), "next24_mm": round(float(fut.iloc[:24].sum()), 1),
            "next72_mm": round(float(fut.sum()), 1), "max_hour_mm": round(float(fut.iloc[:24].max() if len(fut) else 0), 1),
            "next6_max_mm": round(float(nxt6.max() if len(nxt6) else 0), 1),
            "next6_peak_at": nxt6.idxmax().strftime("%H:00") if len(nxt6) and nxt6.max() > 0 else None}


def features(t: dict, o: dict, r: dict) -> dict:
    drain = o.get("drain_m")
    up = o.get("underpasses") or []
    return {
        "low": float(np.clip(-t["tpi_m"] / 2.0, -0.5, 2.0)),
        "flat": float(np.clip(1 - t["slope_pct"] / 1.5, 0, 1)),
        "bowl": float(np.clip((t["share_higher"] - 0.5) * 2, 0, 1)),
        "drain": 1.0 if drain is None else float(np.clip((drain - 150) / 600, 0, 1)),
        "underpass": 1.0 if up and up[0]["distance_m"] <= 300 else 0.5 if up and up[0]["distance_m"] <= 700 else 0.0,
        "wet": float(np.clip(r["past72_mm"] / 50, 0, 2)),
        "rain": float(np.clip(r["next24_mm"] / 64.5, 0, 3)),
        "burst": float(np.clip(r["max_hour_mm"] / 25, 0, 2)),
    }


def score(f: dict, with_rain: bool = True) -> tuple[float, list[dict]]:
    z = INTERCEPT
    parts = []
    for k, (coef, label) in FACTORS.items():
        if not with_rain and k in ("wet", "rain", "burst"):
            continue
        pts = coef * f[k]
        z += pts
        parts.append({"key": k, "label": label, "logodds": round(pts, 2), "value": round(f[k], 2)})
    p = 1 / (1 + math.exp(-z))
    return p, sorted(parts, key=lambda x: -x["logodds"])


def level_of(p: float) -> str:
    return "red" if p >= 0.55 else "orange" if p >= 0.28 else "green"


def flash(r: dict) -> dict:
    """Tier-1 instant alert, no model needed: an hour of >= 30 mm in the next 6 h (IMD cloudburst is 100 mm/h)."""
    mh = r["next6_max_mm"]
    if mh >= 30:
        return {"on": True, "level": "red", "text": f"Downpour of {mh} mm in one hour expected around {r['next6_peak_at']}. "
                                                    "Streets can flood within minutes."}
    if mh >= 15:
        return {"on": True, "level": "orange", "text": f"Heavy burst ({mh} mm/h) likely around {r['next6_peak_at']}. Avoid underpasses."}
    return {"on": False, "level": "green", "text": ""}


def impact(p: float, places: list[dict]) -> list[dict]:
    out = []
    for pl in places:
        R = BUFFER_M.get(pl["kind"], 400)
        if pl["distance_m"] > R:
            continue
        w = WEIGHTS.get(pl["kind"], 0.4)
        s = p * w * (1 - 0.5 * pl["distance_m"] / R)
        out.append({**pl, "impact": round(100 * s)})
    return sorted(out, key=lambda x: -x["impact"])[:10]


def waterlogging(lat: float, lon: float) -> dict:
    errors = {}
    try:
        t = terrain(lat, lon)
    except Exception as e:
        errors["terrain"] = str(e)
        t = {"elevation_m": None, "tpi_m": 0.0, "slope_pct": 1.0, "share_higher": 0.5, "relief_m": None}
    try:
        o = osm_context(lat, lon)
    except Exception as e:
        errors["osm"] = str(e)
        o = {"drain_m": None, "drain_kind": None, "places": [], "underpasses": []}
    r = rain_window(lat, lon)
    f = features(t, o, r)
    if "osm" in errors:
        f["drain"], f["underpass"] = 0.5, 0.0  # unknown, stay neutral
    p, parts = score(f)
    p0, _ = score(f, with_rain=False)
    lv = level_of(p)
    return {"probability": round(p, 2), "baseline": round(p0, 2), "level": lv, "why": parts,
            "terrain": t, "drain_m": o["drain_m"], "drain_kind": o["drain_kind"], "underpasses": o["underpasses"],
            "rain": r, "flash": flash(r), "impact": impact(p, o["places"]), "directives": DIRECTIVES[lv],
            "horizons": [
                {"range": "0-3 days", "what": "Spot warning (this card)", "confidence": "good"},
                {"range": "3-10 days", "what": "City heads-up from the river and rain forecast", "confidence": "fair"},
                {"range": "2-4 weeks", "what": "Only 'wetter or drier than usual' for the region", "confidence": "low"}],
            "errors": errors,
            "source": "Copernicus DEM (Open-Meteo), OpenStreetMap (Overpass), Open-Meteo rain; explainable logistic score"}
