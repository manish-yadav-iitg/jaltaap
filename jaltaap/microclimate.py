"""Heat pockets: which parts of a town run hotter than the rest.

We lay a 7 x 7 grid (about 2.2 km apart, so ~13 km across) over the place
and pull the hourly forecast for all 49 cells in one Open-Meteo call. For
each cell we look at two things:

  daytime peak feels-like  -> where people feel the worst heat
  night-time minimum       -> where the ground and buildings don't cool
                              down (the urban heat island signature)

A cell is "hot" when it runs >= 0.7 °C above the grid average. Touching hot
cells are merged into pockets (flood-fill), ranked by how hot and how big.
The weather model's own grid is a few km, so this shows town-scale pockets,
not single streets.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .http import get_json

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
N = 7
STEP = 0.02  # degrees, ~2.2 km


def grid(lat: float, lon: float, n: int = N, step: float = STEP):
    offs = (np.arange(n) - n // 2) * step
    return [(round(lat + a, 4), round(lon + b, 4)) for a in offs[::-1] for b in offs]  # row 0 = north


def _pockets(hot: np.ndarray) -> list[list[tuple[int, int]]]:
    seen = np.zeros_like(hot, dtype=bool)
    out = []
    for r in range(hot.shape[0]):
        for c in range(hot.shape[1]):
            if hot[r, c] and not seen[r, c]:
                stack, cells = [(r, c)], []
                seen[r, c] = True
                while stack:
                    y, x = stack.pop()
                    cells.append((y, x))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < hot.shape[0] and 0 <= xx < hot.shape[1] and hot[yy, xx] and not seen[yy, xx]:
                            seen[yy, xx] = True
                            stack.append((yy, xx))
                out.append(cells)
    return out


def heat_pockets(lat: float, lon: float, threshold: float = 0.7) -> dict:
    pts = grid(lat, lon)
    data = get_json(FORECAST_API, {
        "latitude": ",".join(str(p[0]) for p in pts), "longitude": ",".join(str(p[1]) for p in pts),
        "hourly": "temperature_2m,apparent_temperature", "forecast_days": 2, "timezone": "Asia/Kolkata",
    })
    if isinstance(data, dict):
        data = [data]
    day_peak, night_min, elev = [], [], []
    for d in data:
        h = pd.DataFrame(d["hourly"])
        h["time"] = pd.to_datetime(h["time"])
        h = h.set_index("time")
        first = h.index[0].normalize()
        today = h[(h.index >= first) & (h.index < first + pd.Timedelta(days=1))]
        night = h[(h.index >= first + pd.Timedelta(hours=20)) & (h.index < first + pd.Timedelta(hours=30))]
        day_peak.append(float(today["apparent_temperature"].max()))
        night_min.append(float(night["temperature_2m"].min()))
        elev.append(d.get("elevation"))
    dp = np.array(day_peak).reshape(N, N)
    nm = np.array(night_min).reshape(N, N)
    d_anom = dp - np.nanmean(dp)
    n_anom = nm - np.nanmean(nm)
    # heat pocket = hot by day OR fails to cool at night (each counts)
    load = np.maximum(d_anom, n_anom)
    pockets = []
    for cells in _pockets(load >= threshold):
        ys, xs = zip(*cells)
        idx = [y * N + x for y, x in cells]
        pockets.append({
            "cells": len(cells), "area_km2": round(len(cells) * (STEP * 111) ** 2, 1),
            "lat": round(float(np.mean([pts[i][0] for i in idx])), 4),
            "lon": round(float(np.mean([pts[i][1] for i in idx])), 4),
            "day_excess": round(float(max(d_anom[y, x] for y, x in cells)), 1),
            "night_excess": round(float(max(n_anom[y, x] for y, x in cells)), 1),
            "peak_feels": round(float(max(dp[y, x] for y, x in cells)), 1),
            "kind": "doesn't cool at night" if max(n_anom[y, x] for y, x in cells) >= max(d_anom[y, x] for y, x in cells)
                    else "hottest by day",
        })
    pockets.sort(key=lambda p: -(max(p["day_excess"], p["night_excess"]) * (1 + 0.15 * p["cells"])))
    cells = [{"lat": p[0], "lon": p[1], "day_peak": round(float(dp.flat[i]), 1),
              "night_min": round(float(nm.flat[i]), 1), "day_anom": round(float(d_anom.flat[i]), 2),
              "night_anom": round(float(n_anom.flat[i]), 2), "elev": elev[i]} for i, p in enumerate(pts)]
    spread = float(np.nanmax(dp) - np.nanmin(dp))
    uhi = float(np.nanmax(nm) - np.nanmedian(nm))
    return {"cells": cells, "pockets": pockets[:6], "step_deg": STEP, "n": N,
            "spread_day": round(spread, 1), "uhi_night": round(uhi, 1),
            "mean_day_peak": round(float(np.nanmean(dp)), 1), "mean_night_min": round(float(np.nanmean(nm)), 1),
            "source": "Open-Meteo hourly forecast, 49-point grid (~2 km spacing)"}
