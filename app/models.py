"""Datove modely a normalizace typu pozemku.

RawListing = jednotny tvar nabidky z libovolneho zdroje. Kazdy scraper vraci
seznam RawListing; ty se dale ukladaji do DB a zpracovavaji.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import re

# Kategorie nemovitosti a typ nabidky.
KAT_POZEMKY = "pozemky"
KAT_BYTY = "byty"
KAT_DOMY = "domy"
NAB_PRODEJ = "prodej"
NAB_PRONAJEM = "pronajem"

KAT_POPIS = {KAT_POZEMKY: "Pozemky", KAT_BYTY: "Byty", KAT_DOMY: "Domy"}
NAB_POPIS = {NAB_PRODEJ: "prodej", NAB_PRONAJEM: "pronájem"}


def normalize_disposition(*texts: Optional[str]) -> Optional[str]:
    """Odvodi dispozici bytu/domu (3+1, 2+kk...) z nazvu inzeratu."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    m = re.search(r"(\d)\s*\+\s*(kk|\d)", blob, re.I)
    if m:
        return f"{m.group(1)}+{m.group(2).lower()}"
    low = blob.lower()
    if "garson" in low:
        return "garsoniéra"
    if "atyp" in low:
        return "atypický"
    return None


def disposition_from_bezrealitky(code: Optional[str]) -> Optional[str]:
    """Prevede Bezrealitky enum (DISP_3_1, DISP_2_KK, GARSONIERA) na 3+1, 2+kk..."""
    if not code:
        return None
    if code == "GARSONIERA":
        return "garsoniéra"
    m = re.match(r"DISP_(\d+)_(KK|\d+)", code)
    if m:
        return f"{m.group(1)}+{'kk' if m.group(2) == 'KK' else m.group(2)}"
    if "ATYP" in code or "OSTAT" in code:
        return "atypický"
    return None


def penb_from_sreality(cb: Optional[dict]) -> Optional[str]:
    """Z energy_efficiency_rating_cb ({name:'G - ...', value:7}) vytahne tridu A-G."""
    if not cb:
        return None
    name = cb.get("name") or ""
    if name and name[0] in "ABCDEFG":
        return name[0]
    val = cb.get("value")
    if isinstance(val, int) and 1 <= val <= 7:
        return "ABCDEFG"[val - 1]
    return None


def extract_penb(text):
    """Najde PENB tridu (A-G) v popisu. Dvoukrokove: najde frazi (energeticka
    trida / PENB) a pak nasledujici VELKE pismeno A-G (aby 'bytu je E' fungovalo)."""
    if not text:
        return None
    pm = re.search(r"energetick\w+\s+(?:tříd\w+|náročnost)|PENB|energetická\s+třída|"
                   r"průkaz\s+energetick\w+", text, re.I)
    if pm:
        seg = text[pm.end():pm.end() + 25]
        m = re.search(r"(?<![A-Za-z])([A-G])(?![A-Za-z])", seg)  # velke pismeno A-G
        if m:
            return m.group(1)
    m = re.search(r"PENB[:\s]*([A-G])", text)
    return m.group(1) if m else None

# Mesicni naklady na bydleni (energie a sluzby navic k najmu).
_COST_AMOUNT = r"\d[\d. \s]{0,8}\d|\d"
_COST_INCLUDE = (r"(?:energi\w*|elektřin\w*|elektrik\w*|plyn\w*|topen\w*|tepl\w*|vod\w*|vodné|"
                 r"stočné|služb\w*|provozní\s+náklad\w*|náklad\w+\s+na\s+bydlení|zál\w*|"
                 r"poplat\w*|inkas\w*|internet\w*|úklid\w*|odpad\w*|fond\s+oprav)")
_COST_EXCLUDE = (r"(?:jistot\w*|jistin\w*|kauc\w*|depozit\w*|vratn\w*|proviz\w*|"
                 r"podnájem\w*|podnájm\w*|nájem\w*|nájm\w*|nájemn\w*|kupní|"
                 r"při\s+podpisu|jednorázov\w*|záloh\w*\s+ve\s+výši)")
_COST_KW = re.compile("(" + _COST_INCLUDE + ")|(" + _COST_EXCLUDE + ")", re.I)
_COST_TRANSFER = re.compile(
    r"(?:elektřin\w*|elektrik\w*|energie|plyn\w*)[^.\d]{0,25}"
    r"(?:na\s+nájemce|přepsán\w*|převád\w*|na\s+vlastní\s+jméno|dle\s+skutečné\s+spotřeby)", re.I)


def _cost_int(s):
    d = re.sub(r"[^\d]", "", s)
    return int(d) if d else None


