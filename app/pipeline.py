"""Orchestrace aktualizace: pro kazdy segment (kategorie x typ nabidky) projede
vsechny tri zdroje, ulozi nabidky a odstrani/oznaci zmizele. Kazdy zdroj i segment
je osetreny samostatne - selhani jednoho neshodi ostatni. Nikdy netvrdi 'prodano'.
"""
from __future__ import annotations

from datetime import datetime

from . import db
from .config import get_config
from .scrapers import SCRAPERS
from .models import (KAT_POZEMKY, KAT_BYTY, KAT_DOMY, NAB_PRODEJ, NAB_PRONAJEM,
                     KAT_POPIS, NAB_POPIS)

# Segmenty ke stahovani. Pozemky jen prodej; byty a domy prodej i pronajem.
SEGMENTS = [
    (KAT_POZEMKY, NAB_PRODEJ),
    (KAT_BYTY, NAB_PRODEJ),
    (KAT_BYTY, NAB_PRONAJEM),
    (KAT_DOMY, NAB_PRODEJ),
    (KAT_DOMY, NAB_PRONAJEM),
]


def run_update(fresh_config: bool = True, segments=None) -> dict:
    cfg = get_config(fresh=fresh_config)
    dny = cfg["stav"]["dny_do_pravdepodobne_stazeno"]
    conn = db.get_conn()
    db.init_db(conn)

    segments = segments or SEGMENTS
    summary = {"segmenty": [], "cas": datetime.now().isoformat(timespec="seconds")}

    for category, offer_type in segments:
        seg_info = {"kategorie": KAT_POPIS[category], "typ": NAB_POPIS[offer_type],
                    "category": category, "offer_type": offer_type, "zdroje": {}}
        for key, (nazev, fn) in SCRAPERS.items():
            started = datetime.now().isoformat(timespec="seconds")
            try:
                res = fn(category, offer_type)
            except Exception as e:
                db.mark_source_unavailable(conn, key, category, offer_type)
                db.log_run(conn, started, key, category, offer_type, "chyba", 0, str(e))
                seg_info["zdroje"][key] = {"nazev": nazev, "ok": False, "pocet": 0, "zprava": str(e)}
                conn.commit()
                continue

            if not res.ok:
                db.mark_source_unavailable(conn, key, category, offer_type)
                db.log_run(conn, started, key, category, offer_type, "nedostupny", 0, res.message)
                seg_info["zdroje"][key] = {"nazev": nazev, "ok": False, "pocet": 0, "zprava": res.message}
                conn.commit()
                continue

            seen_ids: set[str] = set()
            for l in res.listings:
                db.upsert_listing(conn, l)
                seen_ids.add(l.source_id)
            db.mark_missing_listings(conn, key, category, offer_type, seen_ids, dny)
            db.log_run(conn, started, key, category, offer_type, "ok", len(res.listings), res.message)
            seg_info["zdroje"][key] = {"nazev": nazev, "ok": True, "pocet": len(res.listings),
                                       "zprava": res.message}
            conn.commit()
        summary["segmenty"].append(seg_info)

    # Dohledani ceny energii v popisu pronajmu lokalni AI (volitelne, tichy fallback).
    summary["ai"] = _enrich_energy_ai(conn, cfg)
    # Extrakce strukturovanych faktoru z popisu bytu/domu (OpenAI, fallback Ollama).
    summary["ai_faktory"] = _enrich_factors_ai(conn, cfg)
    # Posouzeni vizualni modernosti z fotek (OpenAI vision, volitelne).
    summary["ai_vzhled"] = _enrich_vzhled_ai(conn, cfg)

    _persist_groups(conn, cfg, segments)
    conn.commit()
    conn.close()
    return summary


