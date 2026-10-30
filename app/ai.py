"""Lokalni AI (Ollama) jako ZALOHA pro dohledani mesicnich nakladu na bydleni
v popisu pronajmu - pouziva se jen tam, kde je regulerni (regex) extrakce nenasla.

Cele je volitelne: kdyz Ollama nebezi nebo je v configu vypnuta, funkce se tise
preskoci a vrati (None, None).

Pojistka proti halucinacim: cislo, ktere model vrati, musi se doslovne vyskytovat
v textu popisu - jinak se zahodi (model si tak nemuze vymyslet napr. soucet s najmem).
"""
from __future__ import annotations

import json
import re
from typing import Optional

import requests

PROMPT = """Najdi v inzerátu na pronájem bytu MĚSÍČNÍ zálohu/cenu za energie a služby
placenou NAVÍC k nájmu (energie, elektřina, plyn, topení, voda, služby, provozní náklady,
náklady na bydlení, zálohy, poplatky, inkaso).

Vrať POUZE tuto konkrétní částku, pokud je v textu uvedena číslem. NEPOČÍTEJ do ní:
nájem, vratnou kauci/jistotu/depozit, provizi, poplatek za podnájem ani jednorázové platby.
Nic nedopočítávej ani nevymýšlej – když částka není jasně uvedena, vrať null.

Odpověz POUZE tímto JSON, bez dalšího textu a bez jakéhokoli rozpisu:
{"naklady_kc": <číslo v Kč, nebo null>}

Text inzerátu:
\"\"\"%s\"\"\""""


def ollama_available(cfg: dict) -> bool:
    ai = cfg.get("ai", {})
    if not ai.get("enabled"):
        return False
    try:
        r = requests.get(ai["endpoint"].rstrip("/") + "/api/tags", timeout=4)
        return r.status_code == 200
    except Exception:
        return False


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s))


def _number_in_text(amount: float, text: str) -> bool:
    """True, pokud se cislo (bez oddelovacu) doslovne vyskytuje v textu."""
    target = _digits(int(amount))
    runs = re.findall(r"\d[\d.\s]*\d|\d", text)
    return target in {_digits(r) for r in runs}


def _parse(resp: str) -> Optional[float]:
    """Vytahne pouze cislo 'naklady_kc'. Zadny volny text (aby AI nehalucinovala rozpis)."""
    m = re.search(r"\{.*\}", resp or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    val = obj.get("naklady_kc")
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        d = _digits(val)
        return float(d) if d else None
    return None


def extract_energy(description: str, cfg: dict) -> tuple[Optional[float], Optional[str]]:
    """Vraci (mesicni_naklady_kc, None). AI vraci JEN cislo - rozpis polozek pochazi
    vyhradne z regexu nad skutecnym textem, aby se nic nevymyslelo.
    Nikdy nevyhazuje vyjimku."""
    ai = cfg.get("ai", {})
    desc = (description or "").strip()
    if not desc or not ai.get("enabled"):
        return None, None
    desc = desc[:1600]
    try:
        r = requests.post(
            ai["endpoint"].rstrip("/") + "/api/generate",
            json={"model": ai["model"], "prompt": PROMPT % desc,
                  "stream": False, "format": "json", "options": {"temperature": 0}},
            timeout=ai.get("timeout_s", 30),
        )
        r.raise_for_status()
        resp = r.json().get("response", "")
    except Exception:
        return None, None

    cost = _parse(resp)
    # pojistka proti halucinaci: cislo musi byt v rozsahu A doslovne v textu
    if cost is not None and not (200 <= cost <= 60000 and _number_in_text(cost, desc)):
        cost = None
    return cost, None   # note vzdy None - AI negeneruje volny text
