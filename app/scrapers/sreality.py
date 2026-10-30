"""Scraper Sreality (neoficialni interni JSON API) - pozemky, byty i domy.

Postup:
  1) GET /api/v1/estates/search (category_main_cb, category_type_cb, locality_district_id)
     - strankovane pres offset/limit (POZOR: parametr 'page' API ignoruje!).
  2) Filtr na obec Chrudim (municipality_id, vc. casti Chrudim I-IV, Medlesice...).
  3) Pro kazdou nabidku GET /api/v1/estates/<hash_id> = detail.

Celkova cena se bere z 'price_summary_czk' (u prodeje za nemovitost, u pronajmu za mesic) -
nikdy z 'price_czk', ktere muze byt "za m2".
"""
from __future__ import annotations

from typing import Optional

from .base import make_session, polite_sleep, clean_text, ScrapeResult
from ..config import get_config
from ..models import (RawListing, normalize_land_type, normalize_disposition,
                      penb_from_sreality, extract_costs, extract_penb, TYP_NEUVEDENO,
                      KAT_POZEMKY, KAT_BYTY, KAT_DOMY, NAB_PRODEJ, NAB_PRONAJEM)

API_SEARCH = "https://www.sreality.cz/api/v1/estates/search"
API_DETAIL = "https://www.sreality.cz/api/v1/estates/{hash_id}"

CAT_MAIN = {KAT_BYTY: 1, KAT_DOMY: 2, KAT_POZEMKY: 3}
CAT_TYPE = {NAB_PRODEJ: 1, NAB_PRONAJEM: 2}
KIND_SEO = {KAT_POZEMKY: "pozemek", KAT_BYTY: "byt", KAT_DOMY: "dum"}

SUB_MAP = {
    "Bydlení": "bydleni", "Komerční": "komercni", "Pole": "zemedelsky",
    "Louky": "zemedelsky", "Lesy": "ostatni", "Rybníky": "ostatni",
    "Sady/vinice": "ostatni", "Zahrady": "zahrada", "Ostatní pozemky": "ostatni",
}


# Bez transformacniho suffixu vraci sdn.cz u obrazku 401. sreality.cz servíruje
# nahledy pres tento povoleny preset (resize + doostreni + jpg).
_IMG_SUFFIX = "?fl=res,1200,1200,1|shr,,20|jpg,80"


