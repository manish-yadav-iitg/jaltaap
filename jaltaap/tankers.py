"""Fair water tankers: who gets water next, by which tanker, by what road.

Every summer tanker dispatch in Indian towns runs on phone calls and
pressure. JalTaap replaces that with an open, explainable pipeline:

  1. Requests come from a responsible person per area (ward officer, society
     secretary, slum volunteer, hospital or school head), not from every
     household, so the queue stays clean.
  2. Each request gets a Priority Score (0-100) with its reasons shown:
       Water Stress Index of the area  (rain deficit 40%, groundwater 40%,
                                        days without supply 20%)
       people affected, critical institutions (hospital > school > slum),
       how long it has waited, and repeat complaints.
  3. Allocation is greedy-fair: the highest score is served first by the
     tanker that can reach it soonest with enough water; small requests are
     chained onto the same trip (nearest-neighbour + 2-opt route).
  4. Each delivery gets a one-time code (sent by Amazon SNS when configured).
     The driver enters it at the drop, so "delivered" means delivered.
  5. A diversion check flags GPS tracks that leave the planned route or stop
     too long, and an Equity Score shows if some areas keep getting less.
  6. Demand forecast: litres needed over the next 7 days rise with the heat
     forecast (+3% per °C above 35) and with how long supply has been out.
"""
from __future__ import annotations

import math
import random
import time

import numpy as np

from . import aws, store

LPCD = 55  # litres per person per day, Jal Jeevan Mission norm
INSTITUTION = {"hospital": 30, "school": 18, "slum": 16, "elderly_home": 22, "society": 4, "village": 10, "other": 0}
ROLES = ["Ward officer", "Society secretary", "Slum volunteer", "Hospital admin", "School principal", "Sarpanch"]
SPEED_KMH = 22  # loaded tanker in town traffic
DETOUR = 1.35  # road distance vs straight line


def hav_km(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    d = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(d))


def water_stress_index(rain_deficit_pct: float | None, gw_stress: float | None, days_without: float) -> dict:
    rd = float(np.clip(-(rain_deficit_pct or 0), 0, 100))  # 60% deficit -> 60
    gw = float(gw_stress if gw_stress is not None else 50)
    dw = float(np.clip(days_without / 10 * 100, 0, 100))  # 10 days dry = max
    wsi = 0.4 * rd + 0.4 * gw + 0.2 * dw
    level = "critical" if wsi >= 70 else "high" if wsi >= 50 else "moderate" if wsi >= 30 else "safe"
    return {"wsi": round(wsi), "level": level, "parts": {"rain deficit": round(rd), "groundwater": round(gw),
                                                         "days without water": round(dw)}}


def priority(req: dict, wsi: float, now: float | None = None) -> dict:
    now = now or time.time()
    reasons = []
    s_wsi = 0.35 * wsi
    reasons.append(f"Area water stress {round(wsi)}/100 (+{s_wsi:.0f})")
    pop = max(1, int(req.get("population", 100)))
    s_pop = min(20, 6 * math.log10(pop))
    reasons.append(f"{pop:,} people affected (+{s_pop:.0f})")
    inst = INSTITUTION.get(req.get("institution", "other"), 0)
    if inst:
        reasons.append(f"{req['institution'].replace('_', ' ').title()} (+{inst})")
    dw = float(req.get("days_without", 0))
    s_days = min(15, dw * 2)
    if dw:
        reasons.append(f"{dw:g} days without supply (+{s_days:.0f})")
    waited_h = (now - req.get("ts", now)) / 3600
    s_wait = min(10, waited_h / 2.4)
    if waited_h >= 1:
        reasons.append(f"Waiting {waited_h:.0f} h (+{s_wait:.0f})")
    s_rep = min(6, 2 * int(req.get("complaints", 0)))
    if s_rep:
        reasons.append(f"{req['complaints']} unresolved complaints (+{s_rep})")
    score = min(100, s_wsi + s_pop + inst + s_days + s_wait + s_rep)
    level = "CRITICAL" if score >= 70 else "HIGH" if score >= 50 else "MEDIUM" if score >= 30 else "LOW"
    return {"score": round(score), "level": level, "reasons": reasons}