def extract_costs(text):
    """Z popisu pronajmu vytahne (mesicni_naklady_kc, rozpis_text, penb).
    Mesicni naklady = energie a sluzby navic k najmu. Vylucuje jednorazove polozky
    (jistota, kauce, provize, poplatek za podnajem). Nic nedomysli - jen cte text."""
    if not text:
        return None, None, None
    t = text.replace(" ", " ")
    penb = extract_penb(t)

    items = []
    seen_labels = set()
    for m in re.finditer(r"(" + _COST_AMOUNT + r")\s*,?-?\s*Kč", t):
        amt = _cost_int(m.group(1))
        if amt is None or not (100 <= amt <= 60000):
            continue
        # Hledej klicove slovo na OBOU stranach castky, ale jen v ramci vety
        # (hranice = tecka + mezera; tisicovy oddelovac "13.900" nerozdeli).
        left = re.split(r"\.\s", t[max(0, m.start() - 45):m.start()])[-1]
        right = re.split(r"\.\s", t[m.end():m.end() + 45])[0]
        best = None   # (vzdalenost, je_vylouceny, popisek)
        for km in _COST_KW.finditer(left):
            dist = len(left) - km.end()
            if best is None or dist < best[0]:
                best = (dist, bool(km.group(2)), (km.group(1) or "").strip())
        # Klic ZA castkou plati jen kdyz HNED navazuje (mezera/pomlcka/zavorka),
        # ne pres "+", cislo nebo carku - ty uz patri k jine polozce (napr. nasledujici najem).
        rm = _COST_KW.search(right)
        if rm and re.fullmatch(r"[\s\-(]{0,4}", right[:rm.start()]):
            dist = rm.start()
            if best is None or dist < best[0]:
                label = re.split(r"[)\,;]", right[rm.start():])[0].strip()
                best = (dist, bool(rm.group(2)), label)
        if best is None or best[1]:   # zadny klic, nebo nejblizsi je vyloucny
            continue
        kw = best[2]
        # strop na jednu polozku: bezne mesicni naklady nejsou pres ~15 000,
        # pokud nejde o souhrnnou polozku (naklady/provozni/sluzby/inkaso)
        if amt > 15000 and not re.search(r"náklad|provozní|služb|inkas", kw, re.I):
            continue
        if kw.lower() not in seen_labels:
            items.append((kw, amt))
            seen_labels.add(kw.lower())

    tm = _COST_TRANSFER.search(t)
    transfer = re.sub(r"\s+", " ", tm.group(0)).strip()[:55] if tm else None

    total = None
    for kw, amt in items:
        if re.search(r"náklad|provozní|zál|služb|inkas", kw, re.I):
            total = amt
            break
    if total is None and items:
        total = sum(a for _, a in items)

    # note = ujednani (napr. "elektřina a plyn na nájemce"); rozpis polozek se nezobrazuje.
    # Vraci se i kdyz je castka znama - aby bylo videt, ze cast energii se plati zvlast.
    return (float(total) if total else None), transfer, penb


def extract_energy_cost(text):
    """Zpetne kompatibilni obal - vraci (mesicni_naklady, rozpis)."""
    total, note, _ = extract_costs(text)
    return total, note

# Normalizovane typy pozemku (interni klice). Slovni popis viz TYP_POPIS.
TYP_STAVEBNI = "stavebni"
TYP_BYDLENI = "bydleni"
TYP_KOMERCNI = "komercni"
TYP_ZAHRADA = "zahrada"
TYP_ZEMEDELSKY = "zemedelsky"
TYP_OSTATNI = "ostatni"
TYP_NEUVEDENO = "neuvedeno"

TYP_POPIS = {
    TYP_STAVEBNI: "stavební pozemek",
    TYP_BYDLENI: "pozemek pro bydlení",
    TYP_KOMERCNI: "komerční pozemek",
    TYP_ZAHRADA: "zahrada",
    TYP_ZEMEDELSKY: "zemědělský pozemek",
    TYP_OSTATNI: "ostatní pozemek",
    TYP_NEUVEDENO: "neuvedeno / nejasné",
}


# Hrube tridy pro srovnani "podobnych" nabidek (bod 8 zadani): stavebni/komercni
# maji jinou cenovou hladinu nez zemedelska/ostatni puda.
TRIDA_STAVEBNI = "stavebni"      # stavebni, bydleni, komercni
TRIDA_ZEMEDELSKA = "zemedelska"  # zahrada, zemedelsky, ostatni
TRIDA_NEZNAMA = "neznama"

