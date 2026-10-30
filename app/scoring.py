"""Vypocet skore nabidky (body 8, 9, 10 zadani).

Skore 0-100 podle predem definovanych pravidel a vah z config.toml. Vysledek NENI
znalecke oceneni - je to interni poradi podle dostupnych NABIDKOVYCH udaju.
Kazdy dilci bod je dohledatelny v 'breakdown'.
"""
from __future__ import annotations

from typing import Optional

from .stats import percentile_rank, comparable_pm2
from .models import TYP_NEUVEDENO, TRIDA_STAVEBNI, land_class

# Slovni hodnoceni jistoty
JISTOTA_VYSOKA = "vysoká"
JISTOTA_STREDNI = "střední"
JISTOTA_NIZKA = "nízká"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _score_pm2(pm2, med, vaha, suspicious: bool, zaklad: str) -> tuple[float, str]:
    """Cena za m2 vuci medianu SROVNATELNYCH nabidek. Levnejsi = vic bodu.
    U podezrele nizke ceny (pravdepodobne neuplny udaj) nebodovat jako super levne.
    """
    if pm2 is None or not med:
        return vaha * 0.5, "cena za m² neznámá – přiděleno 50 % bodů"
    if suspicious:
        return vaha * 0.5, "cena/m² je nezvykle nízká – údaj pravděpodobně neúplný, nebráno jako výhoda"
    ratio = pm2 / med
    # ratio 0.7 a min -> plny pocet; 1.3 a vic -> 0. Linearne mezi.
    frac = _clamp((1.3 - ratio) / (1.3 - 0.7), 0.0, 1.0)
    pct = round((ratio - 1) * 100)
    smer = "pod" if pct < 0 else "nad"
    return vaha * frac, f"cena/m² je {abs(pct)} % {smer} mediánem {zaklad}"


def _score_total_price(price, stats, vaha) -> tuple[float, str]:
    """Celkova cena vuci rozdeleni - levnejsi nabidky vic bodu."""
    arr = stats.get("_prices_sorted") or []
    if price is None or not arr:
        return vaha * 0.5, "celková cena neznámá – 50 % bodů"
    rank = percentile_rank(arr, price)  # 0 = nejlevnejsi
    return vaha * (1 - rank), f"celková cena je v {round(rank * 100)}. percentilu (méně = levnější)"


def _score_type(land_type, cfg, vaha) -> tuple[float, str]:
    body_map = cfg["skore_typ"]
    body = body_map.get(land_type, body_map.get("neuvedeno", 6))
    maxb = max(body_map.values()) or 1
    return vaha * (body / maxb), f"typ '{land_type}' hodnocen {body}/{maxb}"


def _area_band(area, cfg) -> Optional[str]:
    if area is None:
        return None
    p = cfg["plocha"]
    if area < p["maly_do"]:
        return "maly"
    if area >= p["velky_od"]:
        return "velky"
    return "stredni"


def _score_area(area, cfg, vaha) -> tuple[float, str]:
    band = _area_band(area, cfg)
    if band is None:
        return vaha * 0.5, "plocha neznámá – 50 % bodů"
    body_map = cfg["skore_plocha"]
    body = body_map.get(band, 5)
    maxb = max(body_map.values()) or 1
    naz = {"maly": "malý", "stredni": "střední", "velky": "velký"}[band]
    return vaha * (body / maxb), f"{naz} pozemek ({body}/{maxb})"


def _score_utilities(utils: dict, vaha) -> tuple[float, str, int]:
    """Kazda uvedena sit prida body. Neuvedeno NEZNAMENA 'neni' - jen 0 bodu."""
    keys = ["elektrina", "voda", "kanalizace", "plyn"]
    present = [k for k in keys if utils.get(k) is True]
    road = bool(utils.get("pristup"))
    total_slots = len(keys) + 1  # +pristup
    got = len(present) + (1 if road else 0)
    if got == 0:
        return 0.0, "sítě neuvedeny", 0
    txt = ", ".join(present + (["přístupová cesta"] if road else []))
    return vaha * (got / total_slots), f"uvedeno: {txt}", got


def _score_history(price, price_old, days, vaha) -> tuple[float, str]:
    if price and price_old and price_old > price:
        drop = (price_old - price) / price_old
        return vaha * _clamp(drop / 0.2, 0.3, 1.0), f"zlevněno o {round(drop * 100)} %"
    if days is not None:
        # cerstve nabidky mala zminka; dlouho inzerovane mensi bonus (prostor k jednani)
        if days <= 14:
            return vaha * 0.4, f"nová nabídka ({days} dní)"
        if days >= 90:
            return vaha * 0.6, f"dlouho v nabídce ({days} dní)"
        return vaha * 0.5, f"v nabídce {days} dní"
    return vaha * 0.4, "bez cenové historie"


