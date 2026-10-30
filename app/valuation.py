"""Hodnoceni bytu a domu podle konceptu (cena ukotvena srovnatelnymi nabidkami).

ZASADA: cena se NEURCUJE souctem bodu za vlastnosti. Primarni je porovnani se
srovnatelnymi nabidkami (stejna dispozice, jinak cely segment). Vysledek rika, zda
je nabidka levnejsi / v beznem rozmezi / drazsi nez podobne nabidky - a jak jisty
ten zaver je. Jde o NABIDKOVE ceny, ne realizovane (viz metodika CSU).

Tohle je 1. krok (kotva). Faktory z popisu (impacty) sem pozdeji pribudou jako
VYSVETLENI odchylky, nikoli jako body.
"""
from __future__ import annotations

from typing import Optional

from .stats import comparable_pm2_disp, quantile
from .scoring import JISTOTA_VYSOKA, JISTOTA_STREDNI, JISTOTA_NIZKA

# Puvod kotevnich udaju (strukturovana data ze zdroje = overitelna).
VERIFIED = "VERIFIED"

# Snizeni jistoty o jeden stupen (kdyz je pritomne vazne riziko M).
_DOWNGRADE = {JISTOTA_VYSOKA: JISTOTA_STREDNI, JISTOTA_STREDNI: JISTOTA_NIZKA,
              JISTOTA_NIZKA: JISTOTA_NIZKA}


def apply_risks(oceneni: dict, ai_factors: list[dict]) -> dict:
    """4. krok: vazna rizika (faktory s impactem 'M' - napr. vlhkost/plisen) snizuji
    jistotu kotvy o jeden stupen a ulozi se k zobrazeni jako varovani. Meni oceneni
    na miste a vraci ho. Vola se PRED explain_with_factors (aby snizena jistota
    ovlivnila i to, zda se ukaze interpretacni veta)."""
    known = [f for f in (ai_factors or []) if f.get("origin") == "LISTING_CLAIM"]
    rizika = [f for f in known if f.get("impact") == "M"]
    oceneni["rizika"] = [{"label": f["label"], "value": f["value"], "citace": f.get("citace")}
                         for f in rizika]
    if rizika:
        oceneni["jistota"] = _DOWNGRADE.get(oceneni.get("jistota"), oceneni.get("jistota"))
        oceneni["jistota_snizena"] = True
    return oceneni


def evaluate_property(group: dict, stats: dict, cfg: dict) -> dict:
    """Vraci hodnoceni bytu/domu vuci srovnatelnym nabidkam.

    Klice: pm2, median, n, zaklad, used_disp, valuation, smer (low/mid/high),
    pct (% vuci medianu), expected_low/high (ocekavane rozmezi celkove ceny Kc),
    jistota, verified[], missing[].
    """
    pm2 = group.get("price_per_m2")
    area = group.get("area")
    price = group.get("price")
    disp = group.get("dispozice")
    p = cfg["cena_pasma"]
    min_c = p["min_srovnatelnych"]

    median, arr, zaklad = comparable_pm2_disp(stats, disp, min_c)
    n = len(arr)
    disp_arr = (stats.get("_pm2_sorted_by_disp") or {}).get(disp or "", [])
    used_disp = bool(disp and (stats.get("_pm2_median_by_disp") or {}).get(disp) is not None
                     and len(disp_arr) >= min_c)

    # Overene (strukturovane) kotevni udaje vs. chybejici.
    verified, missing = [], []
    for lbl, val in [("cena", price), ("plocha", area), ("dispozice", disp)]:
        (verified if val else missing).append(lbl)

    # U domu je cena/m2 zavadejici - nezahrnuje hodnotu pozemku (viz koncept).
    caveat = None
    if group.get("category") == "domy":
        caveat = "cena za m² u domů nezahrnuje hodnotu pozemku – srovnání je jen orientační"

    out = {
        "pm2": pm2, "median": median, "n": n, "zaklad": zaklad, "used_disp": used_disp,
        "verified": verified, "missing": missing, "caveat": caveat,
        "pct": None, "valuation": None, "smer": None,
        "expected_low": None, "expected_high": None, "jistota": JISTOTA_NIZKA,
    }

    # Bez ceny/plochy nebo bez srovnani nelze zaradit.
    if not pm2 or not median:
        out["valuation"] = "nelze spolehlivě určit"
        return out

    ratio = pm2 / median
    out["pct"] = round((ratio - 1) * 100)
    if ratio < p["levnejsi_pod"]:
        out["valuation"], out["smer"] = "levnější než srovnatelné nabídky", "low"
    elif ratio > p["drazsi_nad"]:
        out["valuation"], out["smer"] = "dražší než srovnatelné nabídky", "high"
    else:
        out["valuation"], out["smer"] = "v běžném rozmezí", "mid"

    # Ocekavane rozmezi celkove ceny = pasmo srovnatelnych pm2 * plocha teto nabidky.
    # Pri dost velkem vzorku bereme mezikvartilove pasmo (p25-p75), jinak median +-10 %.
    if n >= 8:
        low_pm2, high_pm2 = quantile(arr, 0.25), quantile(arr, 0.75)
    else:
        low_pm2, high_pm2 = median * p["levnejsi_pod"], median * p["drazsi_nad"]
    if area:
        out["expected_low"] = low_pm2 * area
        out["expected_high"] = high_pm2 * area

    # Jistota: chybi-li overeny kotevni udaj -> nizka; slabe srovnani (bez shody
    # dispozice nebo malo vzorku) -> stredni; jinak vysoka.
    if missing:
        out["jistota"] = JISTOTA_NIZKA
    elif not used_disp or n < max(min_c, 5):
        out["jistota"] = JISTOTA_STREDNI
    else:
        out["jistota"] = JISTOTA_VYSOKA

    return out


