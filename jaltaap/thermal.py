"""Body Heat Load: one 0-100 number for "how hard is this heat on a human body".

Three well-known indices each see a different side of heat:
  - Heat Index (NOAA Rothfusz): air temperature + humidity, in the shade
  - WBGT (Liljegren, via ECMWF thermofeel): adds sun and wind, the work/sport standard
  - UTCI (ECMWF thermofeel): full body energy balance incl. radiation and wind
JalTaap scales each to 0-100 against its own published danger bands and
blends them. A plain average can hide one index screaming danger, so the
score is never allowed to drop below 90% of the worst single index (the
"weakest link" rule). Hot nights add load because the body never recovers.

The output also says which index is driving the score, so the advice can
match the cause (humidity -> shade and fans don't help much; sun -> shade
helps a lot).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .heatstress import cos_solar_zenith
from .http import get_json

FORECAST_API = "https://api.open-meteo.com/v1/forecast"

# (index, value where load starts, value where it is maximal, weight)
SCALES = {"wbgt": (26.0, 35.0, 0.40), "utci": (26.0, 46.0, 0.35), "hi": (32.0, 60.0, 0.25)}
BANDS = [(25, "Comfortable", "green"), (50, "Caution", "green"), (70, "Strain", "orange"),
         (85, "Danger", "red"), (101, "Extreme", "red")]
DRIVER_ADVICE = {
    "hi": "Humidity is the problem: sweat can't cool you. Fans help little above 35°C; use wet cloths, cool water and rest.",
    "wbgt": "Sun and effort are the problem: shade, light clothing and rest breaks cut the load a lot.",
    "utci": "Sun plus still air: get out of direct sun, find moving air, and avoid hot vehicles and tin roofs.",
}


def heat_index_c(t_c, rh):
    """NOAA Rothfusz regression with its low/high humidity adjustments, °C in and out."""
    t = np.asarray(t_c, dtype=float) * 9 / 5 + 32
    rh = np.asarray(rh, dtype=float)
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    full = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh - 6.83783e-3 * t * t
            - 5.481717e-2 * rh * rh + 1.22874e-3 * t * t * rh + 8.5282e-4 * t * rh * rh - 1.99e-6 * t * t * rh * rh)
    low = (rh < 13) & (t >= 80) & (t <= 112)
    full = np.where(low, full - (13 - rh) / 4 * np.sqrt(np.clip(17 - np.abs(t - 95), 0, None) / 17), full)
    high = (rh > 85) & (t >= 80) & (t <= 87)
    full = np.where(high, full + (rh - 85) / 10 * (87 - t) / 5, full)
    hi_f = np.where((simple + t) / 2 >= 80, full, simple)
    return (hi_f - 32) * 5 / 9


def _scale(v, lo, hi):
    return np.clip((np.asarray(v, dtype=float) - lo) / (hi - lo) * 100, 0, 100)


def band(score: float):
    for top, label, level in BANDS:
        if score < top:
            return label, level
    return BANDS[-1][1:]


def compute(t_c, rh, wind_ms, sw, direct, pressure_hpa, times_utc, lat, lon):
    """Vectorised indices + Body Heat Load for hourly arrays."""
    t_c = np.asarray(t_c, float)
    rh = np.asarray(rh, float)
    va = np.maximum(np.asarray(wind_ms, float), 0.5)
    sw = np.maximum(np.asarray(sw, float), 0)
    direct = np.maximum(np.asarray(direct, float), 0)
    t_k = t_c + 273.15
    hi = heat_index_c(t_c, rh)
    try:
        import thermofeel as tf
        fdir = np.where(sw > 1, np.clip(direct / np.maximum(sw, 1), 0, 1), 0)
        cz = cos_solar_zenith(pd.DatetimeIndex(times_utc), lat, lon)
        wbgt = tf.calculate_wbgt_liljegren(t_k, rh, np.asarray(pressure_hpa, float), va, sw, fdir, cz) - 273.15
        # mean radiant temperature: shade = air temp; sun adds roughly 1.8 K per 100 W/m² of direct
        # and 0.8 K per 100 W/m² of diffuse light (capped), a common street-level approximation
        mrt = t_k + np.minimum(0.018 * direct + 0.008 * (sw - direct), 30)
        e_hpa = rh / 100 * 6.105 * np.exp(17.27 * t_c / (237.7 + t_c))
        utci = tf.calculate_utci(t_k, va, mrt, ehPa=e_hpa) - 273.15
        how = "thermofeel (ECMWF)"
    except Exception:
        e = rh / 100 * 6.105 * np.exp(17.27 * t_c / (237.7 + t_c))
        wbgt = 0.567 * t_c + 0.393 * e + 3.94 + np.minimum(sw, 1000) * 0.002  # BoM simplified + sun bump
        utci = t_c + 0.33 * e - 0.7 * va + np.minimum(sw, 1000) * 0.012 - 4.0  # rough fallback
        how = "simplified formulas (install thermofeel for the full method)"
    subs = {k: _scale(v, *SCALES[k][:2]) for k, v in (("wbgt", wbgt), ("utci", utci), ("hi", hi))}
    blend = sum(subs[k] * SCALES[k][2] for k in subs)
    worst = np.max(np.vstack(list(subs.values())), axis=0)
    score = np.maximum(blend, 0.9 * worst)
    return {"hi": hi, "wbgt": wbgt, "utci": utci, "subs": subs, "score": score, "method": how}


def body_heat_load(lat: float, lon: float, days: int = 3) -> dict:
    d = get_json(FORECAST_API, {
        "latitude": lat, "longitude": lon, "forecast_days": days, "timezone": "GMT", "wind_speed_unit": "ms",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation,direct_radiation,"
                  "surface_pressure",
    })
    h = pd.DataFrame(d["hourly"]).dropna()
    h["time"] = pd.to_datetime(h["time"], utc=True)
    r = compute(h["temperature_2m"], h["relative_humidity_2m"], h["wind_speed_10m"], h["shortwave_radiation"],
                h["direct_radiation"], h["surface_pressure"], h["time"], lat, lon)
    local = pd.DatetimeIndex(h["time"]).tz_convert("Asia/Kolkata")
    now = pd.Timestamp.now(tz="Asia/Kolkata").floor("h")
    hours = []
    for i in range(len(h)):
        if local[i] < now:
            continue
        s = float(r["score"][i])
        label, level = band(s)
        hours.append({"time": local[i].strftime("%Y-%m-%dT%H:00"), "score": round(s), "label": label, "level": level,
                      "temp": round(float(h["temperature_2m"].iloc[i]), 1), "rh": int(h["relative_humidity_2m"].iloc[i]),
                      "hi": round(float(r["hi"][i]), 1), "wbgt": round(float(r["wbgt"][i]), 1),
                      "utci": round(float(r["utci"][i]), 1)})
    # night recovery: lowest air temperature each night (8 pm - 6 am) adds load to the next day
    tl = pd.Series(h["temperature_2m"].values, index=local)
    nights = tl[(tl.index.hour >= 20) | (tl.index.hour < 6)]
    night_min = nights.groupby((nights.index - pd.Timedelta(hours=6)).date).min()

    out_days = []
    for day in sorted({x["time"][:10] for x in hours}):
        hs = [x for x in hours if x["time"][:10] == day]
        peak = max(hs, key=lambda x: x["score"])
        i = list(local.strftime("%Y-%m-%dT%H:00")).index(peak["time"])
        contrib = {k: float(r["subs"][k][i] * SCALES[k][2]) for k in SCALES}
        tot = sum(contrib.values()) or 1
        driver = max(r["subs"], key=lambda k: r["subs"][k][i])
        prev_night = night_min.get(dt.date.fromisoformat(day) - dt.timedelta(days=1))
        night_bonus = 0 if prev_night is None else int(np.clip((prev_night - 26) * 3, 0, 12))
        s = min(100, peak["score"] + night_bonus)
        label, level = band(s)
        out_days.append({"date": day, "score": s, "label": label, "level": level, "peak_hour": int(peak["time"][11:13]),
                         "driver": driver, "advice": DRIVER_ADVICE[driver],
                         "share": {k: round(100 * v / tot) for k, v in contrib.items()},
                         "night_min": None if prev_night is None else round(float(prev_night), 1),
                         "night_bonus": night_bonus,
                         "hi": peak["hi"], "wbgt": peak["wbgt"], "utci": peak["utci"]})
    return {"hours": hours, "days": out_days, "method": r["method"],
            "bands": [{"upto": b[0], "label": b[1], "level": b[2]} for b in BANDS],
            "source": "Open-Meteo hourly forecast; Heat Index (NOAA), WBGT + UTCI (ECMWF thermofeel)"}
