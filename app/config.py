"""Nacitani konfigurace z config.toml (stdlib tomllib, zadna extra zavislost)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from functools import lru_cache

# Koren projektu = o uroven vys nez slozka app/
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
# Sablona bez tajnych udaju (je v gitu); config.toml s klicem je v .gitignore.
CONFIG_EXAMPLE_PATH = ROOT / "config.example.toml"
DB_PATH = ROOT / "data" / "sniper.db"


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Nacte a nacachuje config.toml (kdyz chybi, pouzije config.example.toml).
    Cache lze zrusit pres load_config.cache_clear()."""
    path = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_config(fresh: bool = False) -> dict:
    if fresh:
        load_config.cache_clear()
    return load_config()