def _img_url(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    full = ("https:" + u) if u.startswith("//") else u
    if "sdn.cz" in full and "?" not in full:
        full += _IMG_SUFFIX
    return full


def _img_urls(item: dict, d: dict, n: int = 10) -> list:
    """Prvnich n fotek z galerie (pro AI posouzeni vzhledu). Detail (d) ma obrazky
    jako {url:...}, seznam (item) jako retezce; osetrime oboje."""
    raw = (d.get("advert_images") or item.get("advert_images") or [])
    out: list = []
    for x in raw:
        u = x.get("url") if isinstance(x, dict) else x
        full = _img_url(u)
        if full and full not in out:
            out.append(full)
        if len(out) >= n:
            break
    return out


def _detail_url(cat, offer, hash_id, loc, dispozice, sub_value) -> str:
    typ = "pronajem" if offer == NAB_PRONAJEM else "prodej"
    city = loc.get("city_seo_name") or "chrudim"
    part = loc.get("citypart_seo_name")
    locslug = f"{city}-{part}" if part else city
    if cat == KAT_POZEMKY:
        subseo = {19: "bydleni", 18: "komercni", 20: "pole", 21: "louky",
                  22: "lesy", 25: "zahrada", 26: "ostatni-pozemky"}.get(sub_value, "bydleni")
        return f"https://www.sreality.cz/detail/{typ}/pozemek/{subseo}/{locslug}/{hash_id}"
    kindseo = KIND_SEO[cat]
    dispseo = (dispozice or ("rodinny" if cat == KAT_DOMY else "byt")).replace(" ", "")
    return f"https://www.sreality.cz/detail/{typ}/{kindseo}/{dispseo}/{locslug}/{hash_id}"


def _has_util(value) -> Optional[bool]:
    if isinstance(value, list) and len(value) > 0:
        return True
    return None


def _total_price(rec: dict) -> Optional[float]:
    unit = (rec.get("price_unit_cb") or {}).get("value")
    if unit in (1, 2):  # 1=za nemovitost (prodej), 2=za mesic (pronajem)
        return rec.get("price_czk")
    return None


def _in_chrudim(locality: dict, obec_id, mesto: str) -> bool:
    if obec_id and locality.get("municipality_id") == obec_id:
        return True
    return (locality.get("city") or "").strip() == mesto


def scrape_sreality(category: str = KAT_POZEMKY, offer_type: str = NAB_PRODEJ) -> ScrapeResult:
    cfg = get_config()
    okres_id = cfg["lokalita"]["sreality_okres_id"]
    obec_id = cfg["lokalita"].get("sreality_obec_id")
    mesto = cfg["lokalita"]["sreality_mesto"]
    limit = 100
    max_offset = cfg["sit"]["max_stranek"] * limit
    session = make_session()

    town_items: dict = {}
    try:
        offset = 0
        total = None
        while offset <= max_offset:
            params = {
                "category_main_cb": CAT_MAIN[category], "category_type_cb": CAT_TYPE[offer_type],
                "locality_district_id": okres_id,
                "limit": limit, "offset": offset, "lang": "cs", "sort": "-date",
            }
            r = session.get(API_SEARCH, params=params, timeout=session.request_timeout)
            r.raise_for_status()
            data = r.json()
            total = data.get("pagination", {}).get("total", 0)
            results = [x for x in data.get("results", [])
                       if isinstance(x, dict) and "hash_id" in x and "locality" in x]
            if not results:
                break
            for x in results:
                if _in_chrudim(x["locality"], obec_id, mesto):
                    town_items[x["hash_id"]] = x
            offset += limit
            if offset >= (total or 0):
                break
            polite_sleep()
    except Exception as e:
        return ScrapeResult([], ok=False, message=f"Chyba pri nacitani seznamu: {e}")

    listings: list[RawListing] = []
    for hash_id, item in town_items.items():
        try:
            polite_sleep()
            r = session.get(API_DETAIL.format(hash_id=hash_id), timeout=session.request_timeout)
            r.raise_for_status()
            d = r.json().get("result", {})
        except Exception:
            d = {}

        loc = (d.get("locality") or item.get("locality") or {})
        title = clean_text(d.get("advert_name") or item.get("advert_name") or "Nemovitost Chrudim")
        desc = clean_text(d.get("advert_description"))
        price = (d.get("price_summary_czk") or item.get("price_summary_czk")
                 or _total_price(d) or _total_price(item))
        price = float(price) if price else None
        price_old = d.get("price_summary_old") or None
        price_old = float(price_old) if price_old and price_old > 0 else None
        sub = (d.get("category_sub_cb") or item.get("category_sub_cb") or {})

        common = dict(
            source="sreality", source_id=str(hash_id), category=category, offer_type=offer_type,
            url=_detail_url(category, offer_type, hash_id, loc, None, sub.get("value")),
            title=title, price=price, price_old=price_old,
            city=loc.get("city"), city_part=loc.get("citypart"),
            locality=clean_text(loc.get("street")) or loc.get("citypart"),
            description=desc, lat=loc.get("gps_lat"), lon=loc.get("gps_lon"),
            image_url=_img_url((item.get("advert_images") or [None])[0]
                               or ((d.get("advert_images") or [{}])[0] or {}).get("url")),
            image_urls=_img_urls(item, d),
            date_added=(d.get("since") or None),   # realne datum vlozeni ("Vloženo")
        )

        if category == KAT_POZEMKY:
            sub_name = sub.get("name")
            land_type = SUB_MAP.get(sub_name) or normalize_land_type(sub_name, title, desc)
            if land_type == TYP_NEUVEDENO and "staveb" in (title or "").lower():
                land_type = "stavebni"
            listings.append(RawListing(
                **common,
                area=float(d.get("estate_area")) if d.get("estate_area") else None,
                land_type=land_type, land_type_raw=sub_name,
                util_electricity=_has_util(d.get("electricity_set")),
                util_water=_has_util(d.get("water_set")),
                util_sewage=_has_util(d.get("gully_set")),
                util_gas=_has_util(d.get("gas_set")),
                access_road=(d.get("road_type_set")[0]["name"]
                             if isinstance(d.get("road_type_set"), list) and d.get("road_type_set") else None),
                raw={"list": item},
            ))
        else:
            area = d.get("usable_area") or d.get("floor_area") or None
            dispozice = normalize_disposition(title)
            # URL zpresnime dispozici
            common["url"] = _detail_url(category, offer_type, hash_id, loc, dispozice, None)
            penb = penb_from_sreality(d.get("energy_efficiency_rating_cb"))
            # Naklady/PENB casto v "poznamce k cene" (price_note), ne v popisu -> spoj obe.
            cost_text = ". ".join(x for x in [clean_text(d.get("price_note")), desc] if x)
            energy_cost = energy_note = None
            if offer_type == NAB_PRONAJEM:
                # mesicni naklady + rozpis + PENB z textu (regulerni extrakce).
                # co regex nenajde, doplni pozdeji AI (energy_ai).
                energy_cost, energy_note, penb_text = extract_costs(cost_text)
            else:
                penb_text = extract_penb(cost_text)   # u prodeje aspon PENB z textu
            if not penb and penb_text:
                penb = penb_text
            listings.append(RawListing(
                **common,
                area=float(area) if area else None,
                dispozice=dispozice, penb=penb,
                energy_cost=energy_cost, energy_note=energy_note,
                raw={"list": item},
            ))

    return ScrapeResult(listings, ok=True, message=f"OK, {len(listings)} nabidek v Chrudimi")
