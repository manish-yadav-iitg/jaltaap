"""JalTaap web server (FastAPI). Serves the web app in /web and a JSON API.

    python server.py        -> open http://localhost:8000

Desks and their APIs
  Pulse        /api/board, /api/place, /api/alert, /api/ask, /api/bulletin
  Heat         /api/heat/load, /api/heat/pockets, /api/heat/vulnerability, /api/heat/drill, /api/workheat
  Flood        /api/flood/waterlog, /api/flood/drains, /api/flood/cascade, /api/replay, /api/chronos
  Drought      /api/drought, /api/drought/crops, /api/drought/recharge, /api/drought/budget, /api/drought/satellite
  Groundwater  /api/groundwater, /api/groundwater/reading
  Tankers      /api/tankers, /api/tankers/request|dispatch|confirm|diversion|equity|reset
  Leaks        /api/leaks, /api/leaks/whatif, /api/leaks/batch
  Field        /api/reports, /api/reports/photo, /api/route
  AWS          /api/aws/status, /api/aws/outbox, /api/map-config, /api/dispatch
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import os
import threading
import time

import pandas as pd
import uvicorn
from dotenv import load_dotenv

load_dotenv()  # before importing jaltaap so AWS settings in .env are seen

# On Vercel the code folder is read-only; only /tmp can be written (and it is wiped between cold starts).
# Point the HTTP cache, the SQLite fallback and local file uploads there. Set DynamoDB/S3 to keep data.
ON_VERCEL = bool(os.environ.get("VERCEL"))
if ON_VERCEL:
    os.environ.setdefault("JALTAAP_CACHE", "/tmp/jaltaap-cache")
    os.environ.setdefault("JALTAAP_DB", "/tmp/jaltaap.db")
    os.environ.setdefault("JALTAAP_FILES", "/tmp/jaltaap-files")

from fastapi import Body, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from jaltaap import aws, floodhub, reports, store  # noqa: E402
from jaltaap.alerts import LANGS, PERSONAS, overall_level, template_alert  # noqa: E402
from jaltaap.flood import flood_risk, forecast, history  # noqa: E402
from jaltaap.heat import extreme_days_per_year, heat_history, heat_risk, warm_nights_per_year  # noqa: E402
from jaltaap.heatstress import hourly_heat_stress  # noqa: E402
from jaltaap.http import get_json  # noqa: E402
from jaltaap.national import heat_board, river_board  # noqa: E402
from jaltaap.rain import rain_risk  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
OUT = os.path.join(ROOT, "outputs")
app = FastAPI(title="JalTaap API", version="2.0")

# ---------- tiny in-memory cache for slow calls ----------
_cache: dict = {}
_lock = threading.Lock()


def cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def peek(key):
    with _lock:
        hit = _cache.get(key)
    return hit[1] if hit else None


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:  # keep the page alive even if one source fails
        return {"error": str(e)}


def _run(fn, *a, **k):
    """Like _safe but turns failures into a clean 502 the UI can show."""
    try:
        return fn(*a, **k)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"{e.__class__.__name__}: {str(e)[:300]}")


def _k(lat, lon, p=3):
    return f"{lat:.{p}f}:{lon:.{p}f}"


# ---------- national board ----------
@app.get("/api/board")
def board():
    def build():
        rivers = _safe(river_board)
        cities = _safe(heat_board)
        return {"updated": dt.datetime.now().strftime("%d %b %Y, %H:%M"),
                "rivers": rivers if isinstance(rivers, list) else [],
                "cities": cities if isinstance(cities, list) else [],
                "errors": [x["error"] for x in (rivers, cities) if isinstance(x, dict)]}
    return cached("board", 3 * 3600, build)


# ---------- one place ----------
@app.get("/api/search")
def search(q: str):
    loc = aws.location_search(q)
    if loc:
        return loc
    d = get_json("https://geocoding-api.open-meteo.com/v1/search",
                 {"name": q, "count": 8, "language": "en", "countryCode": "IN"})
    return [{"name": r["name"], "state": r.get("admin1", ""), "district": r.get("admin2", ""),
             "lat": r["latitude"], "lon": r["longitude"]} for r in d.get("results", [])]


@app.get("/api/place")
def place(lat: float, lon: float):
    key = f"place:{_k(lat, lon)}"

    def build():
        fl = _safe(flood_risk, lat, lon)
        ra = _safe(rain_risk, lat, lon)
        he = _safe(heat_risk, lat, lon)
        chart = None
        if "error" not in fl:
            c = fl["river_cell"]
            fc = forecast(c["lat"], c["lon"], days=30)
            mem = fc[[k for k in fc.columns if k.startswith("river_discharge_member")]]
            today = pd.Timestamp(dt.date.today())
            mem = mem[mem.index >= today]
            past = history(c["lat"], c["lon"]).iloc[-90:]
            chart = {
                "past": {"t": [d.date().isoformat() for d in past.index], "v": [round(v) for v in past.values]},
                "fc": {"t": [d.date().isoformat() for d in mem.index],
                       "p10": [round(v) for v in mem.quantile(0.1, axis=1)],
                       "p50": [round(v) for v in mem.median(axis=1)],
                       "p90": [round(v) for v in mem.quantile(0.9, axis=1)]},
            }
        levels = [x.get(k) for x, k in ((fl, "level_peak"), (ra, "level"), (he, "level")) if "error" not in x]
        google = {"enabled": floodhub.enabled(), "link": floodhub.hub_link(lat, lon), "gauge": None}
        if floodhub.enabled():
            g = _safe(floodhub.nearest, lat, lon)
            if g and "error" not in g:
                google["gauge"] = g
                google["verdict"] = floodhub.compare(fl.get("level_peak", "green") if "error" not in fl else "green", g["level"])
            elif g:
                google["error"] = g["error"]
        lvl = overall_level(*levels)
        return {"lat": lat, "lon": lon, "flood": fl, "rain": ra, "heat": he, "chart": chart, "google": google,
                "level": lvl}
    return cached(key, 3 * 3600, build)


@app.get("/api/workheat")
def workheat(lat: float, lon: float):
    return cached(f"work:{_k(lat, lon)}", 3600, lambda: hourly_heat_stress(lat, lon))


@app.get("/api/chronos")
def chronos(lat: float, lon: float):
    def build():
        h = history(lat, lon)
        from jaltaap.forecast import chronos_forecast
        fc = chronos_forecast(h, horizon=10)
        return {"t": [d.date().isoformat() for d in fc.index], "p10": fc["p10"].round().tolist(),
                "p50": fc["p50"].round().tolist(), "p90": fc["p90"].round().tolist(), "by": "Chronos on this computer"}
    return cached(f"chronos:{_k(lat, lon)}", 6 * 3600, lambda: _run(build))


@app.get("/api/alert")
def alert(lat: float, lon: float, lang: str = "hi", persona: str = "family", llm: bool = False):
    if llm:
        try:
            from jaltaap.agent import make_alert  # needs strands-agents (requirements-extras.txt)
        except ImportError:
            make_alert = None
        if make_alert:
            a = make_alert(lat, lon, lang, use_llm=True, persona=persona)
            if a["by"] != "template":
                return a
    p = place(lat, lon)
    ok = lambda x: None if "error" in x else x  # noqa: E731
    work = _safe(workheat, lat, lon)
    if lang in ("en", "hi"):
        return {"text": template_alert(ok(p["flood"]), ok(p["heat"]), ok(p["rain"]), lang, persona, ok(work)),
                "by": "template", "lang": lang}
    en = template_alert(ok(p["flood"]), ok(p["heat"]), ok(p["rain"]), "en", persona, ok(work))
    tr = aws.translate(en, lang)
    if tr:
        return {"text": tr, "by": "Amazon Bedrock", "lang": lang}
    return {"text": en, "by": "template", "lang": "en"}


@app.get("/api/meta")
def meta():
    from jaltaap.drought import SOILS
    from jaltaap.groundwater import AQUIFERS, USAGE
    from jaltaap.tankers import INSTITUTION, ROLES
    from jaltaap.vulnerability import FACTORS, PRESETS, SCENARIOS
    return {"langs": LANGS, "personas": {k: v["label"] for k, v in PERSONAS.items()},
            "aquifers": {k: v[0] for k, v in AQUIFERS.items()}, "usage": {k: v[0] for k, v in USAGE.items()},
            "soils": {k: v[0] for k, v in SOILS.items()}, "roles": ROLES, "institutions": list(INSTITUTION),
            "presets": PRESETS, "factors": {k: v[0] for k, v in FACTORS.items()},
            "drills": {k: v["label"] for k, v in SCENARIOS.items()}}


# ---------- Google Flood Hub ----------
@app.get("/api/floodhub")
def floodhub_all():
    if not floodhub.enabled():
        return {"enabled": False, "gauges": [],
                "how": "Add GOOGLE_FLOODHUB_API_KEY to .env (free, join the waitlist at developers.google.com/flood-forecasting)"}
    try:
        g = floodhub.india_statuses()
    except Exception as e:
        return {"enabled": True, "gauges": [], "error": str(e)}
    keep = ("gauge_id", "lat", "lon", "level", "severity", "trend", "river", "site", "verified")
    return {"enabled": True, "attribution": floodhub.ATTRIBUTION,
            "gauges": [{k: x.get(k) for k in keep} for x in g]}


@app.get("/api/floodhub/polygon/{pid}")
def floodhub_polygon(pid: str):
    if not floodhub.enabled():
        raise HTTPException(400, "Google Flood Hub key not set")
    return cached(f"poly:{pid}", 6 * 3600, lambda: floodhub.polygon_geojson(pid))


# ---------- replay + scorecard ----------
@app.get("/api/replay")
def replay():
    p = os.path.join(OUT, "replay_results.json")
    if not os.path.exists(p):
        raise HTTPException(404, "run: python -m jaltaap.replay")
    evs = {e["name"]: e for e in json.load(open(os.path.join(ROOT, "data", "events.json"), encoding="utf-8"))}
    res = json.load(open(p, encoding="utf-8"))
    for r in res:
        ev = evs.get(r["name"], {})
        r["id"], r["lat"], r["lon"] = ev.get("id"), ev.get("lat"), ev.get("lon")
        r["honest_miss"] = ev.get("honest_miss", False)
    return res


@app.get("/api/scorecard")
def scorecard():
    p = os.path.join(OUT, "scorecard.json")
    return json.load(open(p)) if os.path.exists(p) else {}


# ---------- heat desk ----------
@app.get("/api/heat-trend")
def heat_trend(lat: float, lon: float):
    def build():
        h = heat_history(lat, lon)
        s = extreme_days_per_year(h)
        n = warm_nights_per_year(h).reindex(s.index, fill_value=0)
        return {"years": [int(y) for y in s.index], "days": [int(v) for v in s.values],
                "nights": [int(v) for v in n.values]}
    return cached(f"trend:{_k(lat, lon, 2)}", 24 * 3600, lambda: _run(build))


@app.get("/api/heat/load")
def heat_load(lat: float, lon: float):
    from jaltaap.thermal import body_heat_load
    return cached(f"load:{_k(lat, lon)}", 3600, lambda: _run(body_heat_load, lat, lon))


@app.get("/api/heat/pockets")
def heat_pockets(lat: float, lon: float):
    from jaltaap.microclimate import heat_pockets as hp
    return cached(f"pockets:{_k(lat, lon, 2)}", 3 * 3600, lambda: _run(hp, lat, lon))


class Vuln(BaseModel):
    lat: float
    lon: float
    profile: dict


@app.post("/api/heat/vulnerability")
def heat_vulnerability(v: Vuln):
    from jaltaap.vulnerability import assess
    load = heat_load(v.lat, v.lon)
    return assess(load["days"], v.profile)


@app.get("/api/heat/drill")
def heat_drill(key: str = "severe", lat: float = 23.0, lon: float = 77.0):
    from jaltaap.vulnerability import scenario
    return scenario(key, lat, lon)


# ---------- flood desk ----------
@app.get("/api/flood/waterlog")
def flood_waterlog(lat: float, lon: float):
    from jaltaap.waterlog import waterlogging
    return cached(f"wl:{_k(lat, lon)}", 3 * 3600, lambda: _run(waterlogging, lat, lon))


def _drain_readings():
    out = {}
    for r in store.items("drain_reading", since_hours=14 * 24, limit=5000):
        out.setdefault(r["node"], []).append(float(r["head_m"]))
    return out


@app.get("/api/flood/drains")
def flood_drains(lat: float, lon: float, blocked: str = "D3"):
    from jaltaap.drains import network
    r = _run(network, lat, lon, blocked, _drain_readings() or None)
    return r


@app.get("/api/flood/cascade")
def flood_cascade(storm_mm: float = 60, hours: float = 3, blocks: str = "{}"):
    from jaltaap.drains import cascade
    try:
        b = {k: float(v) / (100 if float(v) > 1 else 1) for k, v in json.loads(blocks).items()}
    except (ValueError, AttributeError):
        raise HTTPException(400, "blocks must be JSON like {\"D3\": 60}")
    return cascade(storm_mm, hours, b)


class DrainReading(BaseModel):
    node: str
    head_m: float
    device: str = ""


@app.post("/api/drains/reading")
def drain_reading(r: DrainReading):
    return store.put("drain_reading", r.model_dump())


# ---------- drought desk ----------
def _drought(lat, lon):
    from jaltaap.drought import drought_status
    return cached(f"drought:{_k(lat, lon, 2)}", 12 * 3600, lambda: _run(drought_status, lat, lon))


@app.get("/api/drought")
def drought(lat: float, lon: float):
    from jaltaap.drought import water_health
    d = _drought(lat, lon)
    gw = peek(f"gw:{_k(lat, lon, 2)}")
    return {**d, "health": water_health(d, gw["stress"] if gw else None)}


@app.get("/api/drought/crops")
def drought_crops(lat: float, lon: float, irrigation_mm: float = 0, season: str = ""):
    from jaltaap.drought import crop_advice
    d = _drought(lat, lon)
    season = season or ("kharif" if d["season"].startswith("Kharif") else "rabi")
    mn = d["month_normals"]  # Jan..Dec
    normal = sum(mn[5:9]) if season == "kharif" else sum(mn[9:12]) + sum(mn[0:3])
    expected = normal * (1 + (d["season_deficit_pct"] or 0) / 200)  # lean towards how the season is going
    return {"season": season, "expected_rain_mm": round(expected), "crops": crop_advice(expected, irrigation_mm, season)}


@app.get("/api/drought/recharge")
def drought_recharge(lat: float, lon: float, soil: str = "loam", area_ha: float = 10):
    from jaltaap.drought import recharge_advice
    from jaltaap.waterlog import terrain
    d = _drought(lat, lon)
    t = _safe(terrain, lat, lon, 5, 0.004)
    slope = t.get("slope_pct", 1.0) if "error" not in t else 1.0
    return {"slope_pct": slope, "annual_rain": d["annual_normal_mm"],
            "options": recharge_advice(d["annual_normal_mm"], slope, soil, area_ha)}


@app.get("/api/drought/budget")
def drought_budget(lat: float, lon: float, population: int = 2500, livestock: int = 800, irrigated_ha: float = 120,
                   crop_need_mm: float = 500, catchment_ha: float = 600, gw_safe_m3: float = 250000,
                   rain_change_pct: float = 0, demand_change_pct: float = 0):
    from jaltaap.drought import water_budget
    d = _drought(lat, lon)
    return water_budget(population, livestock, irrigated_ha, crop_need_mm, d["annual_normal_mm"], catchment_ha,
                        gw_safe_m3, rain_change_pct, demand_change_pct)


@app.get("/api/drought/satellite")
def drought_satellite(lat: float, lon: float):
    from jaltaap.satellite import greenness
    return cached(f"sat:{_k(lat, lon, 2)}", 24 * 3600, lambda: _run(greenness, lat, lon))


# ---------- groundwater desk ----------
@app.get("/api/groundwater")
def groundwater(lat: float, lon: float, depth: float = 12, well: float = 60, aquifer: str = "alluvium",
                usage: str = "moderate", draft_mm: float | None = None):
    from jaltaap.groundwater import outlook
    r = _run(outlook, lat, lon, depth, well, aquifer, usage, draft_mm)
    with _lock:
        _cache[f"gw:{_k(lat, lon, 2)}"] = (time.time(), r)
    return r


class GwReading(BaseModel):
    lat: float
    lon: float
    depth_m: float
    well: str = ""
    source: str = "field"


@app.post("/api/groundwater/reading")
def groundwater_reading(r: GwReading):
    from jaltaap.groundwater import add_reading
    return add_reading(r.lat, r.lon, r.depth_m, r.source, r.well)


# ---------- tankers desk ----------
def _wsi_for(lat, lon):
    from jaltaap.tankers import water_stress_index
    d = _safe(_drought, lat, lon)
    gw = peek(f"gw:{_k(lat, lon, 2)}")
    deficit = d.get("season_deficit_pct") if isinstance(d, dict) and "error" not in d else None
    return lambda r: water_stress_index(deficit, gw["stress"] if gw else None, float(r.get("days_without", 0)))


@app.get("/api/tankers")
def tankers_plan(lat: float, lon: float):
    from jaltaap import tankers
    tankers.seed_demo(lat, lon)
    hl = peek(f"load:{_k(lat, lon)}")
    feels = None
    p = peek(f"place:{_k(lat, lon)}")
    if p and "error" not in p.get("heat", {"error": 1}):
        feels = p["heat"]["worst_day"]["feels_like_max"]
    plan = tankers.allocate(lat, lon, _wsi_for(lat, lon), feels)
    plan["equity"] = tankers.equity()
    plan["recent"] = [r for r in store.items("tanker_request") if r.get("status") in ("dispatched", "delivered")][-12:]
    for r in plan["recent"]:
        r.pop("otp", None)
    plan["heat_feels"] = feels
    plan["heat_load"] = hl["days"][0]["score"] if hl else None
    return plan


class TankerRequest(BaseModel):
    area: str
    role: str = "Ward officer"
    institution: str = "other"
    population: int = 500
    days_without: float = 1
    lat: float
    lon: float
    phone: str | None = None
    beds: int | None = None
    note: str = ""


@app.post("/api/tankers/request")
def tankers_request(r: TankerRequest):
    from jaltaap import tankers
    return tankers.add_request(r.model_dump())


@app.post("/api/tankers/dispatch")
def tankers_dispatch(trip: dict = Body(...)):
    from jaltaap import tankers
    return tankers.dispatch(trip)


class Confirm(BaseModel):
    id: str
    otp: str
    litres: int | None = None


@app.post("/api/tankers/confirm")
def tankers_confirm(c: Confirm):
    from jaltaap import tankers
    return tankers.confirm(c.id, c.otp, c.litres)


@app.get("/api/tankers/otp/{rid}")
def tankers_otp(rid: str):
    """Demo helper: shows the code that SNS would have texted (only when SNS is not live)."""
    if next(x for x in aws.status()["services"] if x["key"] == "sns")["state"] == "live":
        raise HTTPException(403, "codes are sent by SMS")
    r = store.get("tanker_request", rid) or {}
    return {"otp": r.get("otp")}


@app.post("/api/tankers/diversion")
def tankers_diversion(body: dict = Body(...)):
    from jaltaap import tankers
    return tankers.diversion_check(body["planned"], body["track"])


@app.post("/api/tankers/reset")
def tankers_reset():
    for col in ("tanker_request", "tanker_fleet"):
        for r in store.items(col, limit=5000):
            store.delete(col, r["id"])
    return {"ok": True}


# ---------- leaks desk ----------
@app.get("/api/leaks")
def leaks(lat: float, lon: float):
    from jaltaap.leaks import overview
    return cached(f"leaks:{_k(lat, lon, 2)}", 6 * 3600, lambda: _run(overview, lat, lon))


@app.get("/api/leaks/whatif")
def leaks_whatif(pressure: float = 25, flow: float = 6, vibration: float = 0.9, temperature: float = 24,
                 age: int = 20, material: str = "CI"):
    from jaltaap.leaks import whatif
    return whatif(pressure, flow, vibration, temperature, age, material)


@app.post("/api/leaks/batch")
def leaks_batch(body: dict = Body(...)):
    from jaltaap.leaks import batch
    rows = list(csv.DictReader(io.StringIO(body.get("csv", ""))))[:5000]
    if not rows:
        raise HTTPException(400, "send {'csv': '<header row>\\n<rows>'} with Pressure, Flow_Rate, Vibration, Temperature, Pipe_Age, Material")
    res = batch(rows)
    return {"rows": res, "flagged": sum(1 for r in res if r["verdict"] == "likely leak")}


@app.post("/api/leaks/reading")
def leaks_reading(body: dict = Body(...)):
    return store.put("leak_reading", body)


# ---------- citizen reports + routes ----------
class Report(BaseModel):
    lat: float
    lon: float
    depth: str = "knee"
    note: str = ""


@app.get("/api/reports")
def get_reports():
    return {"incidents": reports.incidents(hours=48), "raw": reports.recent_reports(hours=48, kind="flood")}


@app.post("/api/reports")
def post_report(r: Report):
    rid = reports.add_report(r.lat, r.lon, "flood", r.depth, r.note, source="web")
    return {"id": rid, "incidents": reports.incidents(hours=48)}


class PhotoReport(Report):
    image_b64: str


@app.post("/api/reports/photo")
def post_photo_report(r: PhotoReport):
    raw = base64.b64decode(r.image_b64.split(",")[-1])
    if len(raw) > 6_000_000:
        raise HTTPException(413, "photo too big (max ~6 MB)")
    saved = aws.put_file(f"reports/{dt.date.today().isoformat()}/{int(time.time() * 1000)}.jpg", raw, "image/jpeg")
    rid = reports.add_report(r.lat, r.lon, "flood", r.depth, r.note, photo=saved["url"], source="web-photo")
    return {"id": rid, "photo": saved, "incidents": reports.incidents(hours=48)}


@app.get("/api/route")
def route(lat: float, lon: float, mode: str = Query("flood", pattern="^(flood|heat)$")):
    from jaltaap.routing import safe_route
    inc = reports.nearby_incidents(lat, lon, km=5, hours=48) if mode == "flood" else []
    try:
        return safe_route(lat, lon, mode=mode, avoid=inc)
    except Exception as e:
        return {"ok": False, "reason": f"Couldn't load roads from OpenStreetMap ({e.__class__.__name__}). Check your internet and try again."}


# ---------- copilot, bulletin, AWS ----------
def _facts(lat, lon, name=""):
    f = {"place": {"name": name, "lat": lat, "lon": lon}}
    p = peek(f"place:{_k(lat, lon)}")
    if p:
        f["place"]["level"] = p["level"]
        f["river"] = p["flood"]
        f["rain"] = p["rain"]
        f["heat"] = {k: v for k, v in p["heat"].items() if k != "days"} if "error" not in p["heat"] else p["heat"]
    for key, name_ in ((f"load:{_k(lat, lon)}", "thermal"), (f"wl:{_k(lat, lon)}", "waterlog"),
                       (f"drought:{_k(lat, lon, 2)}", "drought"), (f"gw:{_k(lat, lon, 2)}", "groundwater"),
                       (f"leaks:{_k(lat, lon, 2)}", "leaks"), (f"pockets:{_k(lat, lon, 2)}", "heat_pockets")):
        v = peek(key)
        if v:
            f[name_] = v
    q = [r for r in store.items("tanker_request") if r.get("status") == "queued"]
    if q:
        f["tankers"] = {"queue": [{"area": r.get("area"), "institution": r.get("institution"),
                                   "days_without": r.get("days_without")} for r in q]}
    return f


class Ask(BaseModel):
    q: str
    lat: float
    lon: float
    name: str = ""
    lang: str = "en"


@app.post("/api/ask")
def ask(a: Ask):
    from jaltaap.copilot import ask as copilot_ask
    if peek(f"place:{_k(a.lat, a.lon)}") is None:
        _safe(place, a.lat, a.lon)
    return copilot_ask(a.q, _facts(a.lat, a.lon, a.name), LANGS.get(a.lang, "English"))


@app.post("/api/bulletin")
def make_bulletin(a: Ask):
    from jaltaap.copilot import bulletin
    if peek(f"place:{_k(a.lat, a.lon)}") is None:
        _safe(place, a.lat, a.lon)
    return bulletin(_facts(a.lat, a.lon, a.name))


@app.get("/api/aws/status")
def aws_status():
    return {**aws.status(), "store": store.backend()}


@app.get("/api/map-config")
def map_config():
    return aws.map_config()


@app.get("/api/aws/outbox")
def aws_outbox():
    return aws.outbox()


class Dispatch(BaseModel):
    text: str
    phone: str | None = None


@app.post("/api/dispatch")
def dispatch_alert(d: Dispatch):
    r = aws.send_sms(d.text, d.phone, subject="JalTaap alert")
    store.audit("alert_dispatch", to=d.phone or "topic", via=r.get("via"))
    return r


# ---------- web app ----------
os.makedirs(aws.LOCAL_FILES, exist_ok=True)
if ON_VERCEL:
    # uploads land in /tmp at runtime, so serve them from the function instead of a static mount
    @app.get("/files/{key:path}")
    def local_file(key: str):
        base = os.path.realpath(aws.LOCAL_FILES)
        path = os.path.realpath(os.path.join(base, key))
        if not path.startswith(base + os.sep) or not os.path.isfile(path):
            raise HTTPException(404, "not found")
        return FileResponse(path)
else:
    app.mount("/files", StaticFiles(directory=aws.LOCAL_FILES), name="files")
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    s = aws.status()
    print(f"\n  JalTaap is running -> open http://localhost:{port}")
    print(f"  AWS: {s['live']}/{s['total']} services live"
          + ("" if s["credentials"] else " (no AWS credentials found: everything runs on free local fallbacks)") + "\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