def _enrich_energy_ai(conn, cfg) -> dict:
    """Pro pronajmy bez zjistene ceny energii zkusi cenu najit v popisu pres Ollamu."""
    from .ai import ollama_available, extract_energy
    ai_cfg = cfg.get("ai", {})
    if not ai_cfg.get("enabled"):
        return {"pouzito": False, "duvod": "vypnuto v configu"}
    if not ollama_available(cfg):
        return {"pouzito": False, "duvod": "Ollama nedostupná (nebeží nebo není nainstalována)"}
    rows = db.listings_for_ai_energy(conn, ai_cfg.get("max_za_beh", 80))
    doplneno = 0
    for r in rows:
        cost, note = extract_energy(r["description"], cfg)
        db.set_ai_energy(conn, r["id"], cost, note)
        if cost is not None or note:
            doplneno += 1
    conn.commit()
    return {"pouzito": True, "provereno": len(rows), "doplneno": doplneno,
            "model": ai_cfg.get("model")}


def _enrich_factors_ai(conn, cfg) -> dict:
    """Pro byty/domy s popisem spusti AI extrakci faktoru a vysledek nacachuje.
    Primarne OpenAI; kdyz neni klic nebo selze, fallback na Ollamu; kdyz nebezi ani
    ta, krok se tise preskoci."""
    import json as _json
    from .ai_factors import extract_factors, openai_ready, summarize
    from .ai import ollama_available
    ai_cfg = cfg.get("ai", {})
    fac_cfg = ai_cfg.get("faktory", {})
    if not ai_cfg.get("enabled") or not fac_cfg.get("enabled", True):
        return {"pouzito": False, "duvod": "vypnuto v configu"}
    has_openai = openai_ready(cfg)
    has_ollama = ollama_available(cfg)
    if not (has_openai or has_ollama):
        return {"pouzito": False,
                "duvod": "žádný poskytovatel (OpenAI bez klíče a Ollama nedostupná)"}

    rows = db.listings_for_ai_factors(conn, fac_cfg.get("max_za_beh", 40))
    doplneno = 0
    provideri: dict[str, int] = {}
    for r in rows:
        res = extract_factors(r["title"], r["description"], r["category"], cfg)
        faktory = res["faktory"]
        provider = res["provider"]
        payload = _json.dumps(faktory, ensure_ascii=False) if faktory else None
        db.set_ai_factors(conn, r["id"], payload, provider)
        if faktory and summarize(faktory)["znamych"] > 0:
            doplneno += 1
        if provider:
            provideri[provider] = provideri.get(provider, 0) + 1
    conn.commit()
    return {"pouzito": True, "provereno": len(rows), "doplneno": doplneno,
            "provideri": provideri,
            "primarni": "openai" if has_openai else "ollama"}


def _enrich_vzhled_ai(conn, cfg) -> dict:
    """Pro byty/domy s fotkami odhadne vizualni modernost interieru (OpenAI vision)
    a vysledek nacachuje. Bez OpenAI klice se tise preskoci."""
    import json as _json
    from .ai_factors import extract_vzhled, openai_ready
    ai_cfg = cfg.get("ai", {})
    vz_cfg = ai_cfg.get("vzhled", {})
    if not ai_cfg.get("enabled") or not vz_cfg.get("enabled", True):
        return {"pouzito": False, "duvod": "vypnuto v configu"}
    if not openai_ready(cfg):
        return {"pouzito": False, "duvod": "OpenAI bez klíče (vision zatím jen přes OpenAI)"}

    rows = db.listings_for_ai_vzhled(conn, vz_cfg.get("max_za_beh", 40))
    doplneno = 0
    for r in rows:
        try:
            urls = _json.loads(r["images_json"]) if r["images_json"] else []
        except Exception:
            urls = []
        res = extract_vzhled(urls, cfg)
        db.set_ai_vzhled(conn, r["id"], res["value"], res["duvod"])
        if res["value"]:
            doplneno += 1
    conn.commit()
    return {"pouzito": True, "provereno": len(rows), "doplneno": doplneno}


def _persist_groups(conn, cfg, segments) -> None:
    """Deduplikace se pocita v ramci kazdeho segmentu zvlast (nemichat kategorie)."""
    from .dedup import cluster
    for category, offer_type in segments:
        rows = db.fetch_segment(conn, category, offer_type)
        listings = [{"id": r["id"], "source": r["source"], "area": r["area"],
                     "price": r["price"], "lat": r["lat"], "lon": r["lon"]} for r in rows]
        if not listings:
            continue
        mapping, _ = cluster(listings, cfg)
        db.set_group_ids(conn, mapping)
