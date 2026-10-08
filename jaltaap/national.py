"""National board: every watched river point and city in India in 2 API calls.

Thresholds were precomputed by scripts/build_stations.py, so this is fast.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from .heat import WETBULB_CAUTION, WETBULB_DANGER
from .http import get_json

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
FLOOD_API = "https://flood-api.open-meteo.com/v1/flood"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def river_board(days: int = 15) -> list[dict]:
    st = _load("stations.json")
    data = get_json(FLOOD_API, {
        "latitude": ",".join(str(s["lat"]) for s in st),
        "longitude": ",".join(str(s["lon"]) for s in st),
        "daily": "river_discharge,river_discharge_median,river_discharge_max,river_discharge_p75",
        "forecast_days": days, "past_days": 1,
    })
    if isinstance(data, dict):
        data = [data]
    today = dt.date.today().isoformat()
    out = []
    for s, d in zip(st, data):
        dd = d["daily"]
        idx = [i for i, t in enumerate(dd["time"]) if t >= today]
        med = [dd["river_discharge_median"][i] or 0 for i in idx]
        mx = [dd["river_discharge_max"][i] or 0 for i in idx]
        now = next((v for v in reversed(dd["river_discharge"][:idx[0] + 1]) if v is not None), med[0] if med else 0)
        peak_i = int(np.argmax(med)) if med else 0
        peak = med[peak_i] if med else 0
        level = "red" if peak >= s["p99"] else "orange" if peak >= s["p95"] else \
            "watch" if (max(mx) if mx else 0) >= s["p95"] else "green"
        out.append({**s, "now": round(now), "peak": round(peak),
                    "peak_date": dd["time"][idx[peak_i]] if idx else today,
                    "ratio": round(peak / s["p95"], 2) if s["p95"] else 0,
                    "level": level, "series": [round(v) for v in med]})
    return out


def heat_board(days: int = 7) -> list[dict]:
    cities = _load("heat_cities.json")
    data = get_json(FORECAST_API, {
        "latitude": ",".join(str(c["lat"]) for c in cities),
        "longitude": ",".join(str(c["lon"]) for c in cities),
        "daily": "temperature_2m_max,apparent_temperature_max",
        "hourly": "wet_bulb_temperature_2m,temperature_2m,relative_humidity_2m,wind_speed_10m,"
                  "shortwave_radiation,direct_radiation,surface_pressure",
        "wind_speed_unit": "ms", "forecast_days": days, "timezone": "Asia/Kolkata",
    })
    if isinstance(data, dict):
        data = [data]
    out = []
    for c, d in zip(cities, data):
        daily = d["daily"]
        hourly = pd.Series(d["hourly"]["wet_bulb_temperature_2m"],
                           index=pd.to_datetime(d["hourly"]["time"])).resample("D").max()
        best = None
        for i, t in enumerate(daily["time"]):
            tmax, feels = daily["temperature_2m_max"][i], daily["apparent_temperature_max"][i]
            if tmax is None or feels is None:
                continue
            wb = hourly.get(pd.Timestamp(t))
            wb = None if wb is None or np.isnan(wb) else round(float(wb), 1)
            normal = c["normal_tmax_by_month"][int(t[5:7]) - 1]
            imd = tmax >= 45 or (tmax >= 40 and tmax - normal >= 4.5)
            if feels >= c["feels_p99"] or (wb or 0) >= WETBULB_DANGER or tmax >= 45:
                lv = "red"
            elif feels >= c["feels_p95"] or (wb or 0) >= WETBULB_CAUTION or imd:
                lv = "orange"
            else:
                lv = "green"
            rank = {"green": 0, "orange": 1, "red": 2}[lv]
            row = {"date": t, "tmax": round(tmax, 1), "feels": round(feels, 1), "wetbulb": wb,
                   "imd_heatwave": imd, "level": lv}
            if best is None or (rank, feels) > ({"green": 0, "orange": 1, "red": 2}[best["level"]], best["feels"]):
                best = row
        out.append({"city": c["city"], "lat": c["lat"], "lon": c["lon"],
                    "feels_p95": c["feels_p95"], "feels_p99": c["feels_p99"], **(best or {"level": "green"}),
                    **_work_today(d["hourly"], c["lat"], c["lon"])})
    return out


def _work_today(hourly: dict, lat: float, lon: float) -> dict:
    """Max WBGT between 6 am and 8 pm today (local) -> outdoor work danger."""
    try:
        import thermofeel as tf
        from .heatstress import band, cos_solar_zenith
        h = pd.DataFrame(hourly).dropna()
        local = pd.to_datetime(h["time"]).dt.tz_localize("Asia/Kolkata")
        today = pd.Timestamp.now(tz="Asia/Kolkata").normalize()
        m = ((local >= today) & (local < today + pd.Timedelta(days=1)) &
             (local.dt.hour >= 6) & (local.dt.hour <= 20)).values
        if not m.any():
            return {}
        h, local = h[m], local[m]
        sw = np.maximum(h["shortwave_radiation"].values, 0)
        fdir = np.where(sw > 1, np.clip(h["direct_radiation"].values / np.maximum(sw, 1), 0, 1), 0)
        cz = cos_solar_zenith(pd.DatetimeIndex(local.dt.tz_convert("UTC")), lat, lon)
        w = tf.calculate_wbgt_liljegren(h["temperature_2m"].values + 273.15, h["relative_humidity_2m"].values,
                                        h["surface_pressure"].values, np.maximum(h["wind_speed_10m"].values, 0.5),
                                        sw, fdir, cz) - 273.15
        i = int(np.nanargmax(w))
        label, level, rest = band(float(w[i]))
        return {"wbgt_today": round(float(w[i]), 1), "work_label": label, "work_level": level,
                "work_worst_hour": int(local.iloc[i].hour), "work_heavy": rest[2]}
    except Exception:
        return {}
