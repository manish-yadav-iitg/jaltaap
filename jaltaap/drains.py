"""Drain watch: spot a choking storm drain days before it floods a street.

How it works (physics first, ML second):
  1. Each drain node has a cheap ultrasonic level sensor upstream and
     downstream of a culvert / grate (ESP32 + HC-SR04 class hardware, or
     anything that can POST to /api/drains/reading).
  2. Flow through the opening follows the orifice equation
         Q = Cd * A * sqrt(2 g dh)
     so for the flow we expect (from rain and catchment), the head drop dh
     tells us the *effective open area*. Blockage = 1 - A_eff / A_design.
  3. An Isolation Forest on the residuals separates a real, steady build-up
     of debris from sensor noise and one-off spikes.
  4. A straight-line trend gives "days until 80% blocked".
  5. A Muskingum routing chain pushes the next storm through the network:
     a choked node holds water back (local flooding) and the delayed wave
     reaches the outfall later and flatter, so we can say where and when
     water will spill.

Without real sensors the module runs a demo network around the chosen
place: 6 nodes, 14 days of 15-minute readings, with debris slowly piling up
at one node. POST real readings to /api/drains/reading and they replace
the simulated ones for that node.
"""
from __future__ import annotations

import math

import numpy as np

G = 9.81
CD = 0.62  # sharp-edged orifice
NODES = [  # id, name, dx km east, dy km north, design area m2, catchment ha, downstream id
    ("D1", "Market nala inlet", -1.6, 1.2, 1.6, 35, "D3"),
    ("D2", "Station road culvert", 0.4, 1.5, 1.4, 28, "D3"),
    ("D3", "Gandhi chowk junction", -0.4, 0.4, 3.0, 20, "D5"),
    ("D4", "Hospital lane grate", 1.3, 0.2, 1.0, 18, "D5"),
    ("D5", "Railway underpass box", 0.2, -0.7, 4.0, 15, "D6"),
    ("D6", "Main outfall to river", 0.6, -1.8, 5.5, 10, None),
]
RUNOFF_C = 0.75  # dense city
DAYS, STEP_MIN = 14, 15


def _expected_q(rain_mm_h: np.ndarray, area_ha: float) -> np.ndarray:
    """Rational method Q = C i A (m3/s) plus a small dry-weather sewage base flow."""
    return RUNOFF_C * (rain_mm_h / 1000 / 3600) * area_ha * 10_000 + 0.12  # Indian storm drains also carry sewage


def _head(q, area_open):
    return (q / (CD * np.maximum(area_open, 1e-3))) ** 2 / (2 * G)


def simulate(seed: int = 7, blocked: str = "D3", blocked_final: float = 0.62, readings: dict | None = None) -> dict:
    rng = np.random.default_rng(seed)
    n = DAYS * 24 * 60 // STEP_MIN
    t_h = np.arange(n) * STEP_MIN / 60
    # light showers most afternoons plus two proper storms
    rain = np.clip(rng.gamma(0.25, 3.0, n) * (np.sin(2 * np.pi * (t_h - 9) / 24) > 0.4), 0, None)
    for start, peak in ((3.6 * 24, 22), (10.4 * 24, 30)):
        rain += peak * np.exp(-0.5 * ((t_h - start) / 1.5) ** 2)
    nodes = []
    for nid, name, dx, dy, area, catch, down in NODES:
        q = _expected_q(rain, catch)
        frac = np.zeros(n)
        if nid == blocked:
            ramp = np.clip((t_h - 2 * 24) / ((DAYS - 2) * 24), 0, 1)
            frac = blocked_final * ramp ** 1.4
        h_true = _head(q, area * (1 - frac))
        # ultrasonic noise ~3% of reading + rare spikes (a leaf, a splash)
        h_meas = h_true * (1 + rng.normal(0, 0.03, n)) * (1 + (rng.random(n) < 0.004) * rng.uniform(-0.6, 1.5, n))
        if readings and nid in readings:  # real readings override the tail of the series
            real = np.asarray(readings[nid], float)[-n:]
            h_meas[-len(real):] = real
        nodes.append(_analyse(nid, name, dx, dy, area, catch, down, t_h, q, h_meas))
    return {"nodes": nodes, "rain_mm_h": [round(float(x), 2) for x in rain[::4]], "step_h": STEP_MIN * 4 / 60,
            "days": DAYS, "demo": not readings}


