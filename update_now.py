"""Jednorazova aktualizace dat (scrape vsech segmentu). Pouziti:  python update_now.py

Tento skript spousti Planovac uloh Windows jednou denne. Server bezet nemusi.
"""
from app.pipeline import run_update

if __name__ == "__main__":
    print("Spoustim aktualizaci nabidek pro Chrudim (pozemky, byty, domy)...")
    summary = run_update()
    for seg in summary["segmenty"]:
        print(f"\n[{seg['kategorie']} – {seg['typ']}]")
        for key, info in seg["zdroje"].items():
            stav = "OK" if info["ok"] else "NEDOSTUPNY"
            print(f"   {info['nazev']:<16} {stav:<11} {info['pocet']:>3} nabidek  | {info['zprava']}")
    ai = summary.get("ai", {})
    if ai.get("pouzito"):
        print(f"\n[AI energie – {ai.get('model')}] prověřeno {ai.get('provereno')} pronájmů, "
              f"doplněno {ai.get('doplneno')} cen energií z popisu.")
    else:
        print(f"\n[AI energie] nepoužito: {ai.get('duvod')}")
    print("\nHotovo:", summary["cas"])
