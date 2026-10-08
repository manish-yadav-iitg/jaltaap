"""Crop greenness from space, straight from the AWS Open Data registry.

Sentinel-2 Level-2A scenes sit in a public S3 bucket (registry.opendata.aws/
sentinel-2-l2a-cogs) and are searchable through the Earth Search STAC API.
No account or key is needed.

For a point we find the least cloudy scene of the last 30 days and the same
window a year ago, read the red (B04) and near-infrared (B08) bands around
the point, and compute NDVI. Comparing the two gives a quick "fields are
greener / browner than last year" signal for drought. Reading the pixels
needs `rasterio`; without it we still return the scenes and their preview
images.
"""
from __future__ import annotations

import datetime as dt

import requests

STAC = "https://earth-search.aws.element84.com/v1/search"


def _search(lat: float, lon: float, start: dt.date, end: dt.date) -> dict | None:
    body = {"collections": ["sentinel-2-l2a"], "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": 40}}, "limit": 30,
            "fields": {"include": ["id", "properties.datetime", "properties.eo:cloud_cover", "assets.red.href",
                                   "assets.nir.href", "assets.thumbnail.href"]}}
    r = requests.post(STAC, json=body, timeout=30)
    r.raise_for_status()
    feats = r.json().get("features", [])
    if not feats:
        return None
    best = min(feats, key=lambda f: f["properties"].get("eo:cloud_cover", 100))
    a = best.get("assets", {})
    return {"id": best["id"], "date": best["properties"]["datetime"][:10],
            "cloud_pct": round(best["properties"].get("eo:cloud_cover", 0)),
            "red": a.get("red", {}).get("href"), "nir": a.get("nir", {}).get("href"),
            "thumbnail": a.get("thumbnail", {}).get("href")}


def _ndvi(scene: dict, lat: float, lon: float, half: int = 15) -> float | None:
    """Mean NDVI in a ~300 m box around the point (10 m pixels)."""
    try:
        import numpy as np
        import rasterio
        from rasterio.warp import transform
        from rasterio.windows import Window
    except ImportError:
        return None
    vals = []
    for band in ("red", "nir"):
        with rasterio.Env(AWS_NO_SIGN_REQUEST="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with rasterio.open(scene[band]) as src:
                xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
                row, col = src.index(xs[0], ys[0])
                win = Window(col - half, row - half, 2 * half, 2 * half)
                vals.append(src.read(1, window=win).astype("float32"))
    red, nir = vals
    ok = (red > 0) & (nir > 0)
    if not ok.any():
        return None
    nd = (nir[ok] - red[ok]) / (nir[ok] + red[ok])
    return round(float(np.nanmean(nd)), 3)


def greenness(lat: float, lon: float) -> dict:
    today = dt.date.today()
    now = _search(lat, lon, today - dt.timedelta(days=30), today)
    then = _search(lat, lon, today - dt.timedelta(days=395), today - dt.timedelta(days=335))
    out = {"now": now, "last_year": then, "source": "Sentinel-2 L2A, AWS Open Data (Earth Search STAC)"}
    if now:
        now["ndvi"] = _ndvi(now, lat, lon)
    if then:
        then["ndvi"] = _ndvi(then, lat, lon)
    if now and then and now.get("ndvi") is not None and then.get("ndvi") is not None:
        ch = now["ndvi"] - then["ndvi"]
        out["change"] = round(ch, 3)
        out["verdict"] = "greener than last year" if ch > 0.05 else "browner than last year" if ch < -0.05 \
            else "about the same as last year"
    elif now and now.get("ndvi") is None:
        out["note"] = "Install rasterio to read NDVI values; the scene previews still work."
    for s in (now, then):  # band links are long and only needed server-side
        if s:
            s.pop("red", None)
            s.pop("nir", None)
    return out
