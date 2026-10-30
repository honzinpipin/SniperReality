"""Statistiky lokality Chrudim (bod 7 zadani).

Pocita se nad AKTIVNIMI hlavnimi zaznamy (skupinami po deduplikaci), aby jedna
nabidka na trech portalech neskreslila prumer trojnasobne.
"""
from __future__ import annotations

import statistics
from typing import Optional

from .models import land_class


def _median(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _mean(values: list[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def compute_locality_stats(groups: list[dict]) -> dict:
    """groups = seznam hlavnich zaznamu (dict s klici price, area, price_per_m2,
    land_type, is_active). Vraci souhrnne statistiky.
    """
    active = [g for g in groups if g.get("is_active")]
    pm2 = [g["price_per_m2"] for g in active if g.get("price_per_m2")]
    prices = [g["price"] for g in active if g.get("price")]
    areas = [g["area"] for g in active if g.get("area")]

    by_type: dict[str, int] = {}
    for g in active:
        t = g.get("land_type") or "neuvedeno"
        by_type[t] = by_type.get(t, 0) + 1

    by_disp: dict[str, int] = {}
    for g in active:
        dsp = g.get("dispozice")
        if dsp:
            by_disp[dsp] = by_disp.get(dsp, 0) + 1

    # pm2 po tridach (stavebni vs zemedelska) pro srovnani "podobnych" nabidek.
    pm2_by_class: dict[str, list[float]] = {}
    for g in active:
        if g.get("price_per_m2"):
            pm2_by_class.setdefault(land_class(g.get("land_type")), []).append(g["price_per_m2"])

    # pm2 po dispozici (2+kk, 3+1...) pro srovnani bytu/domu se stejnou dispozici.
    pm2_by_disp: dict[str, list[float]] = {}
    for g in active:
        if g.get("price_per_m2") and g.get("dispozice"):
            pm2_by_disp.setdefault(g["dispozice"], []).append(g["price_per_m2"])

    return {
        "pocet_aktivnich": len(active),
        "pm2_prumer": _mean(pm2),
        "pm2_median": _median(pm2),
        "pm2_min": min(pm2) if pm2 else None,
        "pm2_max": max(pm2) if pm2 else None,
        "cena_prumer": _mean(prices),
        "vymera_prumer": _mean(areas),
        "pocet_dle_typu": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "pocet_dle_dispozice": dict(sorted(by_disp.items(), key=lambda x: x[0])),
        # Rozdeleni pm2 pro percentilove srovnani (serazene).
        "_pm2_sorted": sorted(pm2),
        "_prices_sorted": sorted(prices),
        # Mediany a serazena rozdeleni po tridach.
        "_pm2_median_by_class": {k: _median(v) for k, v in pm2_by_class.items()},
        "_pm2_sorted_by_class": {k: sorted(v) for k, v in pm2_by_class.items()},
        # Mediany a serazena rozdeleni po dispozici (byty/domy).
        "_pm2_median_by_disp": {k: _median(v) for k, v in pm2_by_disp.items()},
        "_pm2_sorted_by_disp": {k: sorted(v) for k, v in pm2_by_disp.items()},
    }


def comparable_pm2(stats: dict, land_type: str, min_count: int) -> tuple[Optional[float], list[float], str]:
    """Vraci (median srovnatelne tridy, serazene pm2 tridy, popis zakladu srovnani).

    Kdyz ma trida malo vzorku, spadne zpet na celkovy median lokality.
    """
    cls = land_class(land_type)
    med_by = stats.get("_pm2_median_by_class") or {}
    sorted_by = stats.get("_pm2_sorted_by_class") or {}
    arr = sorted_by.get(cls, [])
    if med_by.get(cls) is not None and len(arr) >= min_count:
        popis = "srovnatelných pozemků stejné kategorie"
        return med_by[cls], arr, popis
    return stats.get("pm2_median"), stats.get("_pm2_sorted") or [], "všech nabídek v Chrudimi"


def comparable_pm2_disp(stats: dict, dispozice: Optional[str],
                        min_count: int) -> tuple[Optional[float], list[float], str]:
    """Srovnani bytu/domu podle DISPOZICE. Kdyz ma dispozice malo vzorku, spadne zpet
    na cely segment (vsechny nabidky dane kategorie v Chrudimi).

    Vraci (median srovnatelnych, serazene pm2 srovnatelnych, popis zakladu srovnani).
    """
    med_by = stats.get("_pm2_median_by_disp") or {}
    sorted_by = stats.get("_pm2_sorted_by_disp") or {}
    arr = sorted_by.get(dispozice or "", [])
    if dispozice and med_by.get(dispozice) is not None and len(arr) >= min_count:
        return med_by[dispozice], arr, f"nabídek {dispozice}"
    return stats.get("pm2_median"), stats.get("_pm2_sorted") or [], "všech srovnatelných nabídek v Chrudimi"


def quantile(sorted_values: list[float], q: float) -> Optional[float]:
    """Hodnota na kvantilu q (0..1) v serazenem seznamu (linearni interpolace)."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    pos = q * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return sorted_values[-1]
    return sorted_values[lo] + frac * (sorted_values[lo + 1] - sorted_values[lo])


def percentile_rank(sorted_values: list[float], x: float) -> Optional[float]:
    """Podil hodnot <= x (0..1). Slouzi pro srovnani nabidky s ostatnimi."""
    if not sorted_values:
        return None
    below = sum(1 for v in sorted_values if v <= x)
    return below / len(sorted_values)
