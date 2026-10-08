"""Drought watch for any village or district in India.

Data: ERA5 daily rain and reference evapotranspiration (FAO-56 ET0) from 1991
to last week (Open-Meteo archive, free), plus today's soil moisture.

What it computes
  SPI-1 / 3 / 6      Standardised Precipitation Index (WMO): this period's rain vs the
                     same calendar window in every past year, as a z-score
                     (non-parametric: Gringorten plotting position -> normal quantile)
  SPEI-3             same idea on rain minus evaporative demand (P - ET0),
                     catches "it rained, but the heat dried it all out"
  season deficit     rain since 1 June (kharif) or 1 October (rabi) vs normal
  drought class      US Drought Monitor style D0-D4 from the SPI/SPEI blend
  Water Health Score 0-100 blend of the above + soil moisture + groundwater stress

Then the advice side: water-wise crops, recharge structures that fit the
slope and soil, a village water budget, and a what-if slider.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from .http import get_json

ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"

CLASSES = [(-2.0, "D4", "Exceptional drought", "red"), (-1.6, "D3", "Extreme drought", "red"),
           (-1.3, "D2", "Severe drought", "red"), (-0.8, "D1", "Moderate drought", "orange"),
           (-0.5, "D0", "Abnormally dry", "orange"), (9, "--", "No drought", "green")]


def climate(lat: float, lon: float, start: str = "1991-01-01") -> pd.DataFrame:
    end = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    d = get_json(ARCHIVE_API, {"latitude": round(lat, 2), "longitude": round(lon, 2), "start_date": start,
                               "end_date": end, "daily": "precipitation_sum,et0_fao_evapotranspiration,temperature_2m_max",
                               "timezone": "Asia/Kolkata"}, long_cache=True)["daily"]
    df = pd.DataFrame(d)
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time").rename(columns={"precipitation_sum": "p", "et0_fao_evapotranspiration": "et0",
                                                "temperature_2m_max": "tmax"}).fillna(0)


def _z_from_history(current: float, history: np.ndarray) -> float:
    """Non-parametric standardised index: rank among past values -> normal quantile."""
    try:
        from scipy.stats import norm
    except ImportError:  # the Lambda package leaves scipy out; the standard library has the same quantile
        from statistics import NormalDist

        class norm:  # noqa: N801
            ppf = staticmethod(NormalDist().inv_cdf)
    allv = np.append(history, current)
    rank = (allv < current).sum() + 0.5 * ((allv == current).sum() - 1) + 1
    p = (rank - 0.44) / (len(allv) + 0.12)  # Gringorten
    return float(np.clip(norm.ppf(p), -3, 3))


def window_index(series: pd.Series, days: int, end: pd.Timestamp) -> tuple[float, float, float]:
    """(index, current total, normal total) for the `days` ending at `end`, vs the same window in past years."""
    cur = float(series[(series.index > end - pd.Timedelta(days=days)) & (series.index <= end)].sum())
    past = []
    for y in range(series.index[0].year + 1, end.year):
        e = end.replace(year=y) if not (end.month == 2 and end.day == 29) else end.replace(year=y, day=28)
        past.append(float(series[(series.index > e - pd.Timedelta(days=days)) & (series.index <= e)].sum()))
    past = np.array(past)
    return _z_from_history(cur, past), cur, float(np.mean(past)) if len(past) else float("nan")


def classify(x: float):
    for top, code, label, level in CLASSES:
        if x <= top:
            return code, label, level
    return CLASSES[-1][1:]


def soil_moisture(lat: float, lon: float) -> float | None:
    try:
        d = get_json(FORECAST_API, {"latitude": lat, "longitude": lon, "hourly": "soil_moisture_9_to_27cm",
                                    "past_days": 1, "forecast_days": 1, "timezone": "Asia/Kolkata"})
        v = [x for x in d["hourly"]["soil_moisture_9_to_27cm"] if x is not None]
        return round(float(np.mean(v)), 3) if v else None
    except Exception:
        return None


def drought_status(lat: float, lon: float) -> dict:
    df = climate(lat, lon)
    end = df.index[-1]
    spi1, p1, n1 = window_index(df["p"], 30, end)
    spi3, p3, n3 = window_index(df["p"], 90, end)
    spi6, p6, n6 = window_index(df["p"], 180, end)
    spei3, _, _ = window_index(df["p"] - df["et0"], 90, end)
    if 6 <= end.month <= 9:
        season_start, season = pd.Timestamp(end.year, 6, 1), "Kharif / monsoon (since 1 Jun)"
    else:
        season_start = pd.Timestamp(end.year if end.month >= 10 else end.year - 1, 10, 1)
        season = "Rabi / winter (since 1 Oct)"
    sdays = max((end - season_start).days, 30)  # early in a season, look at least 30 days back
    _, s_cur, s_norm = window_index(df["p"], sdays, end)
    deficit = None if not s_norm else round(100 * (s_cur - s_norm) / s_norm)
    blend = 0.5 * spi3 + 0.3 * spei3 + 0.2 * spi6
    code, label, level = classify(blend)
    sm = soil_moisture(lat, lon)
    # yearly rain history for the chart (completed years)
    yearly = df["p"].groupby(df.index.year).sum()
    yearly = yearly[yearly.index < end.year]
    monthly = df["p"].resample("MS").sum().iloc[-24:]
    mnorm = df["p"].groupby(df.index.month).sum() / max(1, df.index.year.nunique())
    imd = "Large deficient" if deficit is not None and deficit <= -60 else "Deficient" if deficit is not None and deficit <= -20 \
        else "Normal" if deficit is not None and deficit < 20 else "Excess" if deficit is not None else "-"
    return {
        "as_of": end.date().isoformat(), "spi1": round(spi1, 2), "spi3": round(spi3, 2), "spi6": round(spi6, 2),
        "spei3": round(spei3, 2), "blend": round(blend, 2), "class": code, "label": label, "level": level,
        "rain_30d": round(p1), "normal_30d": round(n1), "rain_90d": round(p3), "normal_90d": round(n3),
        "season": season, "season_rain": round(s_cur), "season_normal": round(s_norm or 0), "season_deficit_pct": deficit,
        "imd_category": imd, "annual_normal_mm": round(float(yearly.mean())), "soil_moisture": sm,
        "month_normals": [round(float(mnorm.get(m, 0))) for m in range(1, 13)],
        "et0_90d": round(float(df["et0"].iloc[-90:].sum())),
        "hot_days_90d": int((df["tmax"].iloc[-90:] >= 40).sum()),
        "yearly": {"years": [int(y) for y in yearly.index], "mm": [round(float(v)) for v in yearly.values]},
        "monthly": {"t": [d.strftime("%Y-%m") for d in monthly.index], "mm": [round(float(v)) for v in monthly.values],
                    "normal": [round(float(mnorm[d.month])) for d in monthly.index]},
        "source": "ERA5 via Open-Meteo archive (1991-), FAO-56 ET0; SPI per WMO-No.1090 (non-parametric)",
    }


def water_health(d: dict, gw_stress: float | None = None) -> dict:
    """0 = crisis, 100 = healthy. Each part is shown so the number is never a black box."""
    def z2(x):
        return float(np.clip(50 + 25 * x, 0, 100))
    parts = {"Rain, last 3 months (SPI-3)": (z2(d["spi3"]), 0.30),
             "Rain minus heat demand (SPEI-3)": (z2(d["spei3"]), 0.25),
             "Longer dry spell (SPI-6)": (z2(d["spi6"]), 0.15)}
    if d.get("season_deficit_pct") is not None:
        parts["Season rain vs normal"] = (float(np.clip(70 + d["season_deficit_pct"], 0, 100)), 0.15)
    if d.get("soil_moisture") is not None:
        parts["Soil moisture (9-27 cm)"] = (float(np.clip((d["soil_moisture"] - 0.08) / 0.27 * 100, 0, 100)), 0.15)
    if gw_stress is not None:
        parts["Groundwater"] = (100 - gw_stress, 0.20)
    wsum = sum(w for _, w in parts.values())
    score = round(sum(v * w for v, w in parts.values()) / wsum)
    level = "red" if score < 35 else "orange" if score < 55 else "green"
    word = "Crisis" if score < 20 else "Stressed" if score < 35 else "Watch" if score < 55 else "Healthy"
    return {"score": score, "level": level, "word": word,
            "parts": [{"label": k, "score": round(v), "weight": w} for k, (v, w) in parts.items()]}


# ---------------------------------------------------------------- crops
# season water need (mm, FAO-56 / ICAR ranges), drought tolerance 1-5, season
CROPS = [
    ("Pearl millet (bajra)", 400, 5, "kharif"), ("Sorghum (jowar)", 500, 4, "kharif"),
    ("Finger millet (ragi)", 420, 4, "kharif"), ("Pigeon pea (arhar)", 620, 4, "kharif"),
    ("Green gram (moong)", 350, 4, "kharif"), ("Groundnut", 550, 3, "kharif"),
    ("Soybean", 500, 3, "kharif"), ("Maize", 600, 2, "kharif"), ("Cotton", 850, 3, "kharif"),
    ("Paddy (rice)", 1300, 1, "kharif"), ("Sugarcane", 1900, 1, "annual"),
    ("Chickpea (chana)", 380, 4, "rabi"), ("Mustard", 320, 4, "rabi"), ("Barley", 400, 4, "rabi"),
    ("Safflower", 400, 5, "rabi"), ("Wheat", 500, 2, "rabi"), ("Lentil (masoor)", 300, 4, "rabi"),
]


def crop_advice(expected_rain_mm: float, irrigation_mm: float, season: str) -> list[dict]:
    water = expected_rain_mm * 0.7 + irrigation_mm  # ~70% of rain is usable by the crop
    out = []
    for name, need, tol, s in CROPS:
        if s not in (season, "annual"):
            continue
        cover = water / need
        verdict = "good fit" if cover >= 1 else "ok with care" if cover >= 0.8 or (cover >= 0.65 and tol >= 4) else "too thirsty"
        out.append({"crop": name, "need_mm": need, "tolerance": tol, "covered_pct": round(100 * cover), "verdict": verdict})
    rank = {"good fit": 0, "ok with care": 1, "too thirsty": 2}
    return sorted(out, key=lambda x: (rank[x["verdict"]], x["need_mm"]))


# ---------------------------------------------------------------- recharge
SOILS = {"sandy": ("Sandy / alluvial", 0.25), "loam": ("Loamy", 0.15), "clay": ("Clayey / black cotton", 0.07),
         "rock": ("Hard rock (granite, basalt)", 0.10)}


def recharge_advice(annual_rain: float, slope_pct: float, soil: str = "loam", area_ha: float = 10) -> list[dict]:
    """Rule-of-thumb structure choice (CGWB Master Plan for Artificial Recharge 2020 style)."""
    label, infil = SOILS.get(soil, SOILS["loam"])
    runoff = annual_rain / 1000 * area_ha * 10_000 * max(0.1, 0.45 - infil)  # m3/yr that would run off
    opts = []

    def add(name, why, capture, cost):
        opts.append({"structure": name, "why": why, "recharge_m3": round(runoff * capture), "cost": cost})
    if slope_pct >= 3:
        add("Contour trenches + gully plugs", "Sloping land: slow the water down where it falls", 0.25, "low")
        add("Check dam on the nala", "Enough slope to back water up behind a small wall", 0.35, "medium")
    else:
        add("Farm pond (lined bottom only at the edges)", "Flat land: store run-off and let it soak in", 0.3, "medium")
        add("Percolation tank", "Flat, open land near a seasonal stream", 0.4, "high")
    if soil in ("clay", "rock"):
        add("Recharge shaft / borewell recharge pit", f"{label} soil lets little water through, so push it down", 0.2, "medium")
    else:
        add("Recharge pits along field bunds", f"{label} soil soaks water well", 0.2, "low")
    add("Rooftop rain harvesting (homes, school, panchayat)", "Cleanest water, cheapest to start", 0.05, "low")
    if annual_rain >= 900:
        add("Revive the village tank / johad (desilting)", "High rain: old tanks can hold a lot more", 0.45, "medium")
    return sorted(opts, key=lambda x: -x["recharge_m3"])


# ---------------------------------------------------------------- budget
def water_budget(population: int, livestock: int, irrigated_ha: float, crop_need_mm: float,
                 annual_rain: float, catchment_ha: float, gw_safe_m3: float, rain_change_pct: float = 0,
                 demand_change_pct: float = 0) -> dict:
    """Yearly village water budget in m3. Drinking need: 55 L/person/day (Jal Jeevan Mission norm)."""
    rain = annual_rain * (1 + rain_change_pct / 100)
    domestic = population * 55 * 365 / 1000
    animals = livestock * 30 * 365 / 1000
    irrigation = irrigated_ha * 10_000 * crop_need_mm / 1000 * 0.6  # rain covers part, 60% must be applied
    demand = (domestic + animals + irrigation) * (1 + demand_change_pct / 100)
    surface = rain / 1000 * catchment_ha * 10_000 * 0.12  # what tanks/ponds actually capture
    supply = surface + gw_safe_m3 * (rain / annual_rain if annual_rain else 1)
    gap = supply - demand
    return {"demand_m3": round(demand), "supply_m3": round(supply), "gap_m3": round(gap),
            "parts": {"Drinking & home": round(domestic), "Livestock": round(animals), "Irrigation": round(irrigation),
                      "Tanks & ponds": round(surface), "Groundwater (safe yield)": round(supply - surface)},
            "days_covered": round(365 * supply / demand) if demand else 365,
            "tankers_per_day": max(0, math.ceil(-gap / 365 / 10)) if gap < 0 else 0}  # 10 kL tankers
