"""Hour-by-hour heat stress for people working or playing outside.

WBGT (wet-bulb globe temperature) is the number used worldwide for heat
safety at work, in the army and in sports. It mixes air temperature,
humidity, sun and wind. We compute it with ECMWF's open-source `thermofeel`
library (Liljegren method) from Open-Meteo's hourly forecast.

Work/rest advice follows the widely used ACGIH-style table (fit, hydrated,
acclimatised adults in light clothing). Converted from °F to °C:
  WBGT 25.6-27.8 / 27.8-29.4 / 29.4-31.1 / 31.1-32.2 / >32.2
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .http import get_json

FORECAST_API = "https://api.open-meteo.com/v1/forecast"

# (upper WBGT °C, label, level, minutes work/rest per hour for light, moderate, heavy work)
BANDS = [
    (25.6, "Low", "green", ("no limit", "no limit", "no limit")),
    (27.8, "Caution", "green", ("no limit", "no limit", "no limit")),
    (29.4, "High", "orange", ("no limit", "50 work / 10 rest", "40 work / 20 rest")),
    (31.1, "Very high", "orange", ("no limit", "40 work / 20 rest", "30 work / 30 rest")),
    (32.2, "Extreme", "red", ("no limit", "30 work / 30 rest", "20 work / 40 rest")),
    (99.0, "Dangerous", "red", ("50 work / 10 rest", "20 work / 40 rest", "10 work / 50 rest")),
]
WATER = {"green": "a glass every 30 min", "orange": "a glass every 20 min", "red": "a glass every 15 min"}


def band(wbgt: float):
    for top, label, level, rest in BANDS:
        if wbgt < top:
            return label, level, rest
    return BANDS[-1][1:]


def cos_solar_zenith(times_utc: pd.DatetimeIndex, lat: float, lon: float) -> np.ndarray:
    """NOAA solar position (good to ~0.5°), evaluated at the middle of each hour."""
    t = times_utc - pd.Timedelta(minutes=30)  # Open-Meteo radiation = mean of the preceding hour
    doy = t.dayofyear.values
    hour = t.hour.values + t.minute.values / 60
    g = 2 * np.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    tst = hour * 60 + eqtime + 4 * lon
    ha = np.radians(tst / 4 - 180)
    la = np.radians(lat)
    return np.clip(np.sin(la) * np.sin(decl) + np.cos(la) * np.cos(decl) * np.cos(ha), -1, 1)


def hourly_heat_stress(lat: float, lon: float, days: int = 3) -> dict:
    import thermofeel as tf

    d = get_json(FORECAST_API, {
        "latitude": lat, "longitude": lon, "forecast_days": days, "timezone": "GMT",
        "wind_speed_unit": "ms",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation,"
                  "direct_radiation,surface_pressure,apparent_temperature",
    })
    h = pd.DataFrame(d["hourly"])
    h["time"] = pd.to_datetime(h["time"], utc=True)
    h = h.dropna()
    t_k = h["temperature_2m"].values + 273.15
    rh = h["relative_humidity_2m"].values
    va = np.maximum(h["wind_speed_10m"].values, 0.5)
    sw = np.maximum(h["shortwave_radiation"].values, 0)
    fdir = np.where(sw > 1, np.clip(h["direct_radiation"].values / np.maximum(sw, 1), 0, 1), 0)
    cz = cos_solar_zenith(pd.DatetimeIndex(h["time"]), lat, lon)
    wbgt = tf.calculate_wbgt_liljegren(t_k, rh, h["surface_pressure"].values, va, sw, fdir, cz) - 273.15
    wbt = tf.calculate_wbt(t_k, rh) - 273.15
    if np.ndim(wbt) == 0 or np.isnan(np.nanmean(wbt)):
        wbt = np.full(len(h), np.nan)

    local = pd.DatetimeIndex(h["time"]).tz_convert("Asia/Kolkata")
    now = pd.Timestamp.now(tz="Asia/Kolkata").floor("h")
    hours = []
    for i in range(len(h)):
        if local[i] < now:
            continue
        w = float(wbgt[i])
        label, level, rest = band(w)
        hours.append({
            "time": local[i].strftime("%Y-%m-%dT%H:00"), "hour": int(local[i].hour),
            "temp": round(float(h["temperature_2m"].iloc[i]), 1),
            "feels": round(float(h["apparent_temperature"].iloc[i]), 1),
            "humidity": int(rh[i]), "wbgt": round(w, 1),
            "wetbulb": None if np.isnan(wbt[i]) else round(float(wbt[i]), 1),
            "label": label, "level": level,
            "rest": {"light": rest[0], "moderate": rest[1], "heavy": rest[2]},
        })

    # summary for today (rest of today) and tomorrow, 6 am - 8 pm working hours
    out_days = []
    for day in sorted({x["time"][:10] for x in hours})[:2]:
        hs = [x for x in hours if x["time"][:10] == day and 6 <= x["hour"] <= 20]
        if not hs:
            continue
        worst = max(hs, key=lambda x: x["wbgt"])
        safe = [x["hour"] for x in hs if x["level"] == "green"]
        out_days.append({
            "date": day, "worst_hour": worst["hour"], "worst_wbgt": worst["wbgt"], "worst_label": worst["label"],
            "level": worst["level"], "safe_hours": _ranges(safe),
            "risky_hours": _ranges([x["hour"] for x in hs if x["level"] != "green"]),
            "plan": worst["rest"], "water": WATER[worst["level"]],
        })
    return {"hours": hours, "days": out_days,
            "source": "WBGT via ECMWF thermofeel (Liljegren) from Open-Meteo hourly forecast"}


def _ranges(hrs: list[int]) -> list[str]:
    """[6,7,8,15,16] -> ['6-9 am', '3-5 pm']"""
    if not hrs:
        return []
    out, start, prev = [], hrs[0], hrs[0]
    for h in hrs[1:] + [None]:
        if h is not None and h == prev + 1:
            prev = h
            continue
        out.append(f"{_h(start)}–{_h(prev + 1)}")
        if h is not None:
            start = prev = h
    return out


def _h(h: int) -> str:
    h %= 24
    return f"{12 if h % 12 == 0 else h % 12} {'am' if h < 12 else 'pm'}"
