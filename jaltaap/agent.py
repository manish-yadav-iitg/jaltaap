"""The JalTaap agent, built on the AWS Strands Agents SDK (open source).

The agent gets plain-Python tools (river, heat, rain, citizen reports,
safe route), decides which ones it needs, and writes a short alert in the
person's own language.

Model choice (env JALTAAP_MODEL):
  auto    -> Amazon Bedrock when AWS credentials are present, else Ollama (default)
  bedrock -> Amazon Bedrock (best for Indian languages; needs AWS creds)
  ollama  -> local model via Ollama, free, offline
"""
from __future__ import annotations

import json
import os

from strands import Agent, tool

from . import flood as _flood
from . import heat as _heat
from . import rain as _rain
from . import reports as _reports
from .alerts import LANGS, PERSONAS, template_alert

SYSTEM_PROMPT = """You are JalTaap, a flood and heat early-warning helper for people in India.
You get a location and a language. Use your tools to check river flood risk,
heavy rain, heat, and citizen reports near that place. Then write ONE short
alert (max ~90 words) in the requested language, in simple words a
non-technical person understands.

Rules:
- Start with the risk level and the date. Use 🔴 / 🟠 / 🟢.
- Give 2-4 concrete things to do NOW (move documents/medicine up, avoid
  underpasses, drink water, check on elderly, schools move assembly indoors...).
- For outdoor workers, farmers and schools, call check_outdoor_work_safety and give the safe hours.
- Mention warm nights if check_heat reports them (homes don't cool down, elderly most at risk).
- If citizens reported flooding nearby, say so and call get_safe_route.
- For street flooding in towns, call check_street_waterlogging and name the top reason.
- In dry months, call check_drought and check_groundwater for farmers and planners.
- Only if you called check_google_flood_hub and it returned a gauge, say whether it agrees. Otherwise don't mention Google Flood Hub.
- If a tool returns an error or nothing, leave that topic out. Don't tell the reader about tool errors.
- Be honest about uncertainty ("most forecasts agree", "possible").
- Never invent numbers. Only use numbers from tools.
- If everything is green, say so in one or two lines.
- Output only the alert text, nothing else."""


@tool
def check_river_flood(lat: float, lon: float) -> str:
    """River flood risk for the next ~15 days at a place in India (GloFAS forecast,
    ~50 ensemble runs, compared with that river's own 40-year history)."""
    return json.dumps(_flood.flood_risk(lat, lon))


@tool
def check_heat(lat: float, lon: float) -> str:
    """Heat risk for the next 7 days: max temperature, feels-like, wet-bulb, and
    whether the official IMD heatwave rule would even catch it."""
    r = _heat.heat_risk(lat, lon)
    r.pop("days", None)
    return json.dumps(r)


@tool
def check_outdoor_work_safety(lat: float, lon: float) -> str:
    """Hour-by-hour WBGT (the heat-safety number used for workers, soldiers and sport)
    for today and tomorrow: safe hours, risky hours, worst hour, and a work/rest plan
    for light, moderate and heavy work."""
    from .heatstress import hourly_heat_stress
    r = hourly_heat_stress(lat, lon)
    return json.dumps(r["days"])


@tool
def check_heavy_rain(lat: float, lon: float) -> str:
    """Heavy rain / waterlogging risk for the next 5 days using IMD rainfall categories."""
    r = _rain.rain_risk(lat, lon)
    r.pop("days", None)
    return json.dumps(r)


@tool
def citizen_reports_nearby(lat: float, lon: float, km: float = 5.0) -> str:
    """Flooding reported by people within `km` in the last 24 hours (merged into incidents)."""
    inc = _reports.nearby_incidents(lat, lon, km=km)
    return json.dumps(inc) if inc else "no citizen flood reports within %g km in the last 24 hours" % km


@tool
def get_safe_route(lat: float, lon: float, mode: str = "flood") -> str:
    """Walking route to the nearest relief spot (mode='flood') or cooling spot
    (mode='heat'), avoiding streets citizens reported as flooded."""
    try:
        from .routing import safe_route
    except ImportError:  # osmnx isn't in the slim Lambda package
        return "route not available here; tell people to move to the nearest school, hospital or high ground"
    inc = _reports.nearby_incidents(lat, lon, km=5) if mode == "flood" else []
    r = safe_route(lat, lon, mode=mode, avoid=inc)
    r.pop("path", None)
    return json.dumps(r)


@tool
def check_google_flood_hub(lat: float, lon: float) -> str:
    """Second opinion from Google Flood Hub: severity and trend at the nearest Google
    flood gauge (within 30 km). Returns 'not available' if no key or no gauge nearby."""
    from . import floodhub
    if not floodhub.enabled():
        return "not available (no Google Flood Hub key configured)"
    try:
        g = floodhub.nearest(lat, lon)
    except Exception as e:  # key not approved yet, quota, network
        return f"not available ({type(e).__name__})"
    return json.dumps(g) if g else "no Google Flood Hub gauge within 30 km"


