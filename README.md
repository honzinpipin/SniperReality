# Sniper Chrudim – nemovitosti v Chrudimi

Interní light prototyp. Automaticky načítá nabídky nemovitostí **v Chrudimi**
(obec Chrudim vč. částí – Chrudim I–IV, Medlešice…) ze tří zdrojů (Sreality,
Reality.iDNES, Bezrealitky), sjednotí je, označí duplicity a zobrazí v přehledném webu.

Na úvodní stránce (rozcestník) si uživatel vybere kategorii:

| Kategorie | Cesta | Poznámka |
|-----------|-------|----------|
| Pozemky na prodej | `/pozemky` | se skóre 0–100, hodnocením a odůvodněním pořadí |
| Byty na prodej | `/byty/prodej` | celková cena, dispozice, plocha, PENB |
| Byty k pronájmu | `/byty/pronajem` | nájem/měsíc + **cena energií zvlášť**, dispozice, PENB |
| Domy na prodej | `/domy/prodej` | celková cena, plocha, PENB |
| Domy k pronájmu | `/domy/pronajem` | nájem/měsíc + cena energií, PENB |

U pronájmů se cena energií hledá i v textu popisu (často není v celkové ceně) a
zobrazuje se pod nájmem; když se nenajde, uvádí se „neuvedeno". U bytů a domů se
zobrazuje dispozice (3+1, 3kk…), plocha, lokalita, úvodní fotka, odkaz na zdroj a
energetická náročnost (PENB A–G).

> **Skóre a hodnocení jsou orientační** a vycházejí pouze z **nabídkových** údajů
> z inzerátů. Nejde o znalecké ocenění ani o ověření právního stavu, sítí či
> územních omezení. Typ pozemku, dispozice i PENB jsou převzaty z inzerátu.

---

## 1. Rychlý start (Windows)

```bash
python -m pip install -r requirements.txt
```

Konfigurace – zkopíruj šablonu (bez ní aplikace použije výchozí `config.example.toml`):

```bash
copy config.example.toml config.toml
```

OpenAI klíč je **volitelný** (AI extrakce faktorů a posouzení vzhledu z fotek; bez něj
AI kroky spadnou na lokální Ollamu nebo se tiše přeskočí). Nastav ho ideálně jako
proměnnou prostředí `OPENAI_API_KEY`, případně do `config.toml` → `[ai.openai] api_key`.
**`config.toml` je v `.gitignore`, klíč se do gitu nedostane.**

Načtení dat ze zdrojů (poprvé i kdykoli ručně):

```bash
python update_now.py
```

Spuštění webu:

```bash
python run_server.py
```

Pak otevřít v prohlížeči: **http://127.0.0.1:8000**

Data lze aktualizovat i tlačítkem **Aktualizovat** přímo v aplikaci.

---

## 1b. Volitelně: dohledání ceny energií lokální AI (Ollama)

U pronájmů cena energií často není v celkové ceně, ale je zmíněná v popisu.
Když ji regulérní extrakce nenajde, může ji v popisu dohledat **lokální AI (Ollama)** –
zdarma, běží na tvém PC, data nikam neodcházejí.

**Zapnutí (jednorázově):**

1. Nainstaluj Ollamu z <https://ollama.com>.
2. Stáhni malý model:

   ```bash
   ollama pull llama3.2:3b
   ```

3. Spusť službu (běží na `http://localhost:11434`):

   ```bash
   ollama serve
   ```

Při další aktualizaci (`update_now.py` nebo tlačítko Aktualizovat) se u pronájmů
bez zjištěné ceny energií zkusí částka najít v popisu. Nalezené ceny mají v UI
štítek **„z popisu"**. Nastavení je v `config.toml` v sekci `[ai]` (lze vypnout,
změnit model, endpoint i limit dotazů na běh).

> Když Ollama neběží nebo je v configu vypnutá, krok se **tiše přeskočí** – aplikace
> funguje dál, jen se energie z popisu nedoplní. AI nikdy nic nedomýšlí: když částka
> v textu není, nechá „neuvedeno".

---

## 2. Datové zdroje

Načítá se **pouze** z těchto tří zdrojů (neoficiální, ale technicky dostupné cesty;
malý objem, jednou denně, jen pro interní použití):

| Zdroj | Způsob přístupu | Poznámka |
|-------|-----------------|----------|
| **Sreality** | interní JSON API `/api/v1/estates/search` + detail `/api/v1/estates/{id}` | nejbohatší data: plocha, sítě, popis, GPS, historie ceny |
| **Reality.iDNES** | HTML stránky vyhledávání (nemá veřejné API) | data z karty: název, cena, plocha, cena/m², lokalita, foto |
| **Bezrealitky** | interní GraphQL `listAdverts` (nutné hlavičky prohlížeče) | pozemky od majitelů; obvykle málo nabídek |

