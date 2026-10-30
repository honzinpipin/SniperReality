"""Read model pro web: z ulozenych nabidek postavi hlavni zaznamy (skupiny),
spocita statistiky lokality, skore a oduvodneni, a aplikuje filtry a razeni.

Statistiky i skore se pocitaji nad AKTIVNIMI skupinami, filtry az na zobrazeni.
"""
from __future__ import annotations

import json
from typing import Optional

from . import db
from .config import get_config
from .dedup import cluster
from .stats import compute_locality_stats
from .scoring import compute_score
from .reasoning import build_reasoning
from .valuation import (evaluate_property, explain_with_factors, apply_risks,
                        build_reasoning as build_property_reasoning, score_property)
from .doprava import nearest_stop
from .db import STAV_AKTIVNI
from .models import TYP_NEUVEDENO, TYP_POPIS

# Priorita zdroje pri vyberu hlavniho zaznamu (bohatost dat).
_SOURCE_PRIORITY = {"sreality": 0, "bezrealitky": 1, "idnes": 2}
# Priorita typu pri slouceni (specifictejsi driv).
_TYPE_PRIORITY = ["stavebni", "bydleni", "komercni", "zahrada", "zemedelsky", "ostatni", TYP_NEUVEDENO]


def _row_to_dict(r) -> dict:
    d = dict(r)
    try:
        d["utilities"] = json.loads(r["utilities_json"]) if r["utilities_json"] else {}
    except Exception:
        d["utilities"] = {}
    try:
        d["ai_factors"] = json.loads(r["ai_factors_json"]) if r["ai_factors_json"] else []
    except Exception:
        d["ai_factors"] = []
    return d


def _merge_utilities(members: list[dict]) -> dict:
    """Sit je True, pokud ji uvadi aspon jeden zdroj; jinak None (neuvedeno)."""
    out = {"elektrina": None, "voda": None, "kanalizace": None, "plyn": None, "pristup": None}
    for m in members:
        u = m.get("utilities") or {}
        for k in ("elektrina", "voda", "kanalizace", "plyn"):
            if u.get(k) is True:
                out[k] = True
        if u.get("pristup") and not out["pristup"]:
            out["pristup"] = u["pristup"]
    return out


def _pick_type(members: list[dict]) -> str:
    types = [m.get("land_type") or TYP_NEUVEDENO for m in members]
    for t in _TYPE_PRIORITY:
        if t in types:
            return t
    return TYP_NEUVEDENO


