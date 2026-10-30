"""Sdilene pomucky pro scrapery - HTTP session s browser hlavickami, zdvorila prodleva."""
from __future__ import annotations

import time
import requests

from ..config import get_config


class ScrapeResult:
    """Vysledek scrapovani jednoho zdroje."""

    def __init__(self, listings, ok: bool, message: str = ""):
        self.listings = listings
        self.ok = ok            # False = zdroj nedostupny/selhal -> UI ukaze "zdroj nedostupny"
        self.message = message


def make_session() -> requests.Session:
    cfg = get_config()["sit"]
    s = requests.Session()
    s.headers.update({
        "User-Agent": cfg["user_agent"],
        "Accept-Language": "cs,en;q=0.8",
    })
    s.request_timeout = cfg["timeout_s"]  # ulozime si pro pohodli
    return s


def polite_sleep() -> None:
    time.sleep(get_config()["sit"]["prodleva_mezi_dotazy_s"])


def clean_text(s: str | None) -> str | None:
    if not s:
        return None
    return " ".join(s.split()).strip() or None
