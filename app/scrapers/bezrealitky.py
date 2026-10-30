"""Scraper Bezrealitky (neoficialni GraphQL) - pozemky, byty i domy.

Pouziva pole 'listAdverts' s browser hlavickami (jinak 403). Lokalita = OSM
relation id mesta Chrudim. Pozemek: plocha 'surfaceLand'; byt/dum: 'surface',
'disposition', 'charges' (poplatky) a 'penb' (energeticka trida).
"""
from __future__ import annotations

from typing import Optional

from .base import make_session, polite_sleep, clean_text, ScrapeResult
from ..config import get_config
from ..models import (RawListing, normalize_land_type, disposition_from_bezrealitky,
                      extract_costs, extract_penb, KAT_POZEMKY, KAT_BYTY, KAT_DOMY,
                      NAB_PRODEJ, NAB_PRONAJEM)

GRAPHQL = "https://api.bezrealitky.cz/graphql/"
DETAIL_BASE = "https://www.bezrealitky.cz/nemovitosti-byty-domy/"

ESTATE = {KAT_POZEMKY: "POZEMEK", KAT_BYTY: "BYT", KAT_DOMY: "DUM"}
OFFER = {NAB_PRODEJ: "PRODEJ", NAB_PRONAJEM: "PRONAJEM"}
KAT_TITLE = {KAT_POZEMKY: "pozemku", KAT_BYTY: "bytu", KAT_DOMY: "domu"}

QUERY = """
query SniperList($e:[EstateType],$o:[OfferType],$r:[ID],$limit:Int,$offset:Int){
  listAdverts(estateType:$e, offerType:$o, regionOsmIds:$r,
              limit:$limit, offset:$offset, order:TIMEORDER_DESC, includeImports:true){
    totalCount
    list{
      id uri price currency surface surfaceLand disposition charges penb
      estateType offerType address(locale:CS) description gps{lat lng}
      mainImage{ url(filter:RECORD_MAIN) }
      publicImages{ url(filter:RECORD_MAIN) }
    }
  }
}
"""


def _gallery(a: dict, n: int = 10) -> list:
    """Nekolik fotek z galerie (mainImage + publicImages), pro AI posouzeni vzhledu."""
    urls: list = []
    main = (a.get("mainImage") or {}).get("url")
    if main:
        urls.append(main)
    for x in (a.get("publicImages") or []):
        u = x.get("url") if isinstance(x, dict) else None
        if u and u not in urls:
            urls.append(u)
        if len(urls) >= n:
            break
    return urls[:n]


def _headers() -> dict:
    ua = get_config()["sit"]["user_agent"]
    return {
        "User-Agent": ua, "Content-Type": "application/json",
        "Accept": "application/json", "Accept-Language": "cs",
        "Origin": "https://www.bezrealitky.cz", "Referer": "https://www.bezrealitky.cz/",
    }


def scrape_bezrealitky(category: str = KAT_POZEMKY, offer_type: str = NAB_PRODEJ) -> ScrapeResult:
    cfg = get_config()
    osm_id = cfg["lokalita"]["bezrealitky_osm_id"]
    session = make_session()
    session.headers.update(_headers())

    listings: list[RawListing] = []
    try:
        offset, limit, total = 0, 50, None
        while total is None or offset < total:
            payload = {"query": QUERY, "variables": {
                "e": [ESTATE[category]], "o": [OFFER[offer_type]], "r": [osm_id],
                "limit": limit, "offset": offset,
            }}
            r = session.post(GRAPHQL, json=payload, timeout=session.request_timeout)
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):
                return ScrapeResult([], ok=False, message="GraphQL chyba: " + str(data["errors"][:1]))
            block = (data.get("data") or {}).get("listAdverts") or {}
            total = block.get("totalCount", 0)
            items = block.get("list", []) or []
            if not items:
                break
            for a in items:
                if category == KAT_POZEMKY:
                    area = a.get("surfaceLand") or a.get("surface")
                else:
                    area = a.get("surface") or a.get("surfaceLand")
                area = float(area) if area else None
                price = float(a["price"]) if a.get("price") else None
                gps = a.get("gps") or {}
                addr = clean_text(a.get("address"))
                desc = clean_text(a.get("description"))
                dispozice = disposition_from_bezrealitky(a.get("disposition"))

                nazev = KAT_TITLE[category]
                title = f"{'Pronájem' if offer_type == NAB_PRONAJEM else 'Prodej'} {nazev}"
                if dispozice:
                    title += f" {dispozice}"
                if area:
                    title += f" {int(area)} m²"
                if addr:
                    title += f" – {addr.split(',')[0]}"

                kwargs = dict(
                    source="bezrealitky", source_id=str(a["id"]), category=category,
                    offer_type=offer_type, url=DETAIL_BASE + str(a.get("uri") or a["id"]),
                    title=title, price=price, area=area, city="Chrudim", city_part=None,
                    locality=addr, description=desc, image_url=(a.get("mainImage") or {}).get("url"),
                    image_urls=_gallery(a),
                    lat=gps.get("lat"), lon=gps.get("lng"), raw={"id": a.get("id"), "uri": a.get("uri")},
                )
                if category == KAT_POZEMKY:
                    kwargs.update(land_type=normalize_land_type(title, desc, addr),
                                  land_type_raw=a.get("estateType"))
                else:
                    penb = (a.get("penb") or None)
                    penb = penb if (penb and penb in "ABCDEFG") else None
                    energy_cost = energy_note = None
                    if offer_type == NAB_PRONAJEM:
                        ec, energy_note, penb2 = extract_costs(desc)
                        ch = a.get("charges")
                        # castka: prednostne strukturovane 'charges', jinak z popisu
                        energy_cost = float(ch) if (ch and ch > 0) else ec
                        penb = penb or penb2
                    elif not penb:
                        penb = extract_penb(desc)
                    kwargs.update(dispozice=dispozice, penb=penb,
                                  energy_cost=energy_cost, energy_note=energy_note)
                listings.append(RawListing(**kwargs))
            offset += limit
            if offset >= (total or 0):
                break
            polite_sleep()
    except Exception as e:
        return ScrapeResult([], ok=False, message=f"Chyba pri nacitani Bezrealitky: {e}")

    return ScrapeResult(listings, ok=True, message=f"OK, {len(listings)} nabidek v Chrudimi")
