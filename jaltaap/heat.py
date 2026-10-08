"""Heat risk: two lenses so it works for all of India.

1. IMD-style heatwave rule (dry heat, north/central India):
   Tmax >= 40 C and >= 4.5 C above that day's normal, or Tmax >= 45 C.
2. Humid heat (coasts, east, north-east), which the IMD rule mostly misses:
   - feels-like max vs the place's own 95th/99th percentile
   - wet-bulb temperature: 28 C is hard work for the body, 31 C is dangerous
     even for healthy people at rest (Vecellio et al. 2022, J. Appl. Physiol.)
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .http import get_json

ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"

WETBULB_CAUTION = 28.0
WETBULB_DANGER = 31.0


def heat_history(lat: float, lon: float, start: str = "1995-01-01", end: str | None = None) -> pd.DataFrame:
    end = end or (dt.date.today() - dt.timedelta(days=7)).isoformat()
    d = get_json(ARCHIVE_API, {
        "latitude": round(lat, 2), "longitude": round(lon, 2),
        "start_date": start, "end_date": end,
        "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max",
        "timezone": "Asia/Kolkata",
    }, long_cache=True)["daily"]
    df = pd.DataFrame(d)
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time").dropna()


def heat_thresholds(hist: pd.DataFrame, before: str | None = None) -> dict:
    h = hist[hist.index < pd.Timestamp(before)] if before else hist
    fl = h["apparent_temperature_max"]
    return {"feels_p95": float(fl.quantile(0.95)), "feels_p99": float(fl.quantile(0.99))}


def normal_tmax(hist: pd.DataFrame, day: pd.Timestamp, window: int = 7, col: str = "temperature_2m_max") -> float:
    """Average for this calendar day (+/- window days) across all years."""
    doy = hist.index.dayofyear
    d = day.dayofyear
    diff = np.minimum(np.abs(doy - d), 366 - np.abs(doy - d))
    return float(hist[col][diff <= window].mean())


def imd_heatwave(tmax: float, normal: float, hills: bool = False) -> bool:
    return imd_heat_label(tmax, normal, hills) is not None


def imd_heat_label(tmax: float, normal: float, hills: bool = False) -> str | None:
    """IMD rules: plains need Tmax >= 40 C (hills >= 30 C) plus a departure from normal
    of 4.5-6.4 C (heatwave) or > 6.4 C (severe); or Tmax >= 45 C / >= 47 C outright."""
    base = 30 if hills else 40
    dep = tmax - normal
    if tmax >= 47 or (tmax >= base and dep > 6.4):
        return "Severe heatwave"
    if tmax >= 45 or (tmax >= base and dep >= 4.5):
        return "Heatwave"
    return None


def warm_night_label(tmin: float, normal_min: float) -> str | None:
    """IMD warm night: min temperature 4.5-6.4 C above normal (very warm: > 6.4 C).
    We also need the night to stay at 25 C or more, so a mild winter night isn't flagged."""
    if tmin is None or tmin < 25:
        return None
    dep = tmin - normal_min
    if dep > 6.4:
        return "Very warm night"
    if dep >= 4.5:
        return "Warm night"
    return None


ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"


def ensemble_heat_chance(lat: float, lon: float, th: dict, days: int = 7) -> dict:
    """Share of ECMWF's 51 ensemble runs where the day's max feels-like crosses
    this place's own 95th / 99th percentile."""
    d = get_json(ENSEMBLE_API, {"latitude": lat, "longitude": lon, "hourly": "apparent_temperature",
                                "models": "ecmwf_ifs025", "forecast_days": days, "timezone": "Asia/Kolkata"})
    h = pd.DataFrame(d["hourly"])
    h["time"] = pd.to_datetime(h["time"])
    mem = h.set_index("time")[[c for c in h.columns if c.startswith("apparent_temperature")]]
    dmax = mem.resample("D").max()
    out = {}
    for day, row in dmax.iterrows():
        v = row.dropna()
        if len(v):
            out[day.date().isoformat()] = {"orange": round(float((v >= th["feels_p95"]).mean()), 2),
                                           "red": round(float((v >= th["feels_p99"]).mean()), 2), "runs": int(len(v))}
    return out