**Zastávky pro dojíždění:** dataset `data/zastavky.json` pochází z OpenStreetMap –
© přispěvatelé [OpenStreetMap](https://www.openstreetmap.org/copyright), licence ODbL.
Spojení se dohledává přes odkaz do [IDOS](https://idos.cz).

**Rozsah lokality:** hledá se pouze **město Chrudim**. U Sreality se stahuje celý
okres a filtruje na obec „Chrudim"; u iDNES a Bezrealitky se cílí přímo na město.

**Právní/technická poznámka:** žádný ze zdrojů nenabízí oficiální veřejné API pro
tento účel. Používají se tedy neoficiální (interní) rozhraní jejich vlastních webů.
Je to funkční řešení pro interní prototyp, ale jde o šedou zónu vůči podmínkám
portálů a rozhraní se mohou bez varování změnit (pak je nutná úprava scraperu).
Proto je vše ošetřené tak, aby výpadek jednoho zdroje neshodil ostatní.

---

## 3. Denní automatická aktualizace (Plánovač úloh Windows)

Server běžet nemusí – stačí spouštět `update_now.py` jednou denně:

1. Otevřít **Plánovač úloh** (Task Scheduler).
2. **Vytvořit základní úkol** → název „Sniper Chrudim".
3. Spouštěč: **Denně**, např. 06:00.
4. Akce: **Spustit program**
   - Program/skript: `python`
   - Argumenty: `update_now.py`
   - Začít v: složka projektu (kde je `update_now.py`)
5. Dokončit.

---

## 4. Jak se počítá skóre (0–100)

Skóre je součet předem definovaných bodů (váhy v `config.toml`, sekce `[skore_vahy]`):

| Kritérium | Max. bodů | Princip |
|-----------|-----------|---------|
| Cena za m² | 40 | srovnání s **mediánem podobných** nabídek (stavební vs. zemědělské zvlášť) |
| Celková cena | 15 | pozice v cenovém rozdělení (levnější = víc bodů) |
| Typ pozemku | 15 | stavební/bydlení výš než zemědělský/rekreační (nastavitelné) |
| Plocha | 10 | malý / střední / velký (hranice v konfiguraci) |
| Dostupnost sítí | 10 | za každou uvedenou síť + přístup |
| Historie / délka | 10 | zlevnění nebo doba v nabídce |

Slovní hodnocení: **velmi zajímavá / zajímavá / běžná / méně zajímavá /
nelze spolehlivě vyhodnotit**. Kromě skóre se zobrazuje i **jistota hodnocení**
(vysoká / střední / nízká) podle počtu chybějících údajů.

U každé nabídky je rozklikatelné **odůvodnění** (důvody, omezení, tabulka bodů) –
text vzniká výhradně z vypočítaných hodnot a skutečně dostupných údajů; nedoplňují
se neznámé skutečnosti.

**Pojistka na nereálné ceny:** stavební/komerční pozemek s cenou za m² hluboko pod
mediánem (např. cena za m², spoluvlastnický podíl nebo dražba) se neoznačí jako
výhodný, ale jako „nelze spolehlivě vyhodnotit" s nízkou jistotou.

---

## 5. Konfigurace

Vše nastavitelné je v **`config.toml`** (čte se při každém běhu i startu serveru):

- hranice velikosti pozemku (`[plocha]`),
- váhy a body jednotlivých kritérií (`[skore_vahy]`, `[skore_typ]`, `[skore_plocha]`),
- pásma levnější/dražší a práh podezřelé ceny (`[cena_pasma]`),
- prahy slovního hodnocení (`[hodnoceni_prahy]`),
- tolerance pro deduplikaci (`[deduplikace]`),
- síťové chování scraperů (`[sit]`).

Žádné hranice nejsou natvrdo v kódu.

---

## 6. Deduplikace

Stejný pozemek na více portálech se spojí do jednoho hlavního záznamu podle plochy,
ceny a přibližné GPS polohy. Hlavní záznam ukazuje všechny zdroje, odkazy, rozdíl
ceny a výměry mezi zdroji. Duplicity se **nikdy nemažou**; při nejistotě se zobrazí
štítek „možná duplicita".

## 7. Stav nabídek

U každé nabídky je vidět z jakého zdroje pochází a kdy byla naposledy kontrolována.
Když nabídka ze zdroje zmizí, systém **netvrdí, že byla prodána** – označí ji jako
`nelze ověřit` → po nastavené době `pravděpodobně staženo`. Při výpadku celého
zdroje se jeho nabídky označí `zdroj nedostupný`.

---

## 8. Co první verze záměrně neobsahuje

Ruční vkládání/úpravu nabídek, uživatelské účty a role, platby, veřejnou registraci,
mobilní aplikaci, mapy, katastr, územní plány ani jiné zdroje než uvedené tři.

## 9. Struktura projektu

```
config.example.toml      šablona nastavení (v gitu, bez tajných údajů)
config.toml              lokální nastavení vč. API klíče (v .gitignore)
run_server.py            spuštění webu
update_now.py            jednorázová aktualizace (pro Plánovač úloh)
app/
  models.py              jednotný tvar nabídky + normalizace typu pozemku
  db.py                  SQLite (nabídky, historie ceny, log běhů, stavy)
  config.py              načtení config.toml
  scrapers/              sreality.py, idnes.py, bezrealitky.py, base.py
  dedup.py               spojování duplicit napříč zdroji
  stats.py               statistiky lokality (medián/průměr po třídách)
  scoring.py             výpočet skóre, jistoty, valuace
  reasoning.py           textové odůvodnění pořadí
  pipeline.py            orchestrace aktualizace
  view.py                sestavení hlavních záznamů + filtry + řazení
  web/                   FastAPI + šablona + styl
data/sniper.db           databáze (vzniká automaticky)
```
