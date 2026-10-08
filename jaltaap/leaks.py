"""Pipe leaks: find new leaks early and point the repair crew to the right street.

Indian city networks lose 30-40% of treated water. Utilities mostly check
minimum night flow (MNF), which only notices big bursts. JalTaap layers
four ideas on the pressure + flow sensors a water board already has:

  1. Expected pressure. For each pressure sensor a Ridge regression learns
     pressure from the inlet flow and time of day over the *previous 7 days*
     only. Old leaks get absorbed into "normal"; a NEW leak breaks the
     pattern. (Detecting the onset is the solvable problem.)
  2. CUSUM. Daily residuals become robust z-scores (median / MAD) and a
     two-sided CUSUM piles up evidence, so a slow, small leak still trips
     the alarm after a few days, while one noisy day does not.
  3. Fingerprint localisation. A leak lowers nearby sensors more than far
     ones. We pre-compute the pressure "fingerprint" of a leak on every
     pipe (hydraulic distance decay over the network graph) and match the
     observed drop pattern by cosine similarity: top pipes -> zone.
  4. Isolation Forest per pipe on pressure, flow, acoustic vibration and
     water temperature, to grade severity (No leak / Normal / Medium / High).
  + A what-if model (Random Forest) that answers "with these sensor
     readings, how likely is a leak?", and batch scoring of CSV uploads.

Without a live SCADA feed the module runs a demo District Metered Area
(DMA) placed around the chosen city: 12 zones, ~40 pipes, 10 pressure
sensors, 28 days at 15-minute steps, with one sudden burst and one slowly
growing leak hidden in it. POST real readings to /api/leaks/reading.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

STEP_MIN = 15
DAYS = 28
PER_DAY = 24 * 60 // STEP_MIN
RUPEES_PER_KL = 22  # assumed treatment + pumping cost; set your board's figure
MATERIALS = {"CI": 0.8, "AC": 1.0, "DI": 0.4, "PVC": 0.5, "HDPE": 0.3, "GI": 0.9}


@lru_cache(maxsize=4)
def build_network(seed: int = 3):
    """Grid-like DMA: 4x3 zones, nodes on a jittered lattice, pipes between neighbours."""
    import networkx as nx
    rng = np.random.default_rng(seed)
    G = nx.Graph()
    W, H = 8, 6
    for y in range(H):
        for x in range(W):
            G.add_node((x, y), x=x + rng.uniform(-0.2, 0.2), y=y + rng.uniform(-0.2, 0.2),
                       zone=f"Z{(y // 2) * 4 + x // 2 + 1:02d}", elev=rng.uniform(0, 6))
    pipes = []
    for y in range(H):
        for x in range(W):
            for dx, dy in ((1, 0), (0, 1)):
                if x + dx < W and y + dy < H and rng.random() < 0.82:
                    a, b = (x, y), (x + dx, y + dy)
                    mat = rng.choice(list(MATERIALS), p=[0.2, 0.15, 0.25, 0.2, 0.1, 0.1])
                    age = int(rng.integers(3, 45))
                    G.add_edge(a, b, length=float(np.hypot(G.nodes[a]["x"] - G.nodes[b]["x"], G.nodes[a]["y"] - G.nodes[b]["y"])))
                    pipes.append({"id": f"P{len(pipes) + 1:03d}", "a": a, "b": b, "material": str(mat), "age": age,
                                  "zone": G.nodes[a]["zone"]})
    nodes = list(G.nodes)
    sensor_idx = rng.choice(len(nodes), size=10, replace=False)
    sensors = [nodes[i] for i in sensor_idx]
    inlet = (0, 0)
    return G, pipes, sensors, inlet


def sensitivity(G, pipes, sensors, lam: float = 1.6) -> np.ndarray:
    """Pressure drop at each sensor for a unit leak on each pipe (rows=sensors, cols=pipes)."""
    import networkx as nx
    dist = {s: nx.single_source_dijkstra_path_length(G, s, weight="length") for s in sensors}
    S = np.zeros((len(sensors), len(pipes)))
    for j, p in enumerate(pipes):
        for i, s in enumerate(sensors):
            d = 0.5 * (dist[s].get(p["a"], 99) + dist[s].get(p["b"], 99))
            S[i, j] = math.exp(-d / lam)
    return S


@lru_cache(maxsize=4)
def simulate(seed: int = 3):
    G, pipes, sensors, inlet = build_network(seed)
    rng = np.random.default_rng(seed + 10)
    n = DAYS * PER_DAY
    t = np.arange(n)
    hod = (t % PER_DAY) / PER_DAY * 24
    dow = (t // PER_DAY) % 7
    demand = 18 + 10 * np.exp(-0.5 * ((hod - 7.5) / 1.6) ** 2) + 8 * np.exp(-0.5 * ((hod - 19) / 2.0) ** 2)
    demand = demand * np.where(dow >= 5, 1.08, 1.0) * (1 + rng.normal(0, 0.03, n))  # L/s
    S = sensitivity(G, pipes, sensors)
    burst_pipe, slow_pipe = 7, int(len(pipes) * 0.7)
    burst = np.where(t >= 19 * PER_DAY + 37, 3.2, 0.0)  # sudden 3.2 L/s on day 19
    slow = np.clip((t - 9 * PER_DAY) / (12 * PER_DAY), 0, 1) * 2.0  # grows to 2 L/s over 12 days
    leak_flow = burst + slow
    inflow = demand + leak_flow
    base_p = np.array([28 + 6 * rng.random() - 0.4 * G.nodes[s]["elev"] for s in sensors])
    pressure = (base_p[:, None] - 0.012 * inflow[None, :] ** 1.6
                - 1.1 * (S[:, [burst_pipe]] * burst[None, :]) - 1.1 * (S[:, [slow_pipe]] * slow[None, :])
                + rng.normal(0, 0.08, (len(sensors), n)))
    return {"G": G, "pipes": pipes, "sensors": sensors, "S": S, "inflow": inflow, "pressure": pressure,
            "hod": hod, "truth": {"burst": pipes[burst_pipe]["id"], "slow": pipes[slow_pipe]["id"],
                                  "burst_day": 19, "slow_start_day": 9}}


def _features(inflow, hod):
    return np.c_[inflow, inflow ** 2, np.sin(2 * np.pi * hod / 24), np.cos(2 * np.pi * hod / 24),
                 np.sin(4 * np.pi * hod / 24), np.cos(4 * np.pi * hod / 24)]


def detect(sim: dict, k: float = 0.5, h: float = 4.0) -> dict:
    from sklearn.linear_model import Ridge
    P, q, hod = sim["pressure"], sim["inflow"], sim["hod"]
    X = _features(q, hod)
    n_s = P.shape[0]
    daily_res = np.full((n_s, DAYS), np.nan)
    for d in range(7, DAYS):
        tr = slice((d - 7) * PER_DAY, d * PER_DAY)
        te = slice(d * PER_DAY, (d + 1) * PER_DAY)
        for i in range(n_s):
            m = Ridge(alpha=1.0).fit(X[tr], P[i, tr])
            daily_res[i, d] = float(np.mean(P[i, te] - m.predict(X[te])))
    # robust z against each sensor's own recent residual history, then CUSUM
    cus_lo = np.zeros((n_s, DAYS))
    z = np.zeros((n_s, DAYS))
    for i in range(n_s):
        for d in range(8, DAYS):
            hist = daily_res[i, 7:d]
            med = np.nanmedian(hist)
            mad = np.nanmedian(np.abs(hist - med)) * 1.4826 or 0.02
            z[i, d] = (daily_res[i, d] - med) / max(mad, 0.02)
            cus_lo[i, d] = max(0.0, cus_lo[i, d - 1] - z[i, d] - k)  # pressure DROPS when a leak opens
    alarm_day = None
    for d in range(8, DAYS):
        if (cus_lo[:, d] > h).sum() >= 2:  # two sensors must agree
            alarm_day = d
            break
    # minimum night flow 2-4 am, the utility's usual check
    mnf = [float(np.min(q[d * PER_DAY + 8: d * PER_DAY + 16])) for d in range(DAYS)]
    base_mnf = float(np.median(mnf[:7]))
    mnf_alarm = next((d for d, v in enumerate(mnf) if v > base_mnf * 1.25), None)
    return {"daily_residual": daily_res, "z": z, "cusum": cus_lo, "alarm_day": alarm_day, "mnf": mnf,
            "mnf_alarm_day": mnf_alarm, "mnf_base": base_mnf}


def localise(sim: dict, det: dict, day: int, window: int = 3) -> list[dict]:
    """Match the pressure drop pattern around `day` to every pipe's fingerprint."""
    R = det["daily_residual"]
    before = np.nanmean(R[:, max(7, day - 6):max(8, day - 1)], axis=1)
    after = np.nanmean(R[:, day:min(DAYS, day + window)], axis=1)
    shift = np.nan_to_num(before - after)  # positive = pressure fell
    S = sim["S"]
    sim_cos = (S.T @ shift) / (np.linalg.norm(S, axis=0) * (np.linalg.norm(shift) or 1))
    order = np.argsort(-sim_cos)
    out = []
    for j in order[:5]:
        p = sim["pipes"][j]
        out.append({"pipe": p["id"], "zone": p["zone"], "match": round(float(sim_cos[j]), 3)})
    return out