def _kc(v: Optional[float]) -> str:
    return f"{int(round(v)):,}".replace(",", " ") + " Kč" if v else "?"


def _cl(x, lo, hi):
    return max(lo, min(hi, x))


_PENB_ORDER = "ABCDEFG"


def score_property(group: dict, oceneni: dict, faktory_vyklad: dict, cfg: dict) -> dict:
    """Skóre ATRAKTIVITY bytu/domu (0-100) = orientační pořadí, NE ocenění.
    Cenu neurčuje - jen řadí nabídky podle zajímavosti. Váhy z config [byty_skore_vahy].
    Vzhled z fotek je jen malý bonus (AI se může mýlit); riziko M penalizuje.
    """
    w = cfg.get("byty_skore_vahy", {})
    o, fv = oceneni or {}, faktory_vyklad or {}
    br = []

    # 1) Cena vuci srovnatelnym (jadro)
    vaha = w.get("cena_vs_srovnatelne", 50)
    pm2, med = o.get("pm2"), o.get("median")
    if o.get("caveat"):
        # U domu je cena/m2 nespolehliva (nezahrnuje pozemek) - neutralni, at neinflatuje skore.
        s_cena = vaha * 0.5
        br.append({"k": "Cena vs. srovnatelné", "b": round(s_cena, 1), "max": vaha,
                   "p": "u domů je cena/m² orientační (bez pozemku) – neutrální"})
    elif not pm2 or not med:
        s_cena = vaha * 0.5
        br.append({"k": "Cena vs. srovnatelné", "b": round(s_cena, 1), "max": vaha,
                   "p": "cena/m² neznámá nebo bez srovnání – 50 %"})
    else:
        frac = _cl((1.3 - pm2 / med) / (1.3 - 0.7), 0.0, 1.0)
        s_cena = vaha * frac
        pct = o.get("pct") or 0
        br.append({"k": "Cena vs. srovnatelné", "b": round(s_cena, 1), "max": vaha,
                   "p": f"cena/m² {abs(pct)} % {'pod' if pct < 0 else 'nad'} mediánem {o.get('zaklad','')}"})

    # 2) Vlastnosti z popisu (net dolozenych faktoru)
    vaha = w.get("vlastnosti", 25)
    pv, pn = fv.get("pro_vyssi") or [], fv.get("pro_nizsi") or []
    if not pv and not pn:
        s_vl = vaha * 0.5
        br.append({"k": "Vlastnosti z popisu", "b": round(s_vl, 1), "max": vaha,
                   "p": "bez výrazných doložených vlastností – 50 %"})
    else:
        net = sum(f["impact"] for f in pv) + sum(f["impact"] for f in pn)
        s_vl = vaha * _cl(0.5 + net * 0.1, 0.0, 1.0)
        br.append({"k": "Vlastnosti z popisu", "b": round(s_vl, 1), "max": vaha,
                   "p": f"souhrn pro/proti = {'+' if net >= 0 else ''}{net}"})

    # 3) Energetika (PENB)
    vaha = w.get("penb", 15)
    penb = group.get("penb")
    if penb and penb in _PENB_ORDER:
        s_penb = vaha * (6 - _PENB_ORDER.index(penb)) / 6
        br.append({"k": "Energetika (PENB)", "b": round(s_penb, 1), "max": vaha, "p": f"PENB {penb}"})
    else:
        s_penb = vaha * 0.5
        br.append({"k": "Energetika (PENB)", "b": round(s_penb, 1), "max": vaha, "p": "PENB neuveden – 50 %"})

    # 4) Historie (zlevneni / doba v nabidce)
    vaha = w.get("historie", 10)
    price, price_old, days = group.get("price"), group.get("price_old"), group.get("days_on_market")
    if price and price_old and price_old > price:
        drop = (price_old - price) / price_old
        s_hist = vaha * _cl(drop / 0.2, 0.3, 1.0)
        pop = f"zlevněno o {round(drop * 100)} %"
    elif days is not None:
        if days <= 14:
            s_hist, pop = vaha * 0.4, f"nová nabídka ({days} dní)"
        elif days >= 90:
            s_hist, pop = vaha * 0.6, f"dlouho v nabídce ({days} dní)"
        else:
            s_hist, pop = vaha * 0.5, f"v nabídce {days} dní"
    else:
        s_hist, pop = vaha * 0.4, "bez cenové historie"
    br.append({"k": "Historie", "b": round(s_hist, 1), "max": vaha, "p": pop})

    zaklad = s_cena + s_vl + s_penb + s_hist

    # + Bonus za vzhled z fotek (maly - AI se muze mylit)
    bonus_max = w.get("vzhled_bonus", 5)
    vz = group.get("vzhled") or {}
    vzhled_val = vz.get("value")
    if vzhled_val == "moderní":
        bonus = bonus_max
    elif vzhled_val == "středně moderní":
        bonus = round(bonus_max * 0.4)
    else:
        bonus = 0
    bonus_pop = (f"vzhled „{vzhled_val}“ (+{bonus})" if vzhled_val else None)

    # - Penalizace za vazne riziko (M)
    penalty = w.get("riziko_penalty", 10) if (o.get("rizika")) else 0
    penalty_pop = ("riziko ke kontrole (−" + str(penalty) + ")") if penalty else None

    total = int(_cl(round(zaklad + bonus - penalty), 0, 100))

    prahy = cfg.get("hodnoceni_prahy", {})
    if total >= prahy.get("velmi_zajimava", 80):
        label, band = "velmi zajímavá nabídka", "vys"
    elif total >= prahy.get("zajimava", 60):
        label, band = "zajímavá nabídka", "zaj"
    elif total >= prahy.get("bezna", 40):
        label, band = "běžná nabídka", "bez"
    else:
        label, band = "méně zajímavá nabídka", "niz"

    return {"skore": total, "label": label, "band": band, "breakdown": br,
            "bonus": bonus, "bonus_pop": bonus_pop,
            "penalty": penalty, "penalty_pop": penalty_pop,
            "jistota": o.get("jistota")}


