"""Scrapery jednotlivych zdroju. Kazdy vraci (list[RawListing], ok, message)."""
from .sreality import scrape_sreality
from .idnes import scrape_idnes
from .bezrealitky import scrape_bezrealitky

# Registr zdroju: klic -> (nazev, funkce).
SCRAPERS = {
    "sreality": ("Sreality", scrape_sreality),
    "idnes": ("Reality.iDNES", scrape_idnes),
    "bezrealitky": ("Bezrealitky", scrape_bezrealitky),
}