def litres_needed(req: dict, heat_feels_max: float | None = None, days: int = 1) -> int:
    pop = int(req.get("population", 100))
    bump = 1 + 0.03 * max(0, (heat_feels_max or 35) - 35)
    base = LPCD * pop * days * bump
    if req.get("institution") == "hospital":
        base += 450 * int(req.get("beds", 50)) * days  # ~450 L per bed per day
    return int(math.ceil(base / 500) * 500)


# ---------------------------------------------------------------- fleet
def fleet(lat: float, lon: float) -> list[dict]:
    saved = store.items("tanker_fleet")
    if saved:
        return saved
    rng = random.Random(round(lat * 100) + round(lon * 100))
    depots = [("North filling point", 0.035, -0.01), ("South filling point", -0.03, 0.02)]
    out = []
    for i in range(6):
        name, dy, dx = depots[i % 2]
        out.append(store.put("tanker_fleet", {
            "id": f"TK-{101 + i}", "capacity_l": [10000, 12000, 6000][i % 3], "depot": name,
            "lat": round(lat + dy + rng.uniform(-0.004, 0.004), 5), "lon": round(lon + dx + rng.uniform(-0.004, 0.004), 5),
            "driver": ["Ramesh", "Imran", "Sunita", "Gurpreet", "Arjun", "Fatima"][i], "status": "available"}))
    return out


def add_request(d: dict) -> dict:
    d = {k: v for k, v in d.items() if v is not None}
    d.setdefault("status", "queued")
    d.setdefault("complaints", 0)
    item = store.put("tanker_request", d)
    store.audit("tanker_request", request=item["id"], by=d.get("role"), area=d.get("area"))
    return item


def seed_demo(lat: float, lon: float) -> list[dict]:
    if store.items("tanker_request"):
        return store.items("tanker_request")
    rng = random.Random(42)
    demo = [("Indira Nagar basti", "Slum volunteer", "slum", 1800, 4), ("District hospital", "Hospital admin", "hospital", 600, 1),
            ("Govt girls school", "School principal", "school", 900, 2), ("Shanti Vihar society", "Society secretary", "society", 450, 1),
            ("Old age home, Ram Gali", "Ward officer", "elderly_home", 80, 3), ("Kheri village", "Sarpanch", "village", 2400, 6),
            ("Railway colony", "Ward officer", "other", 700, 2)]
    out = []
    for name, role, inst, pop, days in demo:
        out.append(add_request({"area": name, "role": role, "institution": inst, "population": pop, "days_without": days,
                                "beds": 120 if inst == "hospital" else None,
                                "lat": round(lat + rng.uniform(-0.04, 0.04), 5), "lon": round(lon + rng.uniform(-0.04, 0.04), 5),
                                "ts": time.time() - rng.uniform(0.5, 30) * 3600, "complaints": rng.choice([0, 0, 1, 2])}))
    return out