def build_reasoning(group: dict, oceneni: dict, faktory_vyklad: dict, stats: dict) -> dict:
    """5. krok: souhrnne oduvodneni bytu/domu. Sklada se VYHRADNE z vypocitanych
    hodnot a dolozenych faktoru - nic se nedomysli. Vraci {zaver, duvody[], omezeni[]}.
    """
    o = oceneni or {}
    fv = faktory_vyklad or {}
    je_pronajem = group.get("offer_type") == "pronajem"
    duvody: list[str] = []
    omezeni: list[str] = []

    # --- Duvody (z vypoctu a dolozenych faktoru) ---
    if o.get("pct") is not None and o.get("median"):
        smer = "pod" if o["pct"] < 0 else ("nad" if o["pct"] > 0 else "na úrovni")
        if o["pct"] == 0:
            duvody.append(f"Cena za m² je na úrovni mediánu {o['zaklad']} ({o['n']} nab.).")
        else:
            duvody.append(f"Cena za m² je {abs(o['pct'])} % {smer} mediánem "
                          f"{o['zaklad']} ({o['n']} nab.).")
    if o.get("expected_low"):
        duvody.append(f"Obvyklé rozmezí ceny pro tuto velikost: "
                      f"{_kc(o['expected_low'])} – {_kc(o['expected_high'])}.")
    if fv.get("pro_vyssi"):
        duvody.append("Pro vyšší cenu mluví: "
                      + ", ".join(f["value"] for f in fv["pro_vyssi"]) + ".")
    if fv.get("pro_nizsi"):
        duvody.append("Pro nižší cenu mluví: "
                      + ", ".join(f["value"] for f in fv["pro_nizsi"]) + ".")
    if o.get("rizika"):
        duvody.append("Ke kontrole (vážný signál): "
                      + ", ".join(r["value"] for r in o["rizika"]) + ".")

    # --- Omezeni (poctive, aby vysledek netvrdil vic, nez unese) ---
    if not je_pronajem:
        omezeni.append("Jde o nabídkové ceny, ne realizované – lze říct „levnější/dražší než "
                       "podobné nabídky“, ne „podhodnocená vůči tržní hodnotě“.")
    else:
        omezeni.append("Srovnání vychází z nabídkových nájmů, ne z uzavřených smluv.")
    if o.get("caveat"):
        omezeni.append(o["caveat"] + ".")
    if o.get("missing"):
        omezeni.append("Chybí ověřený údaj: " + ", ".join(o["missing"])
                       + " – hodnocení je méně jisté.")
    if o.get("median") and not o.get("used_disp"):
        omezeni.append("Málo nabídek stejné dispozice – srovnáno se všemi srovnatelnými "
                       "nabídkami v Chrudimi.")
    if fv.get("pro_vyssi") or fv.get("pro_nizsi") or o.get("rizika"):
        omezeni.append("Vlastnosti z popisu jsou tvrzení inzerátu, ne ověřená fakta.")
    pocet = stats.get("pocet_aktivnich")
    if pocet:
        omezeni.append(f"V tomto segmentu je {pocet} nabídek, nad kterými se srovnává.")

    return {"zaver": o.get("valuation"), "jistota": o.get("jistota"),
            "duvody": duvody, "omezeni": omezeni}


