"""Deduplikace nabidek napric zdroji (bod 6 zadani).

Stejny pozemek byva na vice portalech. Dve nabidky povazujeme za DUPLICITU pouze
tehdy, kdyz jsou z RUZNYCH zdroju a PRESNE se shoduje celkova cena i vymera.
Takove nabidky spojime do jednoho hlavniho zaznamu (skupiny). Nic se nemaze -
jen se priradi group_id.

Zamerne uz nepouzivame volne tolerance ani GPS blizkost: ty spojovaly ruzne
sousedni pozemky s podobnou (ne stejnou) cenou/vymerou do jedne skupiny.
"""
from __future__ import annotations

from typing import Optional


def _eq_num(a: Optional[float], b: Optional[float]) -> bool:
    """Presna shoda dvou cisel (s malou tolerandi na zaokrouhleni float)."""
    if a is None or b is None:
        return False
    return round(a) == round(b)


def _is_duplicate(l1: dict, l2: dict) -> bool:
    """Duplicita = jine zdroje + presna shoda ceny A vymery.
    Pokud u nekterr nabidky chybi cena nebo vymera, nespojujeme (nelze overit).
    """
    if l1["source"] == l2["source"]:
        return False
    if l1.get("price") is None or l1.get("area") is None:
        return False
    if l2.get("price") is None or l2.get("area") is None:
        return False
    return _eq_num(l1["price"], l2["price"]) and _eq_num(l1["area"], l2["area"])


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def cluster(listings: list[dict], cfg: dict) -> tuple[dict[int, int], set[int]]:
    """listings = seznam dict s klici id, source, area, price.

    Vraci:
      - mapping listing_id -> group_id (group_id = nejmensi listing_id ve skupine),
      - mnozina group_id oznacenych jako "mozna duplicita" (pri presne shode
        prazdna - vsechny spojene jsou jista duplicita).
    """
    n = len(listings)
    uf = _UF(n)
    for i in range(n):
        for j in range(i + 1, n):
            if _is_duplicate(listings[i], listings[j]):
                uf.union(i, j)

    root_to_members: dict[int, list[int]] = {}
    for idx in range(n):
        root_to_members.setdefault(uf.find(idx), []).append(idx)

    mapping: dict[int, int] = {}
    for members in root_to_members.values():
        gid = min(listings[m]["id"] for m in members)
        for m in members:
            mapping[listings[m]["id"]] = gid

    # Pri presne shode jsou vsechny spojene nabidky jista duplicita.
    uncertain_groups: set[int] = set()
    return mapping, uncertain_groups
