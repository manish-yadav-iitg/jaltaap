"""Ask JalTaap: a water-and-heat copilot on Amazon Bedrock.

The copilot never makes numbers up. The server first gathers the facts for
the place the person is looking at (whatever desks have already been
computed), then Bedrock writes the answer from those facts only. Without
AWS it answers from the same facts with plain rules, so the button never
dead-ends.

The same facts power the daily bulletin, which is archived to Amazon S3.
"""
from __future__ import annotations

import datetime as dt
import json

from . import aws

SYSTEM = """You are JalTaap's copilot for district officials, ward engineers, farmers and residents in India.
Answer ONLY from the JSON facts you are given. If a fact is missing, say what is missing and which
desk in the app would show it. Use plain words, short sentences, Indian units (°C, mm, litres, m below ground).
Lead with the answer, then 2-4 concrete actions. Max 140 words. Reply in the requested language."""

BULLETIN = """Write a one-page daily water & heat bulletin for the place in the facts, for a district control room.
Sections: Today at a glance (one line per hazard with 🔴/🟠/🟢), What to do today (by team),
Watch next 3 days. Use only the facts. Max 220 words."""


def _shrink(facts: dict, limit: int = 9000) -> str:
    """Drop long arrays so the prompt stays small and cheap."""
    def cut(x):
        if isinstance(x, dict):
            return {k: cut(v) for k, v in x.items() if k not in ("hours", "series", "history", "cells", "line", "path",
                                                                   "pipes", "rain_mm_h", "t_h", "outfall", "rain")}
        if isinstance(x, list):
            return [cut(v) for v in x[:8]]
        return x
    s = json.dumps(cut(facts), ensure_ascii=False, default=str)
    return s[:limit]


def ask(question: str, facts: dict, lang: str = "English") -> dict:
    prompt = f"Language: {lang}\nQuestion: {question}\n\nFacts (JSON):\n{_shrink(facts)}"
    text = aws.bedrock_text(prompt, system=SYSTEM, max_tokens=500)
    if text:
        return {"text": text, "by": f"Amazon Bedrock ({aws.BEDROCK_MODEL})"}
    return {"text": rule_answer(question, facts), "by": "built-in rules (add AWS keys to use Amazon Bedrock)"}


def rule_answer(q: str, f: dict) -> str:
    q = q.lower()
    lines = []
    place = f.get("place", {}).get("name") or "this place"

    def has(*k):
        return any(x in q for x in k)
    if has("heat", "hot", "garmi", "work", "outside", "loo") and f.get("thermal"):
        d = f["thermal"]["days"][0]
        lines.append(f"Body heat load in {place} peaks at {d['score']}/100 ({d['label']}) around {d['peak_hour']}:00. {d['advice']}")
    if has("flood", "rain", "waterlog", "drain", "baarish") and f.get("waterlog"):
        w = f["waterlog"]
        lines.append(f"Street waterlogging chance here is {round(100 * w['probability'])}% ({w['level']}). "
                     f"Main reasons: {', '.join(x['label'].lower() for x in w['why'][:2])}. "
                     f"Expected rain next 24 h: {w['rain']['next24_mm']} mm.")
    if has("drought", "dry", "crop", "farm", "sukha") and f.get("drought"):
        d = f["drought"]
        lines.append(f"{d['label']} (SPI-3 {d['spi3']}). Season rain is {d['season_deficit_pct']}% vs normal.")
    if has("ground", "well", "borewell", "aquifer", "water table") and f.get("groundwater"):
        g = f["groundwater"]
        lines.append(f"Groundwater: {g['category']} ({g['stage_pct']}% of recharge pumped). Water table {g['depth_now']} m, "
                     f"moving {g['trend_m_per_year']:+} m/year. Safe pumping is {g['safe_draft_mm_year']} mm/year.")
    if has("tanker", "supply", "shortage") and f.get("tankers"):
        t = f["tankers"]
        lines.append(f"{len(t.get('queue', []))} tanker requests waiting; top priority: {t['queue'][0]['area']}." if t.get("queue") else "No tanker requests waiting.")
    if has("leak", "pipe", "pressure", "nrw") and f.get("leaks"):
        lk = f["leaks"]
        s = lk["suspects"][0]
        lines.append(f"Likely new leak near pipe {s['pipe']} (zone {s['zone']}). Night flow shows about {lk['lost_m3_day']} m³/day lost.")
    if not lines:
        lv = f.get("place", {}).get("level")
        lines.append(f"Open a desk (Heat, Flood, Drought, Groundwater, Tankers, Leaks) for {place} first, then ask again. "
                     + (f"Overall level right now: {lv}." if lv else ""))
    return " ".join(lines)


def bulletin(facts: dict) -> dict:
    name = facts.get("place", {}).get("name") or "Selected place"
    text = aws.bedrock_text(f"Facts (JSON):\n{_shrink(facts)}", system=SYSTEM + "\n" + BULLETIN, max_tokens=700)
    by = f"Amazon Bedrock ({aws.BEDROCK_MODEL})"
    if not text:
        by = "built-in rules"
        text = "\n".join(filter(None, [
            f"JalTaap bulletin — {name} — {dt.date.today():%d %b %Y}",
            rule_answer("heat flood drought groundwater tanker leak", facts)]))
    key = f"bulletins/{dt.date.today().isoformat()}/{name.replace(' ', '_')[:40]}.txt"
    saved = aws.put_file(key, text.encode("utf-8"), "text/plain; charset=utf-8")
    return {"text": text, "by": by, "saved": saved}
