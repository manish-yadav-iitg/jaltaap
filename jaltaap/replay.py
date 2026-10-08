"""Replay real past floods and measure how much warning JalTaap would have given.

Fair test: thresholds use only data from BEFORE the flood year, and Chronos
only sees river data up to each day it is asked.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from .flood import history, snap_to_river, thresholds
from .forecast import chronos_forecast

EVENTS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "events.json")


def load_events() -> list[dict]:
    with open(EVENTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def replay_event(ev: dict, lookback: int = 15, horizon: int = 10) -> dict:
    cell = snap_to_river(ev["lat"], ev["lon"])
    year_start = ev["peak_date"][:4] + "-01-01"
    end = (pd.Timestamp(ev["peak_date"]) + pd.Timedelta(days=20)).date().isoformat()
    s = history(cell["lat"], cell["lon"], end=end)
    th = thresholds(s, before=year_start)

    window = s[pd.Timestamp(ev["peak_date"]) - pd.Timedelta(days=lookback + 5):
               pd.Timestamp(ev["peak_date"]) + pd.Timedelta(days=10)]
    peak_day = window.idxmax()
    observed_orange = next((t for t, v in window.items() if v >= th["p95"]), None)

    # walk forward day by day; first day the MEDIAN forecast crosses orange.
    # (p90 rule fired on ~45% of monsoon days in our 10-year test = too noisy;
    #  p50 rule was right ~9/10 times. See jaltaap/evaluate.py)
    first_alert = None
    alerts = []
    for k in range(lookback, 0, -1):
        cutoff = peak_day - pd.Timedelta(days=k)
        fc = chronos_forecast(s[s.index <= cutoff], horizon=horizon)
        hit = bool((fc["p50"] >= th["p95"]).any())
        alerts.append({"date": cutoff.date().isoformat(), "alert": hit,
                       "p50_peak": round(float(fc["p50"].max())), "p90_peak": round(float(fc["p90"].max()))})
        if hit and first_alert is None:
            first_alert = cutoff

    return {
        "name": ev["name"],
        "river_cell": cell,
        "thresholds": {k: round(v) if k != "years" else v for k, v in th.items()},
        "peak_date": peak_day.date().isoformat(),
        "peak_m3s": round(float(window.max())),
        "peak_vs_p99": round(float(window.max()) / th["p99"], 2),
        "observed_orange_date": observed_orange.date().isoformat() if observed_orange is not None else None,
        "first_alert_date": first_alert.date().isoformat() if first_alert is not None else None,
        "days_warning_before_peak": (peak_day - first_alert).days if first_alert is not None else 0,
        "days_warning_before_orange": (observed_orange - first_alert).days
        if first_alert is not None and observed_orange is not None else None,
        "series": {d.date().isoformat(): round(float(v)) for d, v in window.items()},
        "daily_alerts": alerts,
        "note": ev.get("note", ""),
    }


def plot_event(res: dict, path: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = pd.Series(res["series"])
    s.index = pd.to_datetime(s.index)
    th = res["thresholds"]
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    ax.plot(s.index, s.values, color="#1f4e79", lw=2.2, label="River flow (GloFAS)")
    ax.axhline(th["p95"], color="#e08a00", ls="--", lw=1.4, label="Orange (local 95th pct)")
    ax.axhline(th["p99"], color="#c62828", ls="--", lw=1.4, label="Red (local 99th pct)")
    ax.axvline(pd.Timestamp(res["peak_date"]), color="#555", lw=1, alpha=.6)
    ax.annotate("peak", (pd.Timestamp(res["peak_date"]), res["peak_m3s"]), xytext=(6, -12),
                textcoords="offset points", fontsize=9, color="#555")
    if res["first_alert_date"]:
        fa = pd.Timestamp(res["first_alert_date"])
        ax.axvspan(fa, pd.Timestamp(res["peak_date"]), color="#ffcc80", alpha=.25,
                   label=f"JalTaap warning: {res['days_warning_before_peak']} days before peak")
        ax.axvline(fa, color="#e08a00", lw=2)
    ax.set_title(res["name"], fontsize=12, loc="left")
    ax.set_ylabel("m³/s")
    ax.grid(alpha=.25)
    ax.legend(fontsize=8, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def run_all(out_dir: str = "outputs") -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for ev in load_events():
        r = replay_event(ev)
        plot_event(r, os.path.join(out_dir, f"replay_{ev['id']}.png"))
        results.append(r)
        print(f"{r['name']}: peak {r['peak_m3s']} m3/s ({r['peak_vs_p99']}x red line), "
              f"first alert {r['first_alert_date']} -> {r['days_warning_before_peak']} days before peak")
    with open(os.path.join(out_dir, "replay_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    return results


if __name__ == "__main__":
    run_all()
