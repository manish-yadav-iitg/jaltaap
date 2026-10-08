"""Citizen flood/heat reports.

Models miss things (dam releases, a blocked drain, a broken embankment).
People on the ground don't. Many people reporting the same spot get merged
into one incident (DBSCAN-style clustering), so 30 reports != 30 problems.
"""
from __future__ import annotations

import os
import sqlite3
import time

import numpy as np

DB = os.environ.get("JALTAAP_DB", os.path.join(os.path.dirname(__file__), "..", "data", "jaltaap.db"))
DEPTHS = {"ankle": 1, "knee": 2, "waist": 3, "above waist": 4}


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS reports(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, lat REAL, lon REAL,
        kind TEXT, depth TEXT, note TEXT, photo TEXT, photo_score REAL, source TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS subscribers(
        chat_id INTEGER PRIMARY KEY, lat REAL, lon REAL, lang TEXT, last_level TEXT, ts REAL)""")
    return con


def add_report(lat: float, lon: float, kind: str = "flood", depth: str = "knee", note: str = "",
               photo: str | None = None, source: str = "app", photo_score: float | None = None) -> int:
    """photo_score: pass one in or we try the local CLIP check."""
    score = photo_score if photo_score is not None else (verify_photo(photo) if photo and os.path.exists(photo) else None)
    with _db() as con:
        cur = con.execute("INSERT INTO reports(ts,lat,lon,kind,depth,note,photo,photo_score,source)"
                          " VALUES(?,?,?,?,?,?,?,?,?)",
                          (time.time(), lat, lon, kind, depth, note, photo, score, source))
        return cur.lastrowid


def recent_reports(hours: float = 24, kind: str | None = None) -> list[dict]:
    since = time.time() - hours * 3600
    with _db() as con:
        con.row_factory = sqlite3.Row
        q = "SELECT * FROM reports WHERE ts >= ?" + (" AND kind = ?" if kind else "")
        rows = con.execute(q, (since, kind) if kind else (since,)).fetchall()
    # drop photos our checker is fairly sure are NOT floods
    return [dict(r) for r in rows if r["photo_score"] is None or r["photo_score"] >= 0.35]


def incidents(hours: float = 24, radius_m: float = 300, kind: str = "flood") -> list[dict]:
    """Merge nearby reports into incidents."""
    rows = recent_reports(hours, kind)
    if not rows:
        return []
    labels = _cluster(np.radians([[r["lat"], r["lon"]] for r in rows]), radius_m / 6_371_000)
    out = []
    for lab in sorted(set(labels)):
        grp = [r for r, l in zip(rows, labels) if l == lab]
        worst = max(grp, key=lambda r: DEPTHS.get(r["depth"], 0))["depth"]
        n = len(grp)
        out.append({
            "lat": float(np.mean([r["lat"] for r in grp])),
            "lon": float(np.mean([r["lon"] for r in grp])),
            "reports": n,
            "worst_depth": worst,
            "confidence": "high" if n >= 3 else "medium" if n == 2 else "low",
            "last_report_min_ago": round((time.time() - max(r["ts"] for r in grp)) / 60),
        })
    return sorted(out, key=lambda x: (-x["reports"], -DEPTHS.get(x["worst_depth"], 0)))


def _cluster(X: np.ndarray, eps: float) -> list[int]:
    """Same groups as DBSCAN(min_samples=1, metric='haversine'): reports chained within eps are one
    incident. Plain numpy, so it runs on Lambda without scikit-learn."""
    la, lo = X[:, 0], X[:, 1]
    a = (np.sin((la[:, None] - la[None, :]) / 2) ** 2
         + np.cos(la[:, None]) * np.cos(la[None, :]) * np.sin((lo[:, None] - lo[None, :]) / 2) ** 2)
    near = 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1))) <= eps
    labels = [-1] * len(X)
    for i in range(len(X)):
        if labels[i] >= 0:
            continue
        labels[i], stack = i, [i]
        while stack:
            for j in np.flatnonzero(near[stack.pop()]):
                if labels[j] < 0:
                    labels[j] = i
                    stack.append(int(j))
    return labels


def nearby_incidents(lat: float, lon: float, km: float = 5, **kw) -> list[dict]:
    from .flood import _haversine
    res = []
    for inc in incidents(**kw):
        d = _haversine(lat, lon, inc["lat"], inc["lon"])
        if d <= km:
            res.append({**inc, "distance_km": round(d, 2)})
    return sorted(res, key=lambda x: x["distance_km"])


# ---------- quick photo sanity check (open-source CLIP, zero-shot) ----------
_clf = None


def verify_photo(path: str) -> float | None:
    """Probability-like score that the photo shows a flooded street.
    Uses openai/clip-vit-base-patch32 from Hugging Face, no training needed.
    Returns None if transformers/the model can't load (feature is optional)."""
    global _clf
    try:
        if _clf is None:
            from transformers import pipeline
            _clf = pipeline("zero-shot-image-classification", model="openai/clip-vit-base-patch32")
        labels = ["a flooded street with standing water", "a dry street", "a selfie or indoor photo",
                  "a screenshot or meme"]
        res = _clf(path, candidate_labels=labels)
        return float(next(r["score"] for r in res if r["label"] == labels[0]))
    except Exception:
        return None


# ---------- telegram subscribers ----------

def save_subscriber(chat_id: int, lat: float, lon: float, lang: str = "en"):
    with _db() as con:
        con.execute("INSERT INTO subscribers(chat_id,lat,lon,lang,last_level,ts) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, ts=excluded.ts",
                    (chat_id, lat, lon, lang, "green", time.time()))


def set_lang(chat_id: int, lang: str):
    with _db() as con:
        con.execute("INSERT INTO subscribers(chat_id,lang,last_level,ts) VALUES(?,?,?,?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET lang=excluded.lang", (chat_id, lang, "green", time.time()))


def get_subscriber(chat_id: int) -> dict | None:
    with _db() as con:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone()
    return dict(r) if r else None


def all_subscribers() -> list[dict]:
    with _db() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM subscribers WHERE lat IS NOT NULL").fetchall()]


def set_last_level(chat_id: int, level: str):
    with _db() as con:
        con.execute("UPDATE subscribers SET last_level=? WHERE chat_id=?", (level, chat_id))
