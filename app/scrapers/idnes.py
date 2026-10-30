"""Scraper Reality.iDNES (HTML) - pozemky, byty i domy, prodej i pronajem.

Nacita stranku vyhledavani pro mesto Chrudim a parsuje karty nabidek. Udaje na
urovni karty: nazev (typ/dispozice + plocha), cena (u pronajmu Kc/mesic), lokalita,
obrazek, odkaz. PENB a cena energii nejsou na karte -> zustanou 'neuvedeno'.
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from .base import make_session, polite_sleep, clean_text, ScrapeResult
from ..config import get_config
from ..models import (RawListing, normalize_land_type, normalize_disposition,
                      extract_costs, extract_penb,
                      KAT_POZEMKY, KAT_BYTY, KAT_DOMY, NAB_PRODEJ, NAB_PRONAJEM)

BASE = "https://reality.idnes.cz/s/{offer}/{cat}/{slug}/"
CAT_SLUG = {KAT_POZEMKY: "pozemky", KAT_BYTY: "byty", KAT_DOMY: "domy"}
OFFER_SLUG = {NAB_PRODEJ: "prodej", NAB_PRONAJEM: "pronajem"}

# Ceske cislo (mezera = oddelovac tisicu, skupiny po 3).
_CZ_NUM = r"\d{1,3}(?:\s\d{3})*"


def _num(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(_CZ_NUM, text.replace(" ", " "))
    if not m:
        return None
    try:
        return float(m.group(0).replace(" ", ""))
    except ValueError:
        return None


def _area_from_title(title: str) -> Optional[float]:
    """Plocha = cislo tesne pred 'm2'. Diky skupinam po 3 nezameni dispozici (3+1)
    za soucast vymery."""
    m = re.search(r"(" + _CZ_NUM + r")\s*m", title.replace(" ", " "))
    return float(m.group(1).replace(" ", "")) if m else None


def _detail_id(url: str) -> str:
    parts = [p for p in url.split("/") if p]
    return parts[-1] if parts else url


def _detail_images(soup, n: int = 10) -> list:
    """Vytahne z detailu iDNES nekolik URL fotek z galerie (pro AI posouzeni vzhledu).
    Bere jen fotky z iDNES CDN (1gr.cz), vylouci loga/ikony."""
    urls: list = []
    og = soup.select_one('meta[property="og:image"]')
    if og and og.get("content"):
        urls.append(og["content"])
    for img in soup.select("img"):
        u = img.get("data-src") or img.get("src")
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        low = u.lower()
        if "1gr.cz" not in low:
            continue
        if any(k in low for k in ("logo", "icon", "sprite", "avatar", "placeholder", "blank")):
            continue
        if u not in urls:
            urls.append(u)
        if len(urls) >= n:
            break
    return urls[:n]


def _fetch_detail(session, url: str):
    """Nacte detail inzeratu iDNES a vrati (text popisu+parametru, seznam fotek galerie).
    Text kvuli nakladum a energeticke tride; fotky pro AI posouzeni vzhledu."""
    try:
        r = session.get(url, timeout=session.request_timeout)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        parts = []
        desc = soup.select_one(".b-desc")
        if desc:
            parts.append(desc.get_text(" ", strip=True))
        for p in soup.select(".b-definition-columns, dl.b-definition"):
            parts.append(p.get_text(" ", strip=True))
        return clean_text(". ".join(parts)) or None, _detail_images(soup)
    except Exception:
        return None, []


def scrape_idnes(category: str = KAT_POZEMKY, offer_type: str = NAB_PRODEJ) -> ScrapeResult:
    cfg = get_config()
    slug = cfg["lokalita"]["idnes_slug"]
    max_stranek = cfg["sit"]["max_stranek"]
    session = make_session()
    listings: list[RawListing] = []
    seen: set[str] = set()

    try:
        page = 1
        while page <= max_stranek:
            url = BASE.format(offer=OFFER_SLUG[offer_type], cat=CAT_SLUG[category], slug=slug)
            params = {"page": page} if page > 1 else {}
            r = session.get(url, params=params, timeout=session.request_timeout)
            if r.status_code == 404:
                break
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".c-products__item")
            if not cards:
                break

            new_on_page = 0
            for c in cards:
                a = c.select_one("a.c-products__link") or c.select_one("a")
                if not a or not a.get("href"):
                    continue
                href = a["href"]
                did = _detail_id(href)
                if did in seen:
                    continue
                seen.add(did)
                new_on_page += 1

                title = clean_text(
                    (c.select_one(".c-products__title") or a).get_text(" ", strip=True)
                ) or "Nemovitost Chrudim"
                info = clean_text(
                    (c.select_one(".c-products__info").get_text(" ", strip=True))
                    if c.select_one(".c-products__info") else None
                )
                price_el = c.select_one(".c-products__price")
                price_txt = price_el.get_text(" ", strip=True) if price_el else ""
                price = None
                m_total = re.search(r"(" + _CZ_NUM + r")\s*Kč", price_txt.replace(" ", " "))
                if m_total:
                    price = float(m_total.group(1).replace(" ", ""))
                # u pozemku obcas jen "Kc/m2" - nebrat jako celkovou cenu
                if category == KAT_POZEMKY:
                    m_pm2 = re.search(_CZ_NUM + r"\s*Kč\s*/\s*m", price_txt)
                    if m_pm2 and price and abs(price - _num(m_pm2.group(0))) < 1:
                        price = None

                img = c.select_one("img")
                img_url = (img.get("data-src") or img.get("src")) if img else None

                # jen mesto Chrudim (iDNES stranka mesta muze mit i okoli)
                if info and "chrudim" not in info.lower():
                    continue

                kwargs = dict(
                    source="idnes", source_id=did, category=category, offer_type=offer_type,
                    url=href, title=title, price=price,
                    area=_area_from_title(title),
                    city="Chrudim", city_part=None, locality=info,
                    image_url=img_url, image_urls=([img_url] if img_url else []),
                    raw={"info": info, "price_txt": price_txt},
                )
                if category == KAT_POZEMKY:
                    kwargs.update(land_type=normalize_land_type(title), land_type_raw=title)
                else:
                    # Naklady, energeticka trida i fotky galerie jsou az v detailu -> stahnout.
                    polite_sleep()
                    detail, det_imgs = _fetch_detail(session, href)
                    penb = extract_penb(detail) if detail else None
                    energy_cost = energy_note = None
                    if offer_type == NAB_PRONAJEM and detail:
                        energy_cost, energy_note, penb2 = extract_costs(detail)
                        penb = penb or penb2
                    if det_imgs:
                        combined = ([img_url] if img_url else []) + [u for u in det_imgs if u != img_url]
                        kwargs["image_urls"] = combined[:10]
                    kwargs.update(dispozice=normalize_disposition(title), penb=penb,
                                  energy_cost=energy_cost, energy_note=energy_note,
                                  description=detail)
                listings.append(RawListing(**kwargs))
            if new_on_page == 0:
                break
            page += 1
            polite_sleep()
    except Exception as e:
        return ScrapeResult([], ok=False, message=f"Chyba pri nacitani iDNES: {e}")

    return ScrapeResult(listings, ok=True, message=f"OK, {len(listings)} nabidek v Chrudimi")