def heat_level(feels: float, wetbulb: float | None, tmax: float, normal: float, th: dict,
               hills: bool = False, night: str | None = None) -> str:
    imd = imd_heat_label(tmax, normal, hills)
    if feels >= th["feels_p99"] or (wetbulb is not None and wetbulb >= WETBULB_DANGER) or imd == "Severe heatwave" \
            or night == "Very warm night":
        return "red"
    if feels >= th["feels_p95"] or (wetbulb is not None and wetbulb >= WETBULB_CAUTION) or imd or night:
        return "orange"
    return "green"


def heat_risk(lat: float, lon: float, days: int = 7) -> dict:
    hist = heat_history(lat, lon)
    th = heat_thresholds(hist)
    fc = get_json(FORECAST_API, {
        "latitude": lat, "longitude": lon, "forecast_days": days,
        "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max",
        "hourly": "wet_bulb_temperature_2m",
        "timezone": "Asia/Kolkata",
    })
    hills = (fc.get("elevation") or 0) >= 1000
    try:
        chance = ensemble_heat_chance(lat, lon, th, days)
    except Exception:
        chance = {}
    daily = pd.DataFrame(fc["daily"])
    daily["time"] = pd.to_datetime(daily["time"])
    hourly = pd.DataFrame(fc["hourly"])
    hourly["time"] = pd.to_datetime(hourly["time"])
    wb = hourly.set_index("time")["wet_bulb_temperature_2m"].resample("D").max()

    out = []
    for _, r in daily.iterrows():
        day = r["time"]
        normal = normal_tmax(hist, day)
        w = float(wb.get(day.normalize(), np.nan)) if day.normalize() in wb.index else None
        w = None if w is None or np.isnan(w) else round(w, 1)
        nmin = normal_tmax(hist, day, col="temperature_2m_min")
        tmin = r.get("temperature_2m_min")
        tmin = None if tmin is None or pd.isna(tmin) else float(tmin)
        night = warm_night_label(tmin, nmin)
        imd = imd_heat_label(r["temperature_2m_max"], normal, hills)
        lv = heat_level(r["apparent_temperature_max"], w, r["temperature_2m_max"], normal, th, hills, night)
        ch = chance.get(day.date().isoformat(), {})
        out.append({
            "date": day.date().isoformat(),
            "tmax": round(r["temperature_2m_max"], 1),
            "normal_tmax": round(normal, 1),
            "tmin": None if tmin is None else round(tmin, 1),
            "normal_tmin": round(nmin, 1),
            "feels_like_max": round(r["apparent_temperature_max"], 1),
            "wetbulb_max": w,
            "imd_heatwave": imd is not None,
            "imd_label": imd,
            "night": night,
            "chance_orange": ch.get("orange"), "chance_red": ch.get("red"), "runs": ch.get("runs"),
            "level": lv,
        })
    order = {"green": 0, "orange": 1, "red": 2}
    worst = max(out, key=lambda x: (order[x["level"]], x["feels_like_max"]))
    return {
        "thresholds": {k: round(v, 1) for k, v in th.items()},
        "days": out,
        "worst_day": worst,
        "level": worst["level"],
        "official_rule_misses_it": worst["level"] != "green" and not worst["imd_heatwave"],
        "warm_nights": [d["date"] for d in out if d["night"]],
        "hills": hills,
        "source": "Open-Meteo forecast + ERA5 history (1995-)",
    }


def warm_nights_per_year(hist: pd.DataFrame, base_end: str = "2015-01-01") -> pd.Series:
    """Nights per year warmer than the OLD 99th percentile of minimum temperature."""
    if "temperature_2m_min" not in hist:
        return pd.Series(dtype=int)
    p99 = hist.loc[hist.index < pd.Timestamp(base_end), "temperature_2m_min"].quantile(0.99)
    s = (hist["temperature_2m_min"] >= p99).groupby(hist.index.year).sum()
    return s[s.index < dt.date.today().year]


def extreme_days_per_year(hist: pd.DataFrame, base_end: str = "2015-01-01") -> pd.Series:
    """How many days a year cross the OLD 99th percentile (set on pre-2015 data).
    Shows how fast humid heat is growing at a place."""
    p99 = hist.loc[hist.index < pd.Timestamp(base_end), "apparent_temperature_max"].quantile(0.99)
    s = (hist["apparent_temperature_max"] >= p99).groupby(hist.index.year).sum()
    return s[s.index < dt.date.today().year]