# ---------------------------------------------------------------- routing
def tsp_route(start: tuple, stops: list[tuple]) -> list[int]:
    """Nearest neighbour then 2-opt. Returns the visiting order of `stops`."""
    if len(stops) <= 1:
        return list(range(len(stops)))
    left, order, cur = set(range(len(stops))), [], start
    while left:
        j = min(left, key=lambda k: hav_km(cur, stops[k]))
        order.append(j)
        left.remove(j)
        cur = stops[j]

    def length(o):
        pts = [start] + [stops[k] for k in o]
        return sum(hav_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    improved = True
    while improved:
        improved = False
        for i in range(len(order) - 1):
            for k in range(i + 1, len(order)):
                new = order[:i] + order[i:k + 1][::-1] + order[k + 1:]
                if length(new) < length(order) - 1e-9:
                    order, improved = new, True
    return order


def allocate(lat: float, lon: float, wsi_lookup, heat_feels: float | None = None) -> dict:
    """Plan the next round of trips. wsi_lookup(req) -> stress 0-100 for that request's area."""
    reqs = [r for r in store.items("tanker_request") if r.get("status") in ("queued", None)]
    tanks = [t for t in fleet(lat, lon) if t.get("status", "available") == "available"]
    now = time.time()
    scored = []
    for r in reqs:
        w = wsi_lookup(r)
        pr = priority(r, w["wsi"], now)
        scored.append({**r, "wsi": w, "priority": pr, "litres": litres_needed(r, heat_feels)})
    scored.sort(key=lambda r: -r["priority"]["score"])
    trips, waiting = [], []
    free = {t["id"]: dict(t, left=t["capacity_l"], stops=[]) for t in tanks}
    for r in scored:
        best = None
        for t in free.values():
            if t["left"] <= 0:
                continue
            here = (t["stops"][-1]["lat"], t["stops"][-1]["lon"]) if t["stops"] else (t["lat"], t["lon"])
            eta = hav_km(here, (r["lat"], r["lon"])) * DETOUR / SPEED_KMH * 60
            # prefer a tanker that can carry it all; else take what's left
            key = (0 if t["left"] >= r["litres"] else 1, eta)
            if best is None or key < best[0]:
                best = (key, t, eta)
        if best is None:
            waiting.append(r)
            continue
        _, t, eta = best
        give = min(t["left"], r["litres"])
        t["left"] -= give
        t["stops"].append({**r, "give_l": give, "short_l": r["litres"] - give})
    for t in free.values():
        if not t["stops"]:
            continue
        pts = [(s["lat"], s["lon"]) for s in t["stops"]]
        order = tsp_route((t["lat"], t["lon"]), pts)
        stops = [t["stops"][i] for i in order]
        path = [(t["lat"], t["lon"])] + [(s["lat"], s["lon"]) for s in stops]
        road = aws.location_route(path)
        km = road["km"] if road else round(sum(hav_km(path[i], path[i + 1]) for i in range(len(path) - 1)) * DETOUR, 1)
        mins = road["minutes"] if road else round(km / SPEED_KMH * 60 + 12 * len(stops))  # 12 min per drop
        clock, legs = 0.0, []
        for i, s in enumerate(stops):
            leg = hav_km(path[i], path[i + 1]) * DETOUR
            clock += leg / SPEED_KMH * 60 + (12 if i else 0)
            legs.append({"id": s["id"], "area": s.get("area"), "lat": s["lat"], "lon": s["lon"], "give_l": s["give_l"],
                         "short_l": s["short_l"], "eta_min": round(clock), "priority": s["priority"], "wsi": s["wsi"],
                         "institution": s.get("institution")})
        trips.append({"tanker": t["id"], "driver": t.get("driver"), "capacity_l": t["capacity_l"], "depot": t.get("depot"),
                      "from": {"lat": t["lat"], "lon": t["lon"]}, "stops": legs, "km": km, "minutes": mins,
                      "line": road["line"] if road else [list(p) for p in path], "routed_by": road["by"] if road else
                      "straight-line x1.35 (set JALTAAP_ROUTE_CALCULATOR for Amazon Location roads)"})
    return {"trips": trips, "queue": [{k: r[k] for k in ("id", "area", "priority", "wsi", "litres", "institution",
                                                       "population", "days_without", "lat", "lon", "role")
                                       if k in r} for r in scored],
            "unserved": [r["id"] for r in waiting], "fleet": tanks}


def dispatch(trip: dict, phone: str | None = None) -> dict:
    """Lock a planned trip: mark requests dispatched and issue one-time delivery codes."""
    codes = {}
    for s in trip["stops"]:
        code = f"{random.SystemRandom().randint(0, 999999):06d}"
        req = store.get("tanker_request", s["id"]) or {"id": s["id"]}
        req.update({"status": "dispatched", "tanker": trip["tanker"], "otp": code, "eta_min": s["eta_min"],
                    "give_l": s["give_l"], "dispatched_ts": time.time()})
        store.put("tanker_request", req, rid=s["id"])
        sms = aws.send_sms(f"JalTaap: tanker {trip['tanker']} is coming to {s.get('area')} in about {s['eta_min']} min "
                           f"with {s['give_l']:,} L. Give this code to the driver only when water is delivered: {code}",
                           phone or req.get("phone"))
        codes[s["id"]] = {"otp_sent_via": sms.get("via"), "area": s.get("area")}
    store.audit("tanker_dispatch", tanker=trip["tanker"], stops=[s["id"] for s in trip["stops"]])
    return {"dispatched": len(codes), "codes": codes}


def confirm(rid: str, otp: str, litres: int | None = None) -> dict:
    req = store.get("tanker_request", rid)
    if not req:
        return {"ok": False, "reason": "no such request"}
    if str(req.get("otp")) != str(otp).strip():
        store.audit("tanker_otp_fail", request=rid)
        return {"ok": False, "reason": "code does not match"}
    req.update({"status": "delivered", "delivered_ts": time.time(), "delivered_l": litres or req.get("give_l")})
    req.pop("otp", None)
    store.put("tanker_request", req, rid=rid)
    store.audit("tanker_delivered", request=rid, litres=req["delivered_l"])
    return {"ok": True, "request": req}


def diversion_check(planned: list[list[float]], track: list[dict], max_off_km: float = 0.6,
                    max_stop_min: float = 20) -> dict:
    """track = [{'lat','lon','ts'}...]. Flags points far from the planned line and long stops."""
    def off(p):
        best = 1e9
        for i in range(len(planned) - 1):
            a, b = planned[i], planned[i + 1]
            for f in np.linspace(0, 1, 12):
                q = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
                best = min(best, hav_km((p["lat"], p["lon"]), q))
        return best
    flags, worst = [], 0.0
    for p in track:
        d = off(p)
        worst = max(worst, d)
        if d > max_off_km:
            flags.append({"type": "off route", "km": round(d, 2), "lat": p["lat"], "lon": p["lon"]})
    for a, b in zip(track, track[1:]):
        if hav_km((a["lat"], a["lon"]), (b["lat"], b["lon"])) < 0.05 and (b["ts"] - a["ts"]) / 60 > max_stop_min:
            flags.append({"type": "long stop", "minutes": round((b["ts"] - a["ts"]) / 60), "lat": a["lat"], "lon": a["lon"]})
    gaps = [b["ts"] - a["ts"] for a, b in zip(track, track[1:])]
    if gaps and max(gaps) > 15 * 60:
        flags.append({"type": "GPS off", "minutes": round(max(gaps) / 60)})
    risk = min(100, 30 * sum(f["type"] == "off route" for f in flags[:3]) + 25 * sum(f["type"] == "long stop" for f in flags)
               + 20 * any(f["type"] == "GPS off" for f in flags))
    return {"risk": risk, "flags": flags, "max_off_km": round(worst, 2),
            "verdict": "needs review" if risk >= 40 else "watch" if risk >= 20 else "clean"}


def equity() -> dict:
    """Litres per person delivered vs need, per area; Gini across areas (0 = perfectly fair)."""
    rows = store.items("tanker_request")
    areas = {}
    for r in rows:
        a = areas.setdefault(r.get("area", "?"), {"area": r.get("area"), "need": 0, "got": 0, "pop": r.get("population", 0)})
        a["need"] += litres_needed(r)
        a["got"] += r.get("delivered_l") or 0
    vals = [a["got"] / a["need"] for a in areas.values() if a["need"]]
    if not vals:
        return {"gini": None, "areas": []}
    v = np.sort(np.array(vals))
    n = len(v)
    gini = float((2 * np.arange(1, n + 1) - n - 1).dot(v) / (n * v.sum())) if v.sum() else 0.0
    score = round(100 * (1 - gini)) if v.sum() else None
    return {"gini": round(gini, 2) if v.sum() else None, "equity_score": score,
            "areas": sorted([{**a, "served_pct": round(100 * a["got"] / a["need"]) if a["need"] else 0}
                             for a in areas.values()], key=lambda a: a["served_pct"])}


def demand_forecast(req: dict, feels_by_day: list[tuple[str, float]]) -> list[dict]:
    return [{"date": d, "litres": litres_needed(req, f)} for d, f in feels_by_day]