def _build_group(gid: int, members: list[dict], conn) -> dict:
    # Hlavni zaznam: nejbohatsi zdroj s cenou i plochou.
    def rank(m):
        has_both = 0 if (m.get("price") and m.get("area")) else 1
        return (has_both, _SOURCE_PRIORITY.get(m["source"], 9))
    primary = sorted(members, key=rank)[0]

    prices = [m["price"] for m in members if m.get("price")]
    areas = [m["area"] for m in members if m.get("area")]
    price = primary.get("price") or (prices[0] if prices else None)
    area = primary.get("area") or (areas[0] if areas else None)
    pm2 = (price / area) if (price and area and area > 0) else None

    utils = _merge_utilities(members)
    land_type = _pick_type(members)

    # price_old / dny na trhu
    price_old = None
    for m in members:
        if m.get("price_old"):
            price_old = m["price_old"]
            break
    # Doba v nabidce = od nejstarsiho znamého data (realne "Vloženo" ze zdroje ma prednost,
    # proto bereme nejvyssi pocet dni napric zdroji), aby to odpovidalo skutecnosti.
    days = None
    for m in members:
        d = db.days_on_market(conn, m["id"])
        if d is not None:
            days = d if days is None else max(days, d)
    # realne datum vlozeni ze zdroje (nejstarsi), pro zobrazeni "vloženo"
    added_dates = [m.get("date_added") for m in members if m.get("date_added")]
    date_added = min(added_dates) if added_dates else None

    is_active = any((m.get("status") == STAV_AKTIVNI) for m in members)
    # Stav skupiny: aktivni, jinak "nejlepsi" dostupny stav clenu.
    if is_active:
        group_status = STAV_AKTIVNI
    else:
        statuses = [m.get("status") for m in members]
        for s in ("nelze ověřit", "pravděpodobně staženo", "zdroj nedostupný"):
            if s in statuses:
                group_status = s
                break
        else:
            group_status = statuses[0] if statuses else "nelze ověřit"

    # Zdroje a odkazy
    zdroje = []
    for m in sorted(members, key=lambda x: _SOURCE_PRIORITY.get(x["source"], 9)):
        zdroje.append({
            "source": m["source"], "url": m["url"], "price": m.get("price"),
            "area": m.get("area"), "last_checked": m.get("last_checked"),
            "status": m.get("status"),
        })

    price_diff = (max(prices) - min(prices)) if len(prices) >= 2 and max(prices) != min(prices) else None
    area_diff = (max(areas) - min(areas)) if len(areas) >= 2 and max(areas) != min(areas) else None

    def first(attr):
        for m in members:
            if m.get(attr) not in (None, ""):
                return m.get(attr)
        return None

    # AI faktory: vezmeme z clena s nejvic dolozenymi faktory (prednost bohatsimu zdroji).
    def _known(m):
        return sum(1 for x in (m.get("ai_factors") or []) if x.get("origin") == "LISTING_CLAIM")
    fmember = sorted(
        [m for m in members if m.get("ai_factors")],
        key=lambda m: (-_known(m), _SOURCE_PRIORITY.get(m["source"], 9)),
    )
    ai_factors = fmember[0].get("ai_factors") if fmember else []
    ai_factors_provider = fmember[0].get("ai_factors_provider") if fmember else None
    from .ai_factors import summarize as _sum_factors
    ai_faktory_souhrn = _sum_factors(ai_factors) if ai_factors else None

    # Vizualni vzhled z fotek (AI odhad) - od clena, ktery ho ma (prednost bohatsimu zdroji).
    vmember = sorted([m for m in members if m.get("ai_vzhled")],
                     key=lambda m: _SOURCE_PRIORITY.get(m["source"], 9))
    vzhled = ({"value": vmember[0]["ai_vzhled"], "duvod": vmember[0].get("ai_vzhled_duvod")}
              if vmember else None)

    # Energie vezmeme z clena, ktery ji ma (prednostne s castkou, jinak s poznamkou),
    # vc. priznaku, zda ji dohledala AI.
    emember = (next((m for m in members if m.get("energy_cost") is not None), None)
               or next((m for m in members if m.get("energy_note")), None))
    e_cost = emember.get("energy_cost") if emember else None
    e_note = emember.get("energy_note") if emember else None
    e_ai = bool(emember.get("energy_ai")) if emember else False
    # Pojistka: naklady nesmi byt >= najem (zjevne spatne nacteny udaj -> zahodit castku).
    if e_cost and price and e_cost >= price:
        e_cost = None
    # Celkove mesicni naklady u pronajmu = najem + naklady.
    offer = primary.get("offer_type")
    total_monthly = (price + e_cost) if (offer == "pronajem" and price and e_cost) else None

    return {
        "group_id": gid,
        "title": primary.get("title"),
        "category": primary.get("category"), "offer_type": primary.get("offer_type"),
        "price": price, "area": area, "price_per_m2": pm2, "total_monthly": total_monthly,
        "dispozice": first("dispozice"), "penb": first("penb"),
        "energy_cost": e_cost, "energy_note": e_note, "energy_ai": e_ai,
        "land_type": land_type, "land_type_popis": TYP_POPIS.get(land_type, land_type),
        "city": primary.get("city"), "city_part": primary.get("city_part"),
        "locality": primary.get("locality"),
        "description": primary.get("description"),
        "image_url": primary.get("image_url"),
        "utilities": utils, "price_old": price_old, "days_on_market": days,
        "date_added": date_added,
        "ai_factors": ai_factors, "ai_factors_provider": ai_factors_provider,
        "ai_faktory_souhrn": ai_faktory_souhrn, "vzhled": vzhled,
        "is_active": is_active, "status": group_status,
        "sources": [z["source"] for z in zdroje], "zdroje": zdroje,
        "primary_url": primary.get("url"),
        "last_checked": primary.get("last_checked"),
        "price_diff": price_diff, "area_diff": area_diff,
        "lat": primary.get("lat"), "lon": primary.get("lon"),
        "zastavka": nearest_stop(primary.get("lat"), primary.get("lon")),
    }


