"""Honest scorecard: run the alert rules over every monsoon day 2015-2024
(not just the famous floods) and count hits and false alarms.

    python -m jaltaap.evaluate
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .flood import history, snap_to_river, thresholds
from .forecast import _pipeline
from .replay import load_events

RULES = {
    "watch   (p90 >= orange)": ("p90", "p95"),
    "warning (p50 >= orange)": ("p50", "p95"),
    "severe  (p50 >= red)": ("p50", "p99"),
}


def evaluate(start_year: int = 2015, end: str = "2024-12-31", horizon: int = 10) -> dict:
    import torch
    pipe = _pipeline()
    report = {}
    for ev in load_events():
        c = snap_to_river(ev["lat"], ev["lon"])
        s = history(c["lat"], c["lon"], end=end)
        th = thresholds(s, before=f"{start_year}-01-01")
        days = [d for d in s.index if d.year >= start_year and 6 <= d.month <= 9]
        ctx = [torch.tensor(s[s.index <= d].values[-512:], dtype=torch.float32) for d in days]
        qs = []
        for i in range(0, len(ctx), 256):
            q, _ = pipe.predict_quantiles(ctx[i:i + 256], prediction_length=horizon, quantile_levels=[0.5, 0.9])
            qs.append(q)
        q = torch.cat(qs).numpy()
        cols = {"p50": q[:, :, 0].max(1), "p90": q[:, :, 1].max(1)}
        actual = np.array([s[(s.index > d) & (s.index <= d + pd.Timedelta(days=horizon))].max() for d in days])
        report[ev["id"]] = {}
        for name, (col, lvl) in RULES.items():
            pred = cols[col] >= th[lvl]
            truth = actual >= th[lvl]
            tp, fp, fn = int((pred & truth).sum()), int((pred & ~truth).sum()), int((~pred & truth).sum())
            report[ev["id"]][name] = {
                "alert_days_pct": round(100 * pred.mean()),
                "precision_pct": round(100 * tp / max(tp + fp, 1)),
                "recall_pct": round(100 * tp / max(tp + fn, 1)),
            }
            print(f"{ev['id']:13s} {name}: alert {report[ev['id']][name]['alert_days_pct']:3d}% of days | "
                  f"right {report[ev['id']][name]['precision_pct']}% | caught {report[ev['id']][name]['recall_pct']}%")
    with open("outputs/scorecard.json", "w") as f:
        json.dump(report, f, indent=1)
    return report


if __name__ == "__main__":
    import os
    os.makedirs("outputs", exist_ok=True)
    evaluate()
