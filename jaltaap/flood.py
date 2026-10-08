"""River flood risk from GloFAS (via the free Open-Meteo Flood API).

Idea: every river cell gets its OWN thresholds from its own history
(95th / 99th percentile of daily discharge), so the same code works for
the Brahmaputra, the Periyar or the Godavari.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .http import get_json

FLOOD_API = "https://flood-api.open-meteo.com/v1/flood"
HIST_START = "1984-01-01"


# ---------- finding the right river ----------

def snap_to_river(lat: float, lon: float, radius: float = 0.1, step: float = 0.05) -> dict:
    """GloFAS snaps to the nearest river cell, which is often a tiny stream.
    We sample a small grid around the point (one multi-point API call) and
    pick the cell with the biggest recent flow = the main river nearby."""
    offs = np.arange(-radius, radius + 1e-9, step)
    lats, lons = [], []
    for a in offs:
        for b in offs:
            lats.append(round(lat + a, 4))
            lons.append(round(lon + b, 4))
    data = get_json(FLOOD_API, {
        "latitude": ",".join(map(str, lats)),
        "longitude": ",".join(map(str, lons)),
        "daily": "river_discharge",
        "past_days": 60, "forecast_days": 1,
    })
    if isinstance(data, dict):
        data = [data]
    best = None
    for d in data:
        vals = [v for v in d["daily"]["river_discharge"] if v is not None]
        if not vals:
            continue
        mean = float(np.mean(vals))
        if best is None or mean > best["mean_recent"]:
            best = {"lat": d["latitude"], "lon": d["longitude"], "mean_recent": mean}
    if best is None:
        raise ValueError("no modelled river near this point")
    best["distance_km"] = round(_haversine(lat, lon, best["lat"], best["lon"]), 1)
    return best


# ---------- history + thresholds ----------

def history(lat: float, lon: float, end: str | None = None) -> pd.Series:
    """Daily discharge (m3/s) from 1984 (where available) up to `end`."""
    end = end or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    d = get_json(FLOOD_API, {
        "latitude": lat, "longitude": lon, "daily": "river_discharge",
        "start_date": HIST_START, "end_date": end,
    }, long_cache=True)["daily"]
    s = pd.Series(d["river_discharge"], index=pd.to_datetime(d["time"]), dtype="float").dropna()
    return s


def thresholds(series: pd.Series, before: str | None = None) -> dict:
    """p50/p95/p99 of daily flow. `before` keeps the test honest in replays
    (only use data the system would have had at that time)."""
    s = series[series.index < pd.Timestamp(before)] if before else series
    return {
        "p50": float(s.quantile(0.50)),
        "p95": float(s.quantile(0.95)),
        "p99": float(s.quantile(0.99)),
        "years": round(len(s) / 365.25, 1),
    }


def level(value: float, th: dict) -> str:
    if value >= th["p99"]:
        return "red"
    if value >= th["p95"]:
        return "orange"
    return "green"


# ---------- live forecast with ensemble ----------

def forecast(lat: float, lon: float, days: int = 30) -> pd.DataFrame:
    """GloFAS forecast incl. all ensemble members (~50 runs)."""
    d = get_json(FLOOD_API, {
        "latitude": lat, "longitude": lon, "daily": "river_discharge",
        "ensemble": "true", "forecast_days": days, "past_days": 7,
    })["daily"]
    df = pd.DataFrame(d)
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


def flood_risk(lat: float, lon: float, days: int = 15) -> dict:
    """Everything the app/agent needs about river flood risk at a point."""
    cell = snap_to_river(lat, lon)
    hist = history(cell["lat"], cell["lon"])
    th = thresholds(hist)
    fc = forecast(cell["lat"], cell["lon"], days=max(days, 7))
    members = fc[[c for c in fc.columns if c.startswith("river_discharge_member")]]
    main = fc["river_discharge"]
    today = pd.Timestamp(dt.date.today())
    fut = fc.index >= today

    # share of ensemble runs that cross each threshold at any point ahead
    ahead = members[fut]
    p_orange = float((ahead.max() >= th["p95"]).mean()) if not ahead.empty else 0.0
    p_red = float((ahead.max() >= th["p99"]).mean()) if not ahead.empty else 0.0

    med = ahead.median(axis=1) if not ahead.empty else main[fut]
    peak_day = med.idxmax()
    now_val = float(main[main.index <= today].iloc[-1]) if (main.index <= today).any() else float(main.iloc[0])

    first_cross = None
    for t, v in med.items():
        if v >= th["p95"]:
            first_cross = t
            break

    return {
        "river_cell": cell,
        "now_m3s": round(now_val),
        "thresholds": {k: round(v) if k != "years" else v for k, v in th.items()},
        "level_now": level(now_val, th),
        "peak_m3s": round(float(med.max())),
        "peak_date": peak_day.date().isoformat(),
        "level_peak": level(float(med.max()), th),
        "days_to_orange": (first_cross - today).days if first_cross is not None else None,
        "prob_orange": round(p_orange, 2),
        "prob_red": round(p_red, 2),
        "ensemble_runs": int(members.shape[1]),
        "trend": "rising" if med.iloc[min(3, len(med) - 1)] > now_val * 1.05
                 else "falling" if med.iloc[min(3, len(med) - 1)] < now_val * 0.95 else "steady",
        "source": "GloFAS v4 via Open-Meteo (modelled, not gauge-measured)",
    }


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(a)))
