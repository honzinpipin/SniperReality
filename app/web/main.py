"""FastAPI webove rozhrani.

Uvodni rozcestnik (/) -> vyber kategorie. Pozemky (/pozemky) maji skore a
oduvodneni; byty a domy (/byty/{typ}, /domy/{typ}) jsou prehledny seznam s filtry
a razenim bez skore.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from .. import db
from ..config import get_config
from ..view import build_groups, apply_filters, sort_groups
from ..pipeline import run_update
from ..ai import ollama_available
from .. import csu
from ..models import (TYP_POPIS, KAT_POZEMKY, KAT_BYTY, KAT_DOMY, NAB_PRODEJ,
                      NAB_PRONAJEM, KAT_POPIS, NAB_POPIS)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Sniper Chrudim")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

ZDROJE_NAZVY = {"sreality": "Sreality", "idnes": "Reality.iDNES", "bezrealitky": "Bezrealitky"}


# ---- Jinja filtry ----
def _fmt_int(v) -> str:
    return "–" if v is None else f"{int(round(v)):,}".replace(",", " ")


def fmt_kc(v):
    return "–" if v is None else f"{_fmt_int(v)} Kč"


def fmt_kc_mes(v):
    return "–" if v is None else f"{_fmt_int(v)} Kč/měsíc"


def fmt_m2(v):
    return "–" if v is None else f"{_fmt_int(v)} m²"


def fmt_pm2(v):
    return "nelze vypočítat" if v is None else f"{_fmt_int(v)} Kč/m²"


def fmt_datum(v):
    """ISO '2026-07-16' -> '16. 7. 2026'."""
    if not v:
        return ""
    try:
        y, m, d = str(v)[:10].split("-")
        return f"{int(d)}. {int(m)}. {y}"
    except Exception:
        return str(v)


for name, fn in [("kc", fmt_kc), ("kc_mes", fmt_kc_mes), ("m2", fmt_m2),
                 ("pm2", fmt_pm2), ("cislo", _fmt_int), ("datum", fmt_datum)]:
    templates.env.filters[name] = fn


def _asset_ver() -> str:
    """Verze statickych souboru (max mtime) pro cache-busting CSS/JS."""
    try:
        files = (BASE_DIR / "static").glob("*")
        return str(int(max(f.stat().st_mtime for f in files)))
    except Exception:
        return "1"


templates.env.globals["asset_ver"] = _asset_ver


def _to_float(v: Optional[str]) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _ceil_to(v: float, step: int) -> int:
    return int(math.ceil(v / step) * step) if v else step


def _slider_bounds(groups: list[dict], cena_step=25000) -> dict:
    prices = [g["price"] for g in groups if g.get("price")]
    areas = [g["area"] for g in groups if g.get("area")]
    pm2s = [g["price_per_m2"] for g in groups if g.get("price_per_m2")]
    return {
        "cena": {"min": 0, "max": _ceil_to(max(prices) if prices else 0, cena_step * 4) or cena_step * 40, "step": cena_step},
        "plocha": {"min": 0, "max": _ceil_to(max(areas) if areas else 0, 100) or 2000, "step": 10},
        "pm2": {"min": 0, "max": _ceil_to(max(pm2s) if pm2s else 0, 100) or 5000, "step": 25},
    }


def _norm_filters(bounds, cena_min, cena_max, plocha_min, plocha_max, pm2_min, pm2_max):
    def nmin(v, lo):
        fv = _to_float(v)
        return None if (fv is None or fv <= lo) else fv

    def nmax(v, hi):
        fv = _to_float(v)
        return None if (fv is None or fv >= hi) else fv

    return {
        "cena_min": nmin(cena_min, bounds["cena"]["min"]), "cena_max": nmax(cena_max, bounds["cena"]["max"]),
        "plocha_min": nmin(plocha_min, bounds["plocha"]["min"]), "plocha_max": nmax(plocha_max, bounds["plocha"]["max"]),
        "pm2_min": nmin(pm2_min, bounds["pm2"]["min"]), "pm2_max": nmax(pm2_max, bounds["pm2"]["max"]),
    }


# ---------------------------------------------------------------------------
# Uvodni rozcestnik
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    conn = db.get_conn()
    db.init_db(conn)
    runs = db.last_run_info(conn)
    # Pocty po deduplikaci (aktivni hlavni zaznamy), aby dlazdice sedely se seznamem.
    counts = {}
    for cat, off in [(KAT_POZEMKY, NAB_PRODEJ), (KAT_BYTY, NAB_PRODEJ), (KAT_BYTY, NAB_PRONAJEM),
                     (KAT_DOMY, NAB_PRODEJ), (KAT_DOMY, NAB_PRONAJEM)]:
        g, _, _ = build_groups(conn, cat, off, scored=False)
        counts[(cat, off)] = sum(1 for x in g if x["is_active"])
    conn.close()

    dlazdice = [
        {"category": KAT_POZEMKY, "offer": NAB_PRODEJ, "nazev": "Pozemky", "podnazev": "na prodej",
         "url": "/pozemky", "count": counts.get((KAT_POZEMKY, NAB_PRODEJ), 0), "ikona": "▦"},
        {"category": KAT_BYTY, "offer": NAB_PRODEJ, "nazev": "Byty", "podnazev": "na prodej",
         "url": "/byty/prodej", "count": counts.get((KAT_BYTY, NAB_PRODEJ), 0), "ikona": "🏢"},
        {"category": KAT_BYTY, "offer": NAB_PRONAJEM, "nazev": "Byty", "podnazev": "k pronájmu",
         "url": "/byty/pronajem", "count": counts.get((KAT_BYTY, NAB_PRONAJEM), 0), "ikona": "🏢"},
        {"category": KAT_DOMY, "offer": NAB_PRODEJ, "nazev": "Domy", "podnazev": "na prodej",
         "url": "/domy/prodej", "count": counts.get((KAT_DOMY, NAB_PRODEJ), 0), "ikona": "🏠"},
        {"category": KAT_DOMY, "offer": NAB_PRONAJEM, "nazev": "Domy", "podnazev": "k pronájmu",
         "url": "/domy/pronajem", "count": counts.get((KAT_DOMY, NAB_PRONAJEM), 0), "ikona": "🏠"},
    ]
    posledni = max((r["finished_at"] for r in runs), default=None)
    return templates.TemplateResponse("landing.html", {
        "request": request, "dlazdice": dlazdice, "runs": runs,
        "zdroje_nazvy": ZDROJE_NAZVY, "posledni": posledni,
    })


# ---------------------------------------------------------------------------
# Pozemky (se skore) - vyuziva puvodni sablonu index.html
# ---------------------------------------------------------------------------
@app.get("/pozemky", response_class=HTMLResponse)
def pozemky(request: Request, sort: str = "skore",
            typ: Optional[list[str]] = Query(None), zdroj: Optional[list[str]] = Query(None),
            hodnoceni: Optional[list[str]] = Query(None), stav: Optional[list[str]] = Query(None),
            cena_min: Optional[str] = None, cena_max: Optional[str] = None,
            plocha_min: Optional[str] = None, plocha_max: Optional[str] = None,
            pm2_min: Optional[str] = None, pm2_max: Optional[str] = None,
            fsubmit: Optional[str] = None):
    # Prvni nacteni (filtr neodeslan): defaultne jen aktivni - stazene/neoverene skryjeme.
    if fsubmit is None and stav is None:
        stav = ["aktivni"]
    conn = db.get_conn()
    db.init_db(conn)
    groups, stats, _ = build_groups(conn, KAT_POZEMKY, NAB_PRODEJ, scored=True)
    runs = db.last_run_info(conn, KAT_POZEMKY, NAB_PRODEJ)
    conn.close()

    bounds = _slider_bounds(groups)
    filtr = {"typ": typ or None, "zdroj": zdroj or None, "hodnoceni": hodnoceni or None,
             "stav": stav or None,
             **_norm_filters(bounds, cena_min, cena_max, plocha_min, plocha_max, pm2_min, pm2_max)}
    zobrazene = sort_groups(apply_filters(groups, filtr), sort)

    return templates.TemplateResponse("index.html", {
        "request": request, "groups": zobrazene, "celkem": len(groups), "stats": stats,
        "runs": runs, "sort": sort, "bounds": bounds,
        "filtr": {"typ": typ or [], "zdroj": zdroj or [], "hodnoceni": hodnoceni or [],
                  "cena_min": cena_min, "cena_max": cena_max, "plocha_min": plocha_min,
                  "plocha_max": plocha_max, "pm2_min": pm2_min, "pm2_max": pm2_max, "stav": stav or []},
        "typy": TYP_POPIS, "zdroje_nazvy": ZDROJE_NAZVY, "config": get_config(),
    })


# ---------------------------------------------------------------------------
# Byty a domy (seznam bez skore)
# ---------------------------------------------------------------------------
def _nemovitosti_view(request, category, offer_type, sort, dispozice, penb, zdroj,
                      stav, cena_min, cena_max, plocha_min, plocha_max, pm2_min, pm2_max,
                      fsubmit=None):
    # Prvni nacteni (filtr neodeslan): defaultne jen aktivni - stazene/neoverene skryjeme.
    if fsubmit is None and stav is None:
        stav = ["aktivni"]
    conn = db.get_conn()
    db.init_db(conn)
    groups, stats, _ = build_groups(conn, category, offer_type, scored=False)
    runs = db.last_run_info(conn, category, offer_type)
    conn.close()

    bounds = _slider_bounds(groups, cena_step=(1000 if offer_type == NAB_PRONAJEM else 25000))
    filtr = {"dispozice": dispozice or None, "penb": penb or None, "zdroj": zdroj or None,
             "stav": stav or None,
             **_norm_filters(bounds, cena_min, cena_max, plocha_min, plocha_max, pm2_min, pm2_max)}
    zobrazene = sort_groups(apply_filters(groups, filtr), sort)

    dispozice_moznosti = sorted({g["dispozice"] for g in groups if g.get("dispozice")})
    penb_moznosti = sorted({g["penb"] for g in groups if g.get("penb")})

    # Stav AI (jen u pronajmu, kde se energie dohledavaji).
    ai_status = None
    if offer_type == NAB_PRONAJEM:
        cfg = get_config()
        filled = sum(1 for g in groups if g.get("energy_ai"))
        ai_status = {"enabled": cfg.get("ai", {}).get("enabled", False),
                     "available": ollama_available(cfg),
                     "model": cfg.get("ai", {}).get("model"), "filled": filled}

    return templates.TemplateResponse("nemovitosti.html", {
        "request": request, "groups": zobrazene, "celkem": len(groups), "stats": stats,
        "runs": runs, "sort": sort, "bounds": bounds, "category": category,
        "offer_type": offer_type, "kat_popis": KAT_POPIS[category], "nab_popis": NAB_POPIS[offer_type],
        "je_pronajem": offer_type == NAB_PRONAJEM, "ai_status": ai_status,
        "csu_chart": csu.category_chart(category) if offer_type == NAB_PRODEJ else None,
        "filtr": {"dispozice": dispozice or [], "penb": penb or [], "zdroj": zdroj or [],
                  "stav": stav or [], "cena_min": cena_min, "cena_max": cena_max,
                  "plocha_min": plocha_min, "plocha_max": plocha_max,
                  "pm2_min": pm2_min, "pm2_max": pm2_max},
        "dispozice_moznosti": dispozice_moznosti, "penb_moznosti": penb_moznosti,
        "zdroje_nazvy": ZDROJE_NAZVY,
    })


def _validate_offer(offer: str) -> str:
    return NAB_PRONAJEM if offer == "pronajem" else NAB_PRODEJ


@app.get("/byty/{offer}", response_class=HTMLResponse)
def byty(request: Request, offer: str, sort: str = "cena_m2_asc",
         dispozice: Optional[list[str]] = Query(None), penb: Optional[list[str]] = Query(None),
         zdroj: Optional[list[str]] = Query(None), stav: Optional[list[str]] = Query(None),
         cena_min: Optional[str] = None, cena_max: Optional[str] = None,
         plocha_min: Optional[str] = None, plocha_max: Optional[str] = None,
         pm2_min: Optional[str] = None, pm2_max: Optional[str] = None,
         fsubmit: Optional[str] = None):
    return _nemovitosti_view(request, KAT_BYTY, _validate_offer(offer), sort, dispozice, penb,
                             zdroj, stav, cena_min, cena_max, plocha_min, plocha_max, pm2_min, pm2_max,
                             fsubmit)


@app.get("/domy/{offer}", response_class=HTMLResponse)
def domy(request: Request, offer: str, sort: str = "cena_m2_asc",
         dispozice: Optional[list[str]] = Query(None), penb: Optional[list[str]] = Query(None),
         zdroj: Optional[list[str]] = Query(None), stav: Optional[list[str]] = Query(None),
         cena_min: Optional[str] = None, cena_max: Optional[str] = None,
         plocha_min: Optional[str] = None, plocha_max: Optional[str] = None,
         pm2_min: Optional[str] = None, pm2_max: Optional[str] = None,
         fsubmit: Optional[str] = None):
    return _nemovitosti_view(request, KAT_DOMY, _validate_offer(offer), sort, dispozice, penb,
                             zdroj, stav, cena_min, cena_max, plocha_min, plocha_max, pm2_min, pm2_max,
                             fsubmit)


# ---------------------------------------------------------------------------
# Aktualizace
# ---------------------------------------------------------------------------
@app.post("/aktualizovat")
async def aktualizovat(request: Request, category: Optional[str] = Form(None),
                       offer_type: Optional[str] = Form(None)):
    # Kdyz prijde platny segment, aktualizuje se JEN ten (rychle). Jinak vse.
    segments = None
    if category in (KAT_POZEMKY, KAT_BYTY, KAT_DOMY) and offer_type in (NAB_PRODEJ, NAB_PRONAJEM):
        segments = [(category, offer_type)]
    await run_in_threadpool(lambda: run_update(segments=segments))
    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)


@app.get("/api/nabidky")
def api_nabidky(category: str = KAT_POZEMKY, offer: str = NAB_PRODEJ):
    conn = db.get_conn()
    db.init_db(conn)
    groups, stats, _ = build_groups(conn, category, _validate_offer(offer),
                                    scored=(category == KAT_POZEMKY))
    conn.close()
    return JSONResponse({"pocet": len(groups),
                         "statistiky": {k: v for k, v in stats.items() if not k.startswith("_")},
                         "nabidky": groups})