def compute_score(group: dict, stats: dict, cfg: dict) -> dict:
    """group: dict s price, area, price_per_m2, land_type, utilities(dict),
    price_old, days_on_market. Vraci kompletni vyhodnoceni.
    """
    w = cfg["skore_vahy"]
    utils = group.get("utilities") or {}
    pm2 = group.get("price_per_m2")

    # Srovnani vuci PODOBNYM nabidkam (stejna trida pozemku), s fallbackem na celou lokalitu.
    comp_med, comp_sorted, zaklad = comparable_pm2(
        stats, group.get("land_type"), cfg["cena_pasma"]["min_srovnatelnych"])

    # Guard: podezrele nizka cena/m2 u stavebnich/komercnich pozemku.
    suspicious = bool(
        pm2 and comp_med
        and land_class(group.get("land_type")) == TRIDA_STAVEBNI
        and pm2 < comp_med * cfg["cena_pasma"]["podezrele_pod"]
    )

    s_pm2, r_pm2 = _score_pm2(pm2, comp_med, w["cena_za_m2"], suspicious, zaklad)
    s_price, r_price = _score_total_price(group.get("price"), stats, w["celkova_cena"])
    s_type, r_type = _score_type(group.get("land_type"), cfg, w["typ_pozemku"])
    s_area, r_area = _score_area(group.get("area"), cfg, w["plocha"])
    s_util, r_util, util_count = _score_utilities(utils, w["site"])
    s_hist, r_hist = _score_history(group.get("price"), group.get("price_old"),
                                    group.get("days_on_market"), w["historie"])

    total = round(s_pm2 + s_price + s_type + s_area + s_util + s_hist)
    total = int(_clamp(total, 0, 100))

    breakdown = [
        {"kriterium": "Cena za m²", "body": round(s_pm2, 1), "max": w["cena_za_m2"], "poznamka": r_pm2},
        {"kriterium": "Celková cena", "body": round(s_price, 1), "max": w["celkova_cena"], "poznamka": r_price},
        {"kriterium": "Typ pozemku", "body": round(s_type, 1), "max": w["typ_pozemku"], "poznamka": r_type},
        {"kriterium": "Plocha", "body": round(s_area, 1), "max": w["plocha"], "poznamka": r_area},
        {"kriterium": "Dostupnost sítí", "body": round(s_util, 1), "max": w["site"], "poznamka": r_util},
        {"kriterium": "Historie / délka", "body": round(s_hist, 1), "max": w["historie"], "poznamka": r_hist},
    ]

    # Jistota hodnoceni podle poctu chybejicich klicovych udaju.
    missing = []
    if not group.get("price"):
        missing.append("cena")
    if not group.get("area"):
        missing.append("výměra")
    if group.get("land_type") in (None, TYP_NEUVEDENO):
        missing.append("typ pozemku")
    if util_count == 0:
        missing.append("sítě")
    if suspicious:
        missing.append("cena/m² (nespolehlivá)")
    if suspicious or len(missing) >= 3:
        jistota = JISTOTA_NIZKA
    elif len(missing) == 0:
        jistota = JISTOTA_VYSOKA
    else:
        jistota = JISTOTA_STREDNI

    # Slovni hodnoceni.
    prahy = cfg["hodnoceni_prahy"]
    if not group.get("price") or not group.get("area") or suspicious:
        label = "nelze spolehlivě vyhodnotit"
    elif total >= prahy["velmi_zajimava"]:
        label = "velmi zajímavá nabídka"
    elif total >= prahy["zajimava"]:
        label = "zajímavá nabídka"
    elif total >= prahy["bezna"]:
        label = "běžná nabídka"
    else:
        label = "méně zajímavá nabídka"

    # Podhodnoceni / nadhodnoceni (bod 10) - jen dle nabidkovych cen, vuci podobnym.
    valuation = _valuation(pm2, comp_med, cfg, suspicious)

    return {
        "score": total,
        "label": label,
        "jistota": jistota,
        "breakdown": breakdown,
        "missing": missing,
        "valuation": valuation,
        "srovnani_zaklad": zaklad,
        "suspicious_price": suspicious,
    }


def _valuation(pm2, med, cfg, suspicious: bool) -> str:
    if suspicious:
        return "nelze vyhodnotit (cena pravděpodobně neúplná)"
    if pm2 is None or not med:
        return "nelze vyhodnotit"
    p = cfg["cena_pasma"]
    if pm2 < med * p["levnejsi_pod"]:
        return "levnější než srovnatelné nabídky"
    if pm2 > med * p["drazsi_nad"]:
        return "dražší než srovnatelné nabídky"
    return "přibližně v běžném rozmezí"
