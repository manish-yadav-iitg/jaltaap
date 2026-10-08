"""Heavy-rain / waterlogging risk using IMD's own rainfall categories.

Rivers are slow; city streets flood in hours. This catches the
"one heavy rain and the road is a river" problem.
IMD daily categories (mm in 24 h): heavy 64.5-115.5, very heavy 115.6-204.4,
extremely heavy >= 204.5. Cloudburst = ~100 mm in an hour (IMD).
"""
from __future__ import annotations

import pandas as pd

from .http import get_json

FORECAST_API = "https://api.open-meteo.com/v1/forecast"


def imd_category(mm_day: float) -> str:
    if mm_day >= 204.5:
        return "extremely heavy"
    if mm_day >= 115.6:
        return "very heavy"
    if mm_day >= 64.5:
        return "heavy"
    if mm_day >= 15.6:
        return "moderate"
    return "light/none"


def rain_level(mm_day: float, max_hourly: float) -> str:
    if mm_day >= 115.6 or max_hourly >= 50:
        return "red"
    if mm_day >= 64.5 or max_hourly >= 25:
        return "orange"
    return "green"


def rain_risk(lat: float, lon: float, days: int = 5) -> dict:
    fc = get_json(FORECAST_API, {
        "latitude": lat, "longitude": lon, "forecast_days": days,
        "daily": "precipitation_sum,precipitation_probability_max",
        "hourly": "precipitation",
        "timezone": "Asia/Kolkata",
    })
    daily = pd.DataFrame(fc["daily"])
    hourly = pd.DataFrame(fc["hourly"])
    hourly["time"] = pd.to_datetime(hourly["time"])
    hmax = hourly.set_index("time")["precipitation"].resample("D").max()
    out = []
    for _, r in daily.iterrows():
        day = pd.Timestamp(r["time"])
        mm = float(r["precipitation_sum"] or 0)
        mh = float(hmax.get(day, 0) or 0)
        out.append({
            "date": r["time"], "rain_mm": round(mm, 1), "max_mm_per_hour": round(mh, 1),
            "chance_pct": r.get("precipitation_probability_max"),
            "imd_category": imd_category(mm), "level": rain_level(mm, mh),
        })
    order = {"green": 0, "orange": 1, "red": 2}
    worst = max(out, key=lambda x: (order[x["level"]], x["rain_mm"]))
    return {"days": out, "worst_day": worst, "level": worst["level"],
            "source": "Open-Meteo forecast, IMD rainfall categories"}
