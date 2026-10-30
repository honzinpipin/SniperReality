"""Textove oduvodneni poradi (bod 11 zadani).

Text se sklada VYHRADNE z vypocitanych hodnot a skutecne dostupnych udaju.
Nedoplnuji se nezname skutecnosti. Zadny volny "AI vymysl" - sablonovite vety
nad realnymi cisly, aby bylo oduvodneni dohledatelne.
"""
from __future__ import annotations

from typing import Optional

from .models import TYP_POPIS, TYP_NEUVEDENO
from .stats import comparable_pm2


def _pm2_vs_median(pm2: Optional[float], median: Optional[float], zaklad: str) -> Optional[str]:
    if pm2 is None or not median:
        return None
    diff = (pm2 - median) / median * 100
    if abs(diff) < 3:
        return f"cena za m² je přibližně na úrovni mediánu {zaklad}"
    smer = "pod" if diff < 0 else "nad"
    return f"cena za m² je přibližně {abs(round(diff))} % {smer} mediánem {zaklad}"


def build_reasoning(group: dict, evaluation: dict, stats: dict, cfg: dict) -> dict:
    """Vraci dict: {zarazeni, duvody[], omezeni[], jistota}."""
    label = evaluation = evaluation if False else evaluation  # no-op, keep signature clear
    ev = evaluation
    duvody: list[str] = []
    omezeni: list[str] = []

    # Srovnani vuci podobnym nabidkam (stejna trida pozemku).
    comp_med, _arr, zaklad = comparable_pm2(
        stats, group.get("land_type"), cfg["cena_pasma"]["min_srovnatelnych"])

    # Duvody z pozitivnich zjisteni.
    if ev.get("suspicious_price"):
        omezeni.append("cena za m² je nezvykle nízká vůči srovnatelným pozemkům – "
                       "údaj v inzerátu je pravděpodobně neúplný (např. cena za m², "
                       "spoluvlastnický podíl nebo dražba); nebrat jako výhodu")
    else:
        r = _pm2_vs_median(group.get("price_per_m2"), comp_med, zaklad)
        if r:
            duvody.append(r)

    band = None
    area = group.get("area")
    if area is not None:
        p = cfg["plocha"]
        if area < p["maly_do"]:
            band = f"plocha {int(area)} m² odpovídá menšímu pozemku"
        elif area >= p["velky_od"]:
            band = f"plocha {int(area)} m² odpovídá velkému pozemku"
        else:
            band = f"plocha {int(area)} m² je vhodná pro běžný stavební pozemek"
        duvody.append(band)

    lt = group.get("land_type")
    if lt and lt != TYP_NEUVEDENO:
        duvody.append(f"typ dle inzerátu: {TYP_POPIS.get(lt, lt)}")

    utils = group.get("utilities") or {}
    present = [name for name, key in [("elektřinu", "elektrina"), ("vodu", "voda"),
                                      ("kanalizaci", "kanalizace"), ("plyn", "plyn")]
               if utils.get(key) is True]
    if present:
        duvody.append("inzerát uvádí dostupnost: " + ", ".join(present))
    if utils.get("pristup"):
        duvody.append(f"přístupová cesta: {utils['pristup']}")

    # Omezeni z chybejicich udaju - explicitne, bez domysleni.
    for key, popis in [("elektrina", "elektřina"), ("voda", "voda"),
                       ("kanalizace", "kanalizace"), ("plyn", "plyn")]:
        if utils.get(key) is None:
            omezeni.append(f"{popis}: v inzerátu neuvedeno")
    if lt in (None, TYP_NEUVEDENO):
        omezeni.append("typ pozemku nebyl spolehlivě určen ze zdroje")
    else:
        omezeni.append("typ pozemku převzat z inzerátu, není právně ověřen")
    if not group.get("price") or not group.get("area"):
        omezeni.append("chybí cena nebo výměra – cenu za m² nelze spolehlivě určit")

    pocet = stats.get("pocet_aktivnich", 0)
    omezeni.append(f"v Chrudimi je k dispozici {pocet} srovnatelných nabídek")

    return {
        "zarazeni": ev["label"],
        "valuation": ev["valuation"],
        "duvody": duvody,
        "omezeni": omezeni,
        "jistota": ev["jistota"],
    }