def _analyse(nid, name, dx, dy, area, catch, down, t_h, q, h):
    from sklearn.ensemble import IsolationForest
    a_eff = q / (CD * np.sqrt(2 * G * np.maximum(h, 1e-4)))
    block = np.clip(1 - a_eff / area, -0.5, 1)
    # hourly medians kill single-sample spikes, then daily view for the trend
    per = 60 // STEP_MIN
    hourly = np.array([np.median(block[i:i + per]) for i in range(0, len(block), per)])
    hours = np.arange(len(hourly))
    ref = float(np.median(hourly[:96]))  # first four days = the drain as it was when calibrated
    resid = hourly - ref
    roll = np.array([resid[max(0, i - 6):i + 1].std() for i in range(len(resid))])
    X = np.c_[resid, roll]
    # novelty detection: learn what "normal" looks like from the clean days, judge the rest
    iso = IsolationForest(n_estimators=150, contamination=0.02, random_state=0).fit(X[:96])
    flags = iso.predict(X) == -1
    recent = resid[-72:]
    slope_per_day, _ = np.polyfit(np.arange(len(recent)) / 24, recent, 1)
    now = float(np.median(resid[-12:]))
    sustained = bool(flags[-72:].mean() > 0.5 and slope_per_day > 0.01)
    days_to_80 = None
    if slope_per_day > 0.002 and 0.15 <= now < 0.8:
        days_to_80 = round(float((0.8 - now) / slope_per_day), 1)
    state = "blocked" if now >= 0.6 else "choking" if (now >= 0.3 or sustained) else "clear"
    return {"id": nid, "name": name, "dx": dx, "dy": dy, "design_area_m2": area, "catchment_ha": catch,
            "downstream": down, "blockage_pct": round(100 * max(now, 0)), "trend_pct_per_day": round(100 * float(slope_per_day), 1),
            "days_to_80": days_to_80, "state": state, "ml_confirmed": bool(sustained),
            "series": [round(100 * float(max(x, 0)), 1) for x in resid[::3]]}


def muskingum(inflow: np.ndarray, K: float, X: float, dt: float = 0.25) -> np.ndarray:
    """Classic Muskingum routing (K hours, X weighting, dt hours)."""
    den = 2 * K * (1 - X) + dt
    c0 = (dt - 2 * K * X) / den
    c1 = (dt + 2 * K * X) / den
    c2 = (2 * K * (1 - X) - dt) / den
    out = np.zeros_like(inflow)
    out[0] = inflow[0]
    for i in range(1, len(inflow)):
        out[i] = max(c0 * inflow[i] + c1 * inflow[i - 1] + c2 * out[i - 1], 0)
    return out


def cascade(storm_mm: float = 60, hours: float = 3, blockages: dict | None = None) -> dict:
    """Push a design storm through the network. Blockages: {node_id: fraction 0-1}."""
    blockages = blockages or {}
    dt = 0.25
    T = int(14 / dt)
    t = np.arange(T) * dt
    peak_i = storm_mm / hours * 1.6  # mm/h, triangular-ish storm
    rain = np.clip(peak_i * (1 - np.abs(t - hours / 2) / (hours / 2)), 0, None)
    info = {n[0]: n for n in NODES}
    flows, spill = {}, {}
    order = ["D1", "D2", "D4", "D3", "D5", "D6"]
    for nid in order:
        _, name, _, _, area, catch, down = info[nid]
        local = _expected_q(rain, catch)
        ups = [flows[k] for k, v in info.items() if v[6] == nid and k in flows]
        q_in = local + (sum(ups) if ups else 0)
        open_area = area * (1 - blockages.get(nid, 0))
        cap = CD * open_area * math.sqrt(2 * G * 0.9)  # what passes with 0.9 m of head before the street floods
        passed = np.minimum(q_in, cap)
        over = np.clip(q_in - cap, 0, None)
        spill[nid] = {"name": name, "volume_m3": round(float(over.sum() * dt * 3600)),
                      "starts_h": round(float(t[np.argmax(over > 0)]), 2) if over.max() > 0 else None,
                      "capacity_m3s": round(cap, 2), "peak_in_m3s": round(float(q_in.max()), 2)}
        flows[nid] = muskingum(passed, K=0.6 + 0.4 * blockages.get(nid, 0), X=0.2, dt=dt)
    out = flows["D6"]
    return {"t_h": t[::2].round(2).tolist(), "rain": rain[::2].round(1).tolist(),
            "outfall": out[::2].round(2).tolist(), "outfall_peak_h": round(float(t[np.argmax(out)]), 2),
            "spill": spill, "flooded": [k for k, v in spill.items() if v["volume_m3"] > 0]}


def network(lat: float, lon: float, blocked: str = "D3", readings: dict | None = None) -> dict:
    sim = simulate(blocked=blocked, readings=readings)
    k_lat = 1 / 111.0
    k_lon = 1 / (111.0 * math.cos(math.radians(lat)))
    for n in sim["nodes"]:
        n["lat"] = round(lat + n["dy"] * k_lat, 5)
        n["lon"] = round(lon + n["dx"] * k_lon, 5)
    blocks = {n["id"]: n["blockage_pct"] / 100 for n in sim["nodes"]}
    sim["cascade"] = cascade(60, 3, blocks)
    sim["cascade_clear"] = cascade(60, 3, {})
    return sim