def _pipe_sensor_table(sim: dict, seed: int = 5):
    """Per-pipe readings for the latest hour: pressure, flow, vibration, temperature."""
    rng = np.random.default_rng(seed)
    S = sim["S"]
    p_now = sim["pressure"][:, -4:].mean(axis=1)
    leak_on = {sim["truth"]["burst"]: 3.2, sim["truth"]["slow"]: 2.0}
    rows = []
    for j, p in enumerate(sim["pipes"]):
        w = S[:, j] / S[:, j].sum()
        pressure = float(w @ p_now)
        lf = leak_on.get(p["id"], 0.0)
        flow = float(rng.normal(6, 1.2) + lf * 1.4)
        vib = float(abs(rng.normal(0.8, 0.25)) + lf * 0.9 + MATERIALS[p["material"]] * 0.15)
        temp = float(rng.normal(24, 0.6) - lf * 0.3)
        rows.append({"pipe": p["id"], "zone": p["zone"], "material": p["material"], "age": p["age"],
                     "pressure": round(pressure, 2), "flow": round(flow, 2), "vibration": round(vib, 2),
                     "temperature": round(temp, 2), "true_leak": lf > 0})
    return rows


def grade(rows: list[dict]) -> list[dict]:
    from sklearn.ensemble import IsolationForest
    X = np.array([[r["pressure"], r["flow"], r["vibration"], r["temperature"]] for r in rows])
    iso = IsolationForest(n_estimators=200, contamination=0.06, random_state=1).fit(X)
    s = -iso.score_samples(X)  # higher = more anomalous
    lo, hi = np.percentile(s, 50), np.percentile(s, 97)
    for r, v in zip(rows, s):
        f = (v - lo) / (hi - lo + 1e-9)
        r["anomaly"] = round(float(v), 3)
        r["severity"] = "High" if f >= 0.9 else "Medium" if f >= 0.6 else "Normal" if f >= 0.2 else "No leak"
    return rows


