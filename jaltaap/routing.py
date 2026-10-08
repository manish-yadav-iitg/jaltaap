"""Safe route out: real roads from OpenStreetMap (OSMnx), with streets near
citizen-reported flooding cut out, to the nearest relief/cooling spot.

Relief spots in India are usually schools, hospitals and community halls,
so we pull those from OSM. For heat we look for hospitals, drinking water
points and parks.
"""
from __future__ import annotations

from functools import lru_cache

import networkx as nx
import numpy as np

FLOOD_DEST_TAGS = {"amenity": ["hospital", "school", "college", "shelter", "community_centre", "townhall"],
                   "emergency": ["assembly_point"]}
HEAT_DEST_TAGS = {"amenity": ["hospital", "clinic", "drinking_water"], "leisure": ["park"]}


@lru_cache(maxsize=16)
def _graph(lat: float, lon: float, dist: int):
    import osmnx as ox
    return ox.graph_from_point((lat, lon), dist=dist, network_type="walk", simplify=True)


def _places(lat: float, lon: float, dist: int, mode: str):
    import osmnx as ox
    tags = FLOOD_DEST_TAGS if mode == "flood" else HEAT_DEST_TAGS
    gdf = ox.features_from_point((lat, lon), tags=tags, dist=dist)
    out = []
    for _, r in gdf.iterrows():
        c = r.geometry.centroid
        kind = next((str(r[k]) for k in ("amenity", "emergency", "leisure") if k in r and isinstance(r[k], str)), "place")
        out.append({"name": r.get("name") if isinstance(r.get("name"), str) else kind.replace("_", " ").title(),
                    "kind": kind, "lat": c.y, "lon": c.x})
    return out


def safe_route(lat: float, lon: float, mode: str = "flood", avoid: list[dict] | None = None,
               avoid_radius_m: float = 150, dist: int = 2500) -> dict:
    """Walking route to the nearest reachable relief (flood) or cooling (heat) spot.
    `avoid` = list of {'lat','lon'} incidents; roads within avoid_radius_m are removed."""
    import osmnx as ox
    G = _graph(round(lat, 3), round(lon, 3), dist).copy()
    removed = 0
    if avoid:
        nodes = ox.graph_to_gdfs(G, edges=False)
        for a in avoid:
            d = _hav_vec(a["lat"], a["lon"], nodes["y"].values, nodes["x"].values)
            bad = nodes.index[d <= avoid_radius_m]
            removed += len(bad)
            G.remove_nodes_from(bad)
    if G.number_of_nodes() == 0:
        return {"ok": False, "reason": "no open roads left nearby"}

    start = ox.distance.nearest_nodes(G, lon, lat)
    places = _places(round(lat, 3), round(lon, 3), dist, mode)
    if not places:
        return {"ok": False, "reason": "no relief/cooling places mapped nearby on OpenStreetMap"}
    lengths = nx.single_source_dijkstra_path_length(G, start, weight="length")
    best = None
    for p in places:
        n = ox.distance.nearest_nodes(G, p["lon"], p["lat"])
        if n in lengths and (best is None or lengths[n] < best[0]):
            best = (lengths[n], n, p)
    if best is None:
        return {"ok": False, "reason": "every relief spot is cut off by reported flooding"}
    path = nx.shortest_path(G, start, best[1], weight="length")
    coords = [(G.nodes[n]["y"], G.nodes[n]["x"]) for n in path]
    return {"ok": True, "destination": best[2], "distance_m": round(best[0]),
            "walk_min": round(best[0] / 75), "path": coords, "blocked_nodes": int(removed)}


def _hav_vec(lat, lon, lats, lons):
    p1, p2 = np.radians(lat), np.radians(lats)
    dp, dl = p2 - p1, np.radians(lons - lon)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6_371_000 * np.arcsin(np.sqrt(a))