@tool
def check_street_waterlogging(lat: float, lon: float) -> str:
    """Chance that streets at this spot waterlog in the next 24 h, with the reasons (low point,
    distance to drains, underpasses, rain) and the hospitals/schools most at risk nearby."""
    from .waterlog import waterlogging
    r = waterlogging(lat, lon)
    return json.dumps({k: r[k] for k in ("probability", "level", "why", "rain", "flash", "impact")}, default=str)


@tool
def check_body_heat_load(lat: float, lon: float) -> str:
    """Body Heat Load 0-100 for the next 3 days (Heat Index + WBGT + UTCI blend), the peak hour,
    what drives it (humidity, sun or still air) and matching advice."""
    from .thermal import body_heat_load
    return json.dumps(body_heat_load(lat, lon)["days"])


@tool
def check_drought(lat: float, lon: float) -> str:
    """Drought status: SPI-1/3/6, SPEI-3, season rain vs normal, drought class D0-D4, soil moisture."""
    from .drought import drought_status
    r = drought_status(lat, lon)
    for k in ("yearly", "monthly"):
        r.pop(k, None)
    return json.dumps(r)


@tool
def check_groundwater(lat: float, lon: float) -> str:
    """Groundwater outlook: CGWB category (stage of development), water-table trend, days until the
    well reaches warning/critical depth, and the safe pumping limit. Assumes a 12 m water table if no readings."""
    from .groundwater import outlook
    r = outlook(lat, lon)
    return json.dumps({k: r[k] for k in ("category", "stage_pct", "depth_now", "trend_m_per_year", "breach_days",
                                         "safe_draft_mm_year", "cut_needed_pct", "confidence")})


TOOLS = [check_river_flood, check_heat, check_outdoor_work_safety, check_heavy_rain,
         citizen_reports_nearby, get_safe_route, check_street_waterlogging, check_body_heat_load, check_drought,
         check_groundwater]


def make_model():
    kind = os.environ.get("JALTAAP_MODEL", "auto").lower()
    if kind == "auto":
        from .aws import has_credentials
        kind = "bedrock" if has_credentials() else "ollama"
    if kind == "bedrock":
        from strands.models import BedrockModel
        return BedrockModel(
            model_id=os.environ.get("BEDROCK_MODEL_ID", "apac.amazon.nova-lite-v1:0"),
            region_name=os.environ.get("AWS_REGION", "ap-south-1"),
            temperature=0.2,
        )
    from strands.models.ollama import OllamaModel
    return OllamaModel(host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                       model_id=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"), temperature=0.2)


def build_agent() -> Agent:
    from . import floodhub
    # without a Flood Hub key the tool can only say "not available", and the model then tends to invent an answer
    tools = [check_google_flood_hub, *TOOLS] if floodhub.enabled() else TOOLS
    return Agent(model=make_model(), tools=tools, system_prompt=SYSTEM_PROMPT, callback_handler=None)


def _clean_text(text: str) -> str:
    """Nova models sometimes wrap their reasoning in <thinking> tags; readers only get the alert."""
    import re
    text = re.sub(r"<thinking>.*?(</thinking>|$)", "", text, flags=re.S | re.I)
    return re.sub(r"^\s*Alert:\s*", "", text.strip(), flags=re.I).strip()


def make_alert(lat: float, lon: float, lang: str = "en", use_llm: bool = True, persona: str = "family") -> dict:
    """Returns {'text', 'by'}. Falls back to templates if the LLM isn't reachable."""
    if use_llm:
        try:
            agent = build_agent()
            res = agent(f"Location: lat {lat:.4f}, lon {lon:.4f}. "
                        f"The reader is: {PERSONAS.get(persona, PERSONAS['family'])['label']}. "
                        f"Write the alert in {LANGS.get(lang, 'English')}, with advice for that reader.")
            text = _clean_text(str(res))
            if text:
                used = []
                try:
                    used = list(res.metrics.tool_metrics.keys())
                except Exception:
                    pass
                return {"text": text, "by": "strands-agent", "tools": used, "model": type(agent.model).__name__}
        except Exception as e:  # LLM down, no creds, etc.
            print("agent failed, using template:", e)
    fl = _safe(_flood.flood_risk, lat, lon)
    he = _safe(_heat.heat_risk, lat, lon)
    ra = _safe(_rain.rain_risk, lat, lon)
    from .heatstress import hourly_heat_stress
    wk = _safe(hourly_heat_stress, lat, lon)
    return {"text": template_alert(fl, he, ra, lang, persona, wk), "by": "template"}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        print(fn.__name__, "failed:", e)
        return None


if __name__ == "__main__":
    import sys
    lat, lon = float(sys.argv[1]), float(sys.argv[2])
    lang = sys.argv[3] if len(sys.argv) > 3 else "en"
    print(make_alert(lat, lon, lang)["text"])