@lru_cache(maxsize=1)
def whatif_model():
    """Random Forest on synthetic but physically shaped data (pressure drop, flow, vibration, age, material)."""
    from sklearn.ensemble import RandomForestClassifier
    rng = np.random.default_rng(11)
    n = 6000
    pressure = rng.normal(25, 4, n)
    flow = rng.normal(6, 1.5, n)
    vib = np.abs(rng.normal(0.9, 0.4, n))
    temp = rng.normal(24, 1.5, n)
    age = rng.integers(1, 60, n)
    mat = rng.choice(list(MATERIALS), n)
    frag = np.array([MATERIALS[m] for m in mat])
    logit = (-4.2 + 0.35 * (25 - pressure) + 0.6 * (flow - 6) + 2.1 * (vib - 0.9) + 0.035 * age + 1.4 * frag
             - 0.25 * (temp - 24))
    y = rng.random(n) < 1 / (1 + np.exp(-logit))
    X = np.c_[pressure, flow, vib, temp, age, frag]
    m = RandomForestClassifier(n_estimators=160, max_depth=8, random_state=0).fit(X, y)
    names = ["Pressure", "Flow", "Vibration", "Temperature", "Pipe age", "Material fragility"]
    return m, names


def whatif(pressure: float, flow: float, vibration: float, temperature: float, age: int, material: str) -> dict:
    m, names = whatif_model()
    x = np.array([[pressure, flow, vibration, temperature, age, MATERIALS.get(material, 0.6)]])
    p = float(m.predict_proba(x)[0, 1])
    return {"probability": round(p, 3), "verdict": "likely leak" if p >= 0.6 else "possible leak" if p >= 0.3 else "no leak",
            "importance": sorted([{"feature": n, "weight": round(float(w), 3)} for n, w in zip(names, m.feature_importances_)],
                                 key=lambda r: -r["weight"])}