def build_groups(conn, category: str = None, offer_type: str = None,
                 scored: bool = True) -> tuple[list[dict], dict, set[int]]:
    """Sestavi hlavni zaznamy pro dany segment (kategorie+typ). Kdyz category/offer
    nejsou zadane, vezme vsechny nabidky (zpetna kompatibilita).

    scored=True -> spocita skore a oduvodneni (pozemky). scored=False -> jen seznam
    s hodnotami (byty/domy), bez skore.
    """
    cfg = get_config()
    if category and offer_type:
        raw = db.fetch_segment(conn, category, offer_type)
    else:
        raw = db.fetch_all_listings(conn)
    rows = [_row_to_dict(r) for r in raw]
    if not rows:
        return [], compute_locality_stats([]), set()

    cluster_input = [{"id": r["id"], "source": r["source"], "area": r["area"],
                      "price": r["price"], "lat": r["lat"], "lon": r["lon"]} for r in rows]
    mapping, uncertain = cluster(cluster_input, cfg)

    by_group: dict[int, list[dict]] = {}
    by_id = {r["id"]: r for r in rows}
    for lid, gid in mapping.items():
        by_group.setdefault(gid, []).append(by_id[lid])

    groups = [_build_group(gid, members, conn) for gid, members in by_group.items()]
    stats = compute_locality_stats(groups)

    for g in groups:
        g["mozna_duplicita"] = g["group_id"] in uncertain and len(g["sources"]) > 1
        if scored:
            ev = compute_score(g, stats, cfg)
            g["evaluation"] = ev
            g["score"] = ev["score"]
            g["label"] = ev["label"]
            g["jistota"] = ev["jistota"]
            g["valuation"] = ev["valuation"]
            g["reasoning"] = build_reasoning(g, ev, stats, cfg)
        else:
            # Byty/domy: misto skore cenova kotva vuci srovnatelnym nabidkam,
            # a faktory z popisu jako vysvetleni odchylky (ne body).
            g["score"] = None
            g["oceneni"] = evaluate_property(g, stats, cfg)
            # Vazna rizika (M) snizi jistotu drive, nez se rozhodne o vykladove vete.
            apply_risks(g["oceneni"], g.get("ai_factors") or [])
            g["faktory_vyklad"] = explain_with_factors(g.get("ai_factors") or [], g["oceneni"])
            g["oduvodneni"] = build_property_reasoning(g, g["oceneni"], g["faktory_vyklad"], stats)
            # Skore atraktivity (orientacni poradi) - vc. do g["score"] pro razeni.
            g["skore_info"] = score_property(g, g["oceneni"], g["faktory_vyklad"], cfg)
            g["score"] = g["skore_info"]["skore"]

    return groups, stats, uncertain


# ---------------------------------------------------------------------------
# Filtry a razeni (aplikuji se az na zobrazeni, statistiky zustavaji nad vsemi)
# ---------------------------------------------------------------------------

def _drop_pct(g) -> float:
    if g.get("price") and g.get("price_old") and g["price_old"] > g["price"]:
        return (g["price_old"] - g["price"]) / g["price_old"]
    return 0.0


# Razeni: u ceny/m2, ceny/najmu i plochy oba smery; None hodnoty vzdy na konec.
SORT_KEYS = {
    "skore": lambda g: -(g["score"] or 0),
    "cena_m2_asc": lambda g: (g["price_per_m2"] is None, g["price_per_m2"] or 0),
    "cena_m2_desc": lambda g: (g["price_per_m2"] is None, -(g["price_per_m2"] or 0)),
    "cena_asc": lambda g: (g["price"] is None, g["price"] or 0),
    "cena_desc": lambda g: (g["price"] is None, -(g["price"] or 0)),
    "plocha_asc": lambda g: (g["area"] is None, g["area"] or 0),
    "plocha_desc": lambda g: (g["area"] is None, -(g["area"] or 0)),
    "nejnovejsi": lambda g: (g["days_on_market"] if g["days_on_market"] is not None else 1e9),
    "zlevneni": lambda g: -_drop_pct(g),
}


def _as_list(v):
    """Normalizuje filtr na seznam vybranych hodnot (checkboxy = vice hodnot)."""
    if not v:
        return None
    return v if isinstance(v, list) else [v]


def apply_filters(groups: list[dict], f: dict) -> list[dict]:
    typ = _as_list(f.get("typ"))
    disp = _as_list(f.get("dispozice"))
    penb = _as_list(f.get("penb"))
    zdroj = _as_list(f.get("zdroj"))
    hodn = _as_list(f.get("hodnoceni"))
    stav = _as_list(f.get("stav"))
    out = []
    for g in groups:
        if typ and g["land_type"] not in typ:
            continue
        if disp and g.get("dispozice") not in disp:
            continue
        if penb and g.get("penb") not in penb:
            continue
        if zdroj and not any(z in g["sources"] for z in zdroj):
            continue
        if hodn and g.get("label") not in hodn:
            continue
        if f.get("cena_min") is not None and (g["price"] is None or g["price"] < f["cena_min"]):
            continue
        if f.get("cena_max") is not None and (g["price"] is None or g["price"] > f["cena_max"]):
            continue
        if f.get("plocha_min") is not None and (g["area"] is None or g["area"] < f["plocha_min"]):
            continue
        if f.get("plocha_max") is not None and (g["area"] is None or g["area"] > f["plocha_max"]):
            continue
        if f.get("pm2_min") is not None and (g["price_per_m2"] is None or g["price_per_m2"] < f["pm2_min"]):
            continue
        if f.get("pm2_max") is not None and (g["price_per_m2"] is None or g["price_per_m2"] > f["pm2_max"]):
            continue
        if stav and not (("aktivni" in stav and g["is_active"])
                         or ("neovereno" in stav and not g["is_active"])):
            continue
        out.append(g)
    return out


def sort_groups(groups: list[dict], sort: str) -> list[dict]:
    key = SORT_KEYS.get(sort) or SORT_KEYS["cena_m2_asc"]
    return sorted(groups, key=key)
