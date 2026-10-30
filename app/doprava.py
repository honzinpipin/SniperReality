"""Dojíždění: nejbližší zastávka veřejné dopravy k bytu/domu (z OSM datasetu).

Dataset: data/zastavky.json (jednorázově stažen z OpenStreetMap). Nejbližší zastávka
se počítá geometricky (haversine) nad malým seznamem – rychlé, žádné externí volání.
Samotný odkaz do IDOSu (zastávka bytu -> cílová zastávka uživatele) se skládá až na
klientovi z uložené cílové zastávky, takže stránka nic nedopočítává na pozadí.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Optional

from .config import ROOT

STOPS_PATH = ROOT / "data" / "zastavky.json"


@lru_cache(maxsize=1)
def _stops() -> list:
    try:
        with open(STOPS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def nearest_stop(lat: Optional[float], lon: Optional[float]) -> Optional[dict]:
    """Nejbližší zastávka k daným souřadnicím. Vrací {name, kind, dist_m, walk_min}
    nebo None (bez souřadnic / prázdný dataset). Chůze počítána ~80 m/min."""
    if lat is None or lon is None:
        return None
    stops = _stops()
    if not stops:
        return None
    best = None
    for s in stops:
        d = _haversine_m(lat, lon, s["lat"], s["lon"])
        if best is None or d < best[0]:
            best = (d, s)
    d, s = best
    return {"name": s["name"], "kind": s["kind"], "dist_m": round(d),
            "walk_min": max(1, round(d / 80))}
