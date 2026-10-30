"""Oficialni ceny nemovitosti z Ceskeho statistickeho uradu (Verejna databaze,
dataset CEN14A) - vyvoj REALIZOVANYCH cen Kc/m2 pro okres Chrudim, rocni rada
zvlast pro byty a rodinne domy.

Data se stahnou jednou a ulozi do cache (ČSÚ je aktualizuje ~1x rocne). Modul
nikdy nevyhazuje vyjimku - pri selhani vrati posledni cache (nebo None).
Zdroj je verejny a citovatelny (ČSÚ VDB).
"""
from __future__ import annotations

import json
import re
import datetime
import urllib.request

from bs4 import BeautifulSoup

from .config import ROOT

CSU_URL = ("https://vdb.czso.cz/vdbvo2/faces/cs/index.jsf?page=vystup-objekt&z=T&f=TABULKA"
           "&skupId=5073&katalog=31782&pvo=CEN14A&evo=v514_!_CEN14ACRKRAJOKR_1")
OKRES = "Chrudim"
CACHE = ROOT / "data" / "csu_ceny.json"
MAX_AGE_DNI = 30
PRVNI_ROK = 2019
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126 Safari/537.36", "Accept": "text/html"}


def _parse(html: str) -> dict | None:
    """Z HTML tabulky vytahne pro okres Chrudim rady domy/byty jako [[rok, kc], ...].

    Radek okresu ma 24 hodnot = 12 rodinne domy (7 let 2019-2025 + 5 klouzavych)
    + 12 byty. Bereme jen prvnich 7 (rocni hodnoty) z kazde skupiny.
    """
    soup = BeautifulSoup(html, "html.parser")
    tbl = next((t for t in soup.find_all("table") if OKRES in t.get_text()), None)
    if not tbl:
        return None
    for r in tbl.find_all("tr"):
        cs = [c.get_text(" ", strip=True) for c in r.find_all(["th", "td"])]
        if cs and cs[0].strip() == OKRES:
            vals = [int(re.sub(r"[^\d]", "", c) or 0) or None for c in cs[1:]]
            years = list(range(PRVNI_ROK, PRVNI_ROK + 7))
            domy = [[y, v] for y, v in zip(years, vals[0:7]) if v]
            byty = [[y, v] for y, v in zip(years, vals[12:19]) if v]
            return {"domy": domy, "byty": byty}
    return None


def _fetch_fresh() -> dict | None:
    req = urllib.request.Request(CSU_URL, headers=_UA)
    try:
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:      # stranka nekdy vraci 401 s plnym telem
        html = e.read().decode("utf-8", "replace")
    return _parse(html)


def get_data(force: bool = False) -> dict | None:
    """Vraci {'domy':[[rok,kc]..], 'byty':[[..]], 'fetched':ISO, 'source':...} z cache;
    cerstve stahne, kdyz cache chybi nebo je starsi nez MAX_AGE_DNI."""
    cached = None
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        cached = None

    stale = force or not cached
    if cached and not force:
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(cached["fetched"])).days
            stale = age > MAX_AGE_DNI
        except Exception:
            stale = True

    if stale:
        parsed = None
        try:
            parsed = _fetch_fresh()
        except Exception:
            parsed = None
        if parsed and (parsed.get("byty") or parsed.get("domy")):
            cached = {**parsed, "fetched": datetime.date.today().isoformat(),
                      "source": "ČSÚ – Veřejná databáze, dataset CEN14A (okres Chrudim)",
                      "url": CSU_URL}
            try:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
    return cached


def _fmt(v) -> str:
    return f"{int(v):,}".replace(",", " ")


def chart_svg(points: list, color: str) -> str:
    """Carovy SVG graf Kc/m2 v case s interaktivnimi hover body. points = [[rok, kc], ...]."""
    if not points or len(points) < 2:
        return ""
    W, H, L, R, T, B = 720, 210, 84, 22, 22, 30   # sirsi levy okraj kvuli popiskum
    ys = [p[1] for p in points]
    ymin, ymax = min(ys), max(ys)
    if ymin == ymax:
        ymin -= 1; ymax += 1
    pad = (ymax - ymin) * 0.15
    lo, hi = ymin - pad, ymax + pad
    n = len(points)
    band = (W - L - R) / (n - 1)

    def X(i):
        return L + (W - L - R) * i / (n - 1)

    def Y(v):
        return T + (H - T - B) * (1 - (v - lo) / (hi - lo))

    poly = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (yr, v) in enumerate(points))
    dots, zones = "", ""
    for i, (yr, v) in enumerate(points):
        cx, cy = X(i), Y(v)
        dots += f'<circle class="csu-dot" data-i="{i}" cx="{cx:.1f}" cy="{cy:.1f}" r="3.6" fill="{color}"/>'
        zx = max(0, cx - band / 2)
        zones += (f'<rect class="csu-hz" data-i="{i}" data-year="{yr}" data-val="{_fmt(v)}" '
                  f'x="{zx:.1f}" y="{T}" width="{band:.1f}" height="{H-T-B:.1f}" fill="transparent"/>')
    guide = f'<line class="csu-guide" x1="0" y1="{T}" x2="0" y2="{H-B}" stroke="{color}" stroke-dasharray="3 3" opacity="0"/>'
    # osa Y: jen cislo (jednotka je v nadpisu/zdroji), aby se popisky vesly
    ylab = (f'<text x="{L-10}" y="{Y(ymax)+4:.1f}" text-anchor="end" class="cy">{_fmt(ymax)}</text>'
            f'<text x="{L-10}" y="{Y(ymin)+4:.1f}" text-anchor="end" class="cy">{_fmt(ymin)}</text>'
            f'<text x="{L-10}" y="{T+9:.1f}" text-anchor="end" class="cyunit">Kč/m²</text>')
    grid = (f'<line x1="{L}" y1="{Y(ymax):.1f}" x2="{W-R}" y2="{Y(ymax):.1f}" class="cg"/>'
            f'<line x1="{L}" y1="{Y(ymin):.1f}" x2="{W-R}" y2="{Y(ymin):.1f}" class="cg"/>')
    xlab = "".join(f'<text x="{X(i):.1f}" y="{H-9}" text-anchor="middle" class="cx">{yr}</text>'
                   for i, (yr, v) in enumerate(points))
    lyr, lv = points[-1]
    last = (f'<text x="{X(n-1):.1f}" y="{Y(lv)-10:.1f}" text-anchor="end" '
            f'class="clast" fill="{color}">{_fmt(lv)} Kč/m²</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="csu-chart" preserveAspectRatio="xMidYMid meet" '
            f'xmlns="http://www.w3.org/2000/svg">{grid}{guide}'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/>{dots}{ylab}{xlab}{last}{zones}</svg>')


def category_chart(category: str) -> dict | None:
    """Vrati {'svg':..., 'source':..., 'fetched':..., 'kind':'byty'|'domy'} pro danou
    kategorii, nebo None kdyz data nejsou. Pozemky nemaji ČSÚ radu."""
    data = get_data()
    if not data:
        return None
    kind = "byty" if category == "byty" else ("domy" if category == "domy" else None)
    if not kind:
        return None
    points = data.get(kind) or []
    color = "#1f6feb" if kind == "byty" else "#b8860b"
    svg = chart_svg(points, color)
    if not svg:
        return None
    return {"svg": svg, "source": data.get("source"), "fetched": data.get("fetched"),
            "url": data.get("url"), "kind": kind}