def explain_with_factors(ai_factors: list[dict], oceneni: dict) -> dict:
    """3. krok: faktory z popisu (LISTING_CLAIM) jako VYSVETLENI odchylky od kotvy.

    NEscitame impacty do zadneho cisla (to koncept zakazuje). Jen roztridime, co by
    cenu spis zvysovalo / snizovalo, a spojime to se smerem kotvy do jedne opatrne vety
    ("co muze/nemusi vysvetlovat, proc je cena, kde je").
    """
    known = [f for f in (ai_factors or []) if f.get("origin") == "LISTING_CLAIM"]
    pro_vyssi = [f for f in known if isinstance(f.get("impact"), int) and f["impact"] > 0]
    pro_nizsi = [f for f in known if isinstance(f.get("impact"), int) and f["impact"] < 0]
    kontrola = [f for f in known if f.get("impact") == "M"]

    out = {"pro_vyssi": pro_vyssi, "pro_nizsi": pro_nizsi, "kontrola": kontrola, "vyklad": None}

    smer = oceneni.get("smer")
    # Vyklad (vetu) davame jen kde je informativni: jasna odchylka od kotvy, dost
    # jista kotva a existuji faktory. Pri nizke jistote (napr. domy - cena/m2 orientacni)
    # vetu vynechame, aby netvrdila vic nez data unesou; split pro/proti zustava.
    if (smer not in ("low", "high") or not (pro_vyssi or pro_nizsi)
            or oceneni.get("jistota") == JISTOTA_NIZKA):
        return out

    # Zamerne NEscitame faktory do net verdiktu (nemaji stejnou vahu). Vetu volime
    # podle toho, zda protismerne faktory VUBEC existuji - kdyz ano, poctive je prizname.
    if smer == "low":       # levnejsi nez srovnatelne
        if not pro_nizsi and not kontrola:
            out["vyklad"] = ("Levnější než srovnatelné a v popisu není nic, co by nižší cenu "
                             "vysvětlovalo – stojí za bližší prověření.")
        else:
            out["vyklad"] = ("Levnější než srovnatelné; část rozdílu mohou vysvětlovat uvedené "
                             "nevýhody či rizika níže, zbytek stojí za prověření.")
    else:                   # drazsi nez srovnatelne
        if not pro_vyssi:
            out["vyklad"] = ("Dražší než srovnatelné, aniž by to popis zjevně opodstatňoval – "
                             "stojí za prověření.")
        else:
            out["vyklad"] = ("Dražší než srovnatelné; vyšší cenu mohou částečně opodstatňovat "
                             "uvedené přednosti (viz „pro vyšší cenu“).")
    return out
