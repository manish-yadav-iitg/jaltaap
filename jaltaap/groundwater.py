"""Groundwater: how deep is the water table going, and how fast?

India has ~25,000 government water-level recorders but most villages never
see their data. JalTaap gives any place a working groundwater picture from
three things it can always get, plus whatever a field officer adds:

  1. Rainfall routed into the aquifer. Rain doesn't reach the water table
     the same day; we weight the last 30 / 90 / 180 days with decaying
     weights so a good monsoon keeps helping for months.
  2. The aquifer type sets how much rain soaks in (CGWB GEC-2015 rainfall
     infiltration factors) and how much the level moves per metre of water
     (specific yield).
  3. Pumping (draft). Picked from a usage profile, or set exactly.
  +  Real well readings (DWLR / hand-measured), which re-anchor the model.

Outputs follow the official CGWB language so officials trust them:
  Stage of Groundwater Development = yearly draft / yearly recharge
     <= 70% Safe, 70-90% Semi-critical, 90-100% Critical, > 100% Over-exploited
  plus a depth-based status, days until the well hits warning / critical /
  dry levels, a 90-day projection under 4 scenarios, a P10-P90 band built
  by replaying every past year's rain, the safe pumping limit, and advice
  for farmers, planners and researchers.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import store
from .drought import climate

AQUIFERS = {  # key: (label, rainfall infiltration factor, specific yield)
    "alluvium": ("Alluvium (Indo-Gangetic, coastal)", 0.22, 0.12),
    "sandstone": ("Sandstone / soft rock", 0.12, 0.05),
    "basalt": ("Basalt (Deccan trap)", 0.13, 0.025),
    "granite": ("Granite / gneiss (hard rock)", 0.11, 0.015),
    "limestone": ("Limestone", 0.07, 0.02),
}
USAGE = {  # yearly draft in mm of water over the area
    "heavy": ("Heavy irrigation (tubewell paddy / sugarcane)", 260),
    "moderate": ("Mixed farming + town supply", 140),
    "light": ("Rain-fed farming, drinking water only", 55),
}
SCENARIOS = {  # rain multiplier, pumping multiplier
    "normal": ("Normal year", 1.0, 1.0),
    "drought": ("Drought: no rain, 20% more pumping", 0.0, 1.2),
    "monsoon": ("Good monsoon: 50% more rain, 30% less pumping", 1.5, 0.7),
    "heatwave": ("Heatwave: half the rain, 40% more pumping", 0.5, 1.4),
}


def effective_rain(p: pd.Series) -> pd.Series:
    """Rain that reaches the water table today = decaying blend of the last 30/90/180 days (mm/day)."""
    w30 = p.rolling(30, min_periods=1).mean()
    w90 = p.rolling(90, min_periods=1).mean()
    w180 = p.rolling(180, min_periods=1).mean()
    return 0.5 * w30 + 0.3 * w90 + 0.2 * w180


def _draft_daily(annual_mm: float, month: int) -> float:
    """Pumping peaks in the dry months (Mar-May) and rabi irrigation (Dec-Feb), low in monsoon."""
    shape = {1: 1.3, 2: 1.4, 3: 1.5, 4: 1.6, 5: 1.6, 6: 0.9, 7: 0.4, 8: 0.3, 9: 0.5, 10: 0.8, 11: 1.0, 12: 1.2}
    return annual_mm / 365 * shape[month] / (sum(shape.values()) / 12)


def cgwb_stage(stage_pct: float) -> tuple[str, str]:
    if stage_pct > 100:
        return "Over-exploited", "red"
    if stage_pct > 90:
        return "Critical", "red"
    if stage_pct > 70:
        return "Semi-critical", "orange"
    return "Safe", "green"


def depth_status(depth: float) -> tuple[str, str]:
    """Depth-to-water proxy used for continuous telemetry (m below ground)."""
    if depth > 25:
        return "Over-exploited range (> 25 m)", "red"
    if depth > 15:
        return "Critical range (15-25 m)", "red"
    if depth > 8:
        return "Semi-critical range (8-15 m)", "orange"
    return "Safe range (< 8 m)", "green"


def readings(place_key: str) -> list[dict]:
    return [r for r in store.items("gw_reading", limit=2000) if r.get("place") == place_key]


def place_key(lat: float, lon: float) -> str:
    return f"{lat:.2f},{lon:.2f}"


def _step(depth, eff_rain_mm, draft_mm, rif, sy, well):
    rise = rif * eff_rain_mm / 1000 / sy
    fall = draft_mm / 1000 / sy
    return float(np.clip(depth - rise + fall, 0.3, well))


def outlook(lat: float, lon: float, depth_now: float = 12.0, well_depth: float = 60.0,
            aquifer: str = "alluvium", usage: str = "moderate", draft_mm: float | None = None) -> dict:
    label, rif, sy = AQUIFERS.get(aquifer, AQUIFERS["alluvium"])
    draft_year = float(draft_mm if draft_mm is not None else USAGE.get(usage, USAGE["moderate"])[1])
    df = climate(lat, lon)
    p = df["p"]
    eff = effective_rain(p)

    # use the latest real reading if a field officer logged one
    key = place_key(lat, lon)
    obs = readings(key)
    if obs:
        depth_now = float(obs[-1]["depth_m"])

    # yearly balance from the last 10 complete years (mm of water)
    years = p.groupby(p.index.year).sum()
    years = years[years.index < df.index[-1].year].iloc[-10:]
    recharge_year = float(rif * years.mean())
    stage = 100 * draft_year / recharge_year if recharge_year else 999
    cat, cat_level = cgwb_stage(stage)
    safe_draft = 0.7 * recharge_year
    dstat, dlevel = depth_status(depth_now)

    # long-term trend: replay the model over the last 10 years
    hist_depth, d = [], depth_now
    sim_days = eff.iloc[-3650:]
    for t, e in sim_days.items():
        d = _step(d, e, _draft_daily(draft_year, t.month), rif, sy, well_depth)
        hist_depth.append(d)
    hist = pd.Series(hist_depth, index=sim_days.index)
    hist = hist - hist.iloc[-1] + depth_now  # anchor the replay on today's depth
    yearly_change = float(np.polyfit(np.arange(len(hist)) / 365, hist.values, 1)[0])  # + = going deeper

    # 90-day projections
    start = df.index[-1] + pd.Timedelta(days=1)
    days = pd.date_range(start, periods=90, freq="D")
    clim = p.groupby([p.index.month, p.index.day]).mean()

    def run(rain_mult, pump_mult, rain_source=None):
        series = np.concatenate([p.iloc[-180:].values, np.zeros(len(days))])
        depth, out = depth_now, []
        for i, t in enumerate(days):
            series[180 + i] = rain_source(t) if rain_source else clim.get((t.month, t.day), 0.0) * rain_mult
            w = series[i + 1:181 + i]
            e = 0.5 * w[-30:].mean() + 0.3 * w[-90:].mean() + 0.2 * w.mean()
            depth = _step(depth, e, _draft_daily(draft_year * pump_mult, t.month), rif, sy, well_depth)
            out.append(round(depth, 2))
        return out

    scen = {k: {"label": v[0], "depth": run(v[1], v[2])} for k, v in SCENARIOS.items()}
    # ensemble: what if the next 90 days get the rain of each past year
    ens = []
    for y in range(max(p.index[0].year, df.index[-1].year - 25), df.index[-1].year):
        yr = p[p.index.year == y]
        lookup = {(t.month, t.day): v for t, v in yr.items()}
        ens.append(run(1, 1, lambda t, L=lookup: L.get((t.month, t.day), 0.0)))
    ens = np.array(ens) if ens else np.array([scen["normal"]["depth"]])
    band = {"p10": np.percentile(ens, 10, axis=0).round(2).tolist(),
            "p50": np.percentile(ens, 50, axis=0).round(2).tolist(),
            "p90": np.percentile(ens, 90, axis=0).round(2).tolist()}

    thresholds = {"warning": round(0.5 * well_depth, 1), "critical": round(0.75 * well_depth, 1),
                  "dry": round(0.92 * well_depth, 1)}

    def days_to(level, rate_m_per_year, series=None):
        if series:
            for i, v in enumerate(series):
                if v >= level:
                    return i + 1
        if depth_now >= level:
            return 0
        if rate_m_per_year <= 0.01:
            return None
        return int((level - depth_now) / rate_m_per_year * 365)

    breach = {k: {"normal": days_to(v, yearly_change, scen["normal"]["depth"]),
                  "drought": days_to(v, yearly_change + 0.5, scen["drought"]["depth"])}
              for k, v in thresholds.items()}
    stress = float(np.clip(0.45 * min(stage, 150) / 1.5 + 0.35 * min(depth_now / well_depth, 1) * 100
                           + 0.20 * np.clip(yearly_change * 50, 0, 100), 0, 100))
    r2 = float(np.corrcoef(np.arange(len(hist)), hist.values)[0, 1] ** 2) if len(hist) > 30 else 0
    confidence = "high" if len(obs) >= 12 else "medium" if obs else "model only"

    return {
        "place": key, "aquifer": label, "rif": rif, "specific_yield": sy, "usage": USAGE.get(usage, ("custom",))[0],
        "depth_now": round(depth_now, 2), "well_depth": well_depth, "depth_status": dstat, "depth_level": dlevel,
        "recharge_mm_year": round(recharge_year), "draft_mm_year": round(draft_year), "stage_pct": round(stage),
        "category": cat, "level": cat_level, "safe_draft_mm_year": round(safe_draft),
        "cut_needed_pct": max(0, round(100 * (1 - safe_draft / draft_year))) if draft_year else 0,
        "trend_m_per_year": round(yearly_change, 2), "trend_fit_r2": round(r2, 2), "confidence": confidence,
        "stress": round(stress), "thresholds": thresholds, "breach_days": breach,
        "dates": [d.date().isoformat() for d in days], "scenarios": scen, "band": band,
        "history": {"t": [d.date().isoformat() for d in hist.index[::14]], "depth": hist.iloc[::14].round(2).tolist()},
        "observations": [{"t": dt.datetime.fromtimestamp(o["ts"]).date().isoformat(), "depth": o["depth_m"]} for o in obs[-60:]],
        "advice": advice(cat, yearly_change, depth_now, thresholds, safe_draft, draft_year),
        "source": "ERA5 rain (Open-Meteo), CGWB GEC-2015 infiltration factors, water-table fluctuation model",
    }


def advice(cat, trend, depth, th, safe, draft) -> dict:
    cut = max(0, round(100 * (1 - safe / draft))) if draft else 0
    farmer = ["Switch one paddy or sugarcane field to millets, pulses or oilseeds next season.",
              "Use drip or sprinklers on vegetables and orchards (saves 30-50% water).",
              "Irrigate at night or early morning; skip watering on rainy days."]
    planner = [f"Cap new borewells in this block until pumping falls by about {cut}%." if cut else
               "Pumping is within the safe limit; keep new borewells under watch.",
               "Fund check dams, farm ponds and recharge shafts before next monsoon (see Drought desk).",
               "Meter large users (industry, tanker filling points) and price over-use."]
    research = [f"Water table trend {trend:+.2f} m/year; add real well readings to tighten this.",
                "Compare with CGWB pre/post-monsoon data on India-WRIS for the block.",
                "Test the infiltration factor by matching the 2019-2023 monsoon rises."]
    if cat in ("Critical", "Over-exploited"):
        farmer.insert(0, "Your area pumps out more than the rain puts back. Wells will keep going deeper.")
    if depth >= th["warning"]:
        farmer.insert(0, f"The water is already below {th['warning']} m: deepen only as a last resort, share wells.")
    return {"Farmer": farmer, "Planner / BDO": planner, "Researcher": research}


def add_reading(lat: float, lon: float, depth_m: float, source: str = "field", well: str = "") -> dict:
    """Log a well reading. Returns the reading plus a transition alert if the category changed."""
    key = place_key(lat, lon)
    prev = readings(key)
    item = store.put("gw_reading", {"place": key, "lat": lat, "lon": lon, "depth_m": float(depth_m),
                                    "source": source, "well": well})
    out = {"reading": item}
    if prev:
        before, _ = depth_status(float(prev[-1]["depth_m"]))
        now, lvl = depth_status(float(depth_m))
        if before != now:
            from . import aws
            msg = f"JalTaap groundwater alert ({key}): well moved from '{before}' to '{now}' ({depth_m} m below ground)."
            out["transition"] = {"from": before, "to": now, "level": lvl, "sent": aws.send_sms(msg, subject="Groundwater")}
            store.audit("gw_transition", place=key, frm=before, to=now)
    return out