def batch(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        try:
            res = whatif(float(r.get("Pressure", r.get("pressure", 25))), float(r.get("Flow_Rate", r.get("flow", 6))),
                         float(r.get("Vibration", r.get("vibration", 1))), float(r.get("Temperature", r.get("temperature", 24))),
                         int(float(r.get("Pipe_Age", r.get("age", 20)))), str(r.get("Material", r.get("material", "CI"))))
            out.append({**r, "leak_probability": res["probability"], "verdict": res["verdict"]})
        except (TypeError, ValueError):
            out.append({**r, "leak_probability": None, "verdict": "bad row"})
    return out


def overview(lat: float, lon: float) -> dict:
    sim = simulate()
    det = detect(sim)
    day = det["alarm_day"] if det["alarm_day"] is not None else DAYS - 3
    suspects = localise(sim, det, day)
    rows = grade(_pipe_sensor_table(sim))
    G = sim["G"]
    k_lat, k_lon = 0.0045, 0.0045 / math.cos(math.radians(lat))  # ~500 m per lattice step
    x0, y0 = 3.5, 2.5

    def ll(node):
        return [round(lat + (G.nodes[node]["y"] - y0) * k_lat, 5), round(lon + (G.nodes[node]["x"] - x0) * k_lon, 5)]
    sev = {r["pipe"]: r for r in rows}
    pipes = [{"id": p["id"], "zone": p["zone"], "material": p["material"], "age": p["age"], "line": [ll(p["a"]), ll(p["b"])],
              "severity": sev[p["id"]]["severity"]} for p in sim["pipes"]]
    sensors = []
    for i, s in enumerate(sim["sensors"]):
        sensors.append({"id": f"S{i + 1:02d}", "latlon": ll(s), "zone": G.nodes[s]["zone"],
                        "cusum": [round(float(v), 2) for v in det["cusum"][i]],
                        "tripped": bool(det["cusum"][i].max() > 4.0)})
    # second, later alarm after the first is fixed: look again in the last days
    later, later_day = None, None
    if det["alarm_day"] is not None and det["alarm_day"] < DAYS - 6:
        R = det["daily_residual"]
        drops = {d: float(np.nanmean(R[:, d - 3:d]) - np.nanmean(R[:, d:d + 2]))
                 for d in range(det["alarm_day"] + 3, DAYS - 1)}
        later_day = max(drops, key=drops.get)
        if drops[later_day] > 0.05:
            later = localise(sim, det, later_day)
        else:
            later_day = None
    excess = max(0.0, det["mnf"][-1] - det["mnf_base"])  # L/s lost at night ~ leakage
    lost_m3_day = excess * 86.4
    zones = {}
    for p in pipes:
        z = zones.setdefault(p["zone"], {"zone": p["zone"], "pipes": 0, "high": 0, "medium": 0})
        z["pipes"] += 1
        z["high"] += p["severity"] == "High"
        z["medium"] += p["severity"] == "Medium"
    return {"pipes": pipes, "sensors": sensors, "suspects": suspects, "later_suspects": later, "later_day": later_day,
            "alarm_day": det["alarm_day"], "mnf_alarm_day": det["mnf_alarm_day"], "days": DAYS,
            "mnf": [round(v, 2) for v in det["mnf"]], "mnf_base": round(det["mnf_base"], 2),
            "lost_m3_day": round(lost_m3_day), "lost_rupees_month": round(lost_m3_day * 30 * RUPEES_PER_KL),
            "pipe_table": sorted(rows, key=lambda r: -r["anomaly"])[:12], "zones": sorted(zones.values(), key=lambda z: -z["high"]),
            "truth": sim["truth"], "demo": True,
            "method": "Ridge expected-pressure (7-day trailing) -> robust z -> two-sided CUSUM; cosine fingerprint "
                      "localisation; Isolation Forest severity"}