_TRIDA_MAP = {
    TYP_STAVEBNI: TRIDA_STAVEBNI, TYP_BYDLENI: TRIDA_STAVEBNI, TYP_KOMERCNI: TRIDA_STAVEBNI,
    TYP_ZAHRADA: TRIDA_ZEMEDELSKA, TYP_ZEMEDELSKY: TRIDA_ZEMEDELSKA, TYP_OSTATNI: TRIDA_ZEMEDELSKA,
}


def land_class(land_type: Optional[str]) -> str:
    return _TRIDA_MAP.get(land_type or "", TRIDA_NEZNAMA)


def normalize_land_type(*texts: Optional[str]) -> str:
    """Odvodi normalizovany typ pozemku z libovolnych textovych indicii
    (nazev inzeratu, kategorie ze zdroje, popis). Kdyz nelze urcit -> neuvedeno.
    """
    blob = " ".join(t.lower() for t in texts if t)
    if not blob.strip():
        return TYP_NEUVEDENO
    # Poradi zamerne - specifictejsi drive.
    if "staveb" in blob:
        return TYP_STAVEBNI
    if "komer" in blob or "podnik" in blob or "sklad" in blob or "výrob" in blob or "vyrob" in blob:
        return TYP_KOMERCNI
    if "bydlen" in blob or "bydlení" in blob:
        return TYP_BYDLENI
    if "zahrad" in blob:
        return TYP_ZAHRADA
    if any(k in blob for k in ("pole", "orná", "orna", "louk", "zeměděl", "zemedel", "pastvin", "trvalý travní")):
        return TYP_ZEMEDELSKY
    if any(k in blob for k in ("les", "rybník", "rybnik", "vinic", "sad")):
        return TYP_OSTATNI
    if "ostatní" in blob or "ostatni" in blob:
        return TYP_OSTATNI
    # Obecne "pozemek" bez blizsiho urceni -> nejasny.
    return TYP_NEUVEDENO


@dataclass
class RawListing:
    """Jednotny tvar nabidky z jednoho zdroje pred ulozenim."""
    source: str                       # "sreality" | "idnes" | "bezrealitky"
    source_id: str                    # jednoznacne ID v ramci zdroje
    url: str
    title: str
    category: str = KAT_POZEMKY       # pozemky | byty | domy
    offer_type: str = NAB_PRODEJ      # prodej | pronajem
    price: Optional[float] = None            # prodej: celkova cena; pronajem: najem/mesic
    area: Optional[float] = None             # plocha v m2
    # Byty/domy:
    dispozice: Optional[str] = None          # 3+1, 2+kk...
    penb: Optional[str] = None               # energeticka trida A-G
    energy_cost: Optional[float] = None       # cena energii/poplatku (pronajem), Kc/mesic
    energy_note: Optional[str] = None         # kratky popis ujednani o energiich/sluzbach
    energy_ai: Optional[bool] = None          # True = udaj dohledala AI z popisu
    date_added: Optional[str] = None          # realne datum vlozeni ze zdroje (ISO), kdyz ho zdroj uvadi
    city: Optional[str] = None
    city_part: Optional[str] = None
    locality: Optional[str] = None           # ulice / lokalita, kdyz je
    land_type: str = TYP_NEUVEDENO
    land_type_raw: Optional[str] = None      # puvodni oznaceni ze zdroje
    description: Optional[str] = None
    image_url: Optional[str] = None            # hlavni nahledova fotka (thumbnail)
    image_urls: list = field(default_factory=list)  # nekolik fotek z galerie (pro AI posouzeni vzhledu)
    lat: Optional[float] = None
    lon: Optional[float] = None
    price_old: Optional[float] = None        # drivejsi cena (pro zlevneni), kdyz zdroj uvadi
    # Site: True = uvedeno jako dostupne, None = neuvedeno (NIKDY nepredpokladat False).
    util_electricity: Optional[bool] = None
    util_water: Optional[bool] = None
    util_sewage: Optional[bool] = None       # kanalizace
    util_gas: Optional[bool] = None
    access_road: Optional[str] = None        # informace o pristupove ceste
    raw: dict = field(default_factory=dict)  # cely puvodni zaznam pro ladeni

    @property
    def price_per_m2(self) -> Optional[float]:
        if self.price and self.area and self.area > 0:
            return self.price / self.area
        return None

    def raw_json(self) -> str:
        try:
            return json.dumps(self.raw, ensure_ascii=False, default=str)
        except Exception:
            return "{}"


def utilities_dict(l: "RawListing") -> dict:
    return {
        "elektrina": l.util_electricity,
        "voda": l.util_water,
        "kanalizace": l.util_sewage,
        "plyn": l.util_gas,
        "pristup": l.access_road,
    }
