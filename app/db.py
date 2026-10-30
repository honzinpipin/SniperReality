"""SQLite vrstva - schema, upsert nabidek, historie ceny, log behu.

Zadne duplicity se nikdy nemazou natvrdo; stav nabidky se jen aktualizuje.
"""
from __future__ import annotations

import sqlite3
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .config import DB_PATH
from .models import RawListing, utilities_dict

# Stavy nabidky (viz zadani, bod 5).
STAV_AKTIVNI = "aktivní"
STAV_NELZE_OVERIT = "nelze ověřit"
STAV_ZDROJ_NEDOSTUPNY = "zdroj nedostupný"
STAV_PRAVDEPODOBNE_STAZENO = "pravděpodobně staženo"


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source          TEXT NOT NULL,
            source_id       TEXT NOT NULL,
            category        TEXT DEFAULT 'pozemky',
            offer_type      TEXT DEFAULT 'prodej',
            url             TEXT,
            title           TEXT,
            price           REAL,
            area            REAL,
            price_per_m2    REAL,
            dispozice       TEXT,
            penb            TEXT,
            energy_cost     REAL,
            energy_note     TEXT,
            energy_ai       INTEGER DEFAULT 0,
            city            TEXT,
            city_part       TEXT,
            locality        TEXT,
            land_type       TEXT,
            land_type_raw   TEXT,
            description     TEXT,
            image_url       TEXT,
            lat             REAL,
            lon             REAL,
            price_old       REAL,
            utilities_json  TEXT,
            access_road     TEXT,
            date_added      TEXT,
            ai_factors_json     TEXT,
            ai_factors_checked  INTEGER DEFAULT 0,
            ai_factors_provider TEXT,
            images_json         TEXT,
            ai_vzhled           TEXT,
            ai_vzhled_duvod     TEXT,
            ai_vzhled_checked   INTEGER DEFAULT 0,
            first_seen      TEXT,
            last_checked    TEXT,
            last_seen_active TEXT,
            status          TEXT,
            group_id        INTEGER,
            raw_json        TEXT,
            UNIQUE(source, source_id)
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id  INTEGER NOT NULL,
            checked_at  TEXT NOT NULL,
            price       REAL,
            FOREIGN KEY (listing_id) REFERENCES listings(id)
        );

        CREATE TABLE IF NOT EXISTS runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT,
            finished_at TEXT,
            source      TEXT,
            category    TEXT DEFAULT 'pozemky',
            offer_type  TEXT DEFAULT 'prodej',
            status      TEXT,
            found       INTEGER,
            message     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
        CREATE INDEX IF NOT EXISTS idx_listings_group ON listings(group_id);
        CREATE INDEX IF NOT EXISTS idx_pricehist_listing ON price_history(listing_id);
        """
    )
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Doplni chybejici sloupce do existujici databaze (starsi schema)."""
    def cols(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    listings_new = [
        ("category", "TEXT DEFAULT 'pozemky'"), ("offer_type", "TEXT DEFAULT 'prodej'"),
        ("dispozice", "TEXT"), ("penb", "TEXT"), ("energy_cost", "REAL"), ("energy_note", "TEXT"),
        ("energy_ai", "INTEGER DEFAULT 0"), ("ai_energy_checked", "INTEGER DEFAULT 0"),
        ("date_added", "TEXT"),
        ("ai_factors_json", "TEXT"), ("ai_factors_checked", "INTEGER DEFAULT 0"),
        ("ai_factors_provider", "TEXT"),
        ("images_json", "TEXT"), ("ai_vzhled", "TEXT"), ("ai_vzhled_duvod", "TEXT"),
        ("ai_vzhled_checked", "INTEGER DEFAULT 0"),
    ]
    have = cols("listings")
    for name, ddl in listings_new:
        if name not in have:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {name} {ddl}")
    have_runs = cols("runs")
    for name, ddl in [("category", "TEXT DEFAULT 'pozemky'"), ("offer_type", "TEXT DEFAULT 'prodej'")]:
        if name not in have_runs:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {ddl}")


def upsert_listing(conn: sqlite3.Connection, l: RawListing) -> int:
    """Vlozi nebo aktualizuje nabidku. Vraci listing.id.

    - Nova nabidka: nastavi first_seen = dnes, status = aktivni.
    - Existujici: aktualizuje udaje, last_checked, last_seen_active, status = aktivni.
    - Pri zmene ceny zapise radek do price_history.
    """
    today = _today()
    cur = conn.execute(
        "SELECT id, price, first_seen, energy_cost, energy_note, energy_ai, date_added, "
        "description, images_json "
        "FROM listings WHERE source=? AND source_id=?",
        (l.source, l.source_id),
    )
    row = cur.fetchone()
    utils = json.dumps(utilities_dict(l), ensure_ascii=False)
    images = json.dumps(l.image_urls or [], ensure_ascii=False)
    e_ai_new = 1 if l.energy_ai else 0

    if row is None:
        cur = conn.execute(
            """
            INSERT INTO listings
              (source, source_id, category, offer_type, url, title, price, area, price_per_m2,
               dispozice, penb, energy_cost, energy_note, energy_ai, city, city_part,
               locality, land_type, land_type_raw, description, image_url, images_json, lat, lon,
               price_old, utilities_json, access_road, date_added, first_seen, last_checked,
               last_seen_active, status, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                l.source, l.source_id, l.category, l.offer_type, l.url, l.title, l.price,
                l.area, l.price_per_m2, l.dispozice, l.penb, l.energy_cost, l.energy_note,
                e_ai_new, l.city, l.city_part, l.locality, l.land_type, l.land_type_raw,
                l.description, l.image_url, images, l.lat, l.lon, l.price_old, utils,
                l.access_road, l.date_added, today, today, today, STAV_AKTIVNI, l.raw_json(),
            ),
        )
        listing_id = cur.lastrowid
        if l.price is not None:
            conn.execute(
                "INSERT INTO price_history (listing_id, checked_at, price) VALUES (?,?,?)",
                (listing_id, today, l.price),
            )
        return listing_id

    listing_id = row["id"]
    old_price = row["price"]
    # price_old: pokud zdroj sam neuvadi drivejsi cenu, dopocitame z historie
    price_old = l.price_old if l.price_old is not None else old_price

    # Zachovat energii dohledanou AI, kdyz novy scrape zadnou (regex) cenu nenasel.
    if l.energy_cost is None and not l.energy_ai and row["energy_ai"]:
        e_cost, e_note, e_ai = row["energy_cost"], row["energy_note"], row["energy_ai"]
    else:
        e_cost, e_note, e_ai = l.energy_cost, l.energy_note, e_ai_new
    # Zachovat realne datum vlozeni, kdyz ho novy scrape nema (napr. detail selhal).
    date_added = l.date_added or row["date_added"]

    conn.execute(
        """
        UPDATE listings SET
          category=?, offer_type=?, url=?, title=?, price=?, area=?, price_per_m2=?,
          dispozice=?, penb=?, energy_cost=?, energy_note=?, energy_ai=?, city=?, city_part=?,
          locality=?, land_type=?, land_type_raw=?, description=?, image_url=?, images_json=?,
          lat=?, lon=?, price_old=?, utilities_json=?, access_road=?, date_added=?,
          last_checked=?, last_seen_active=?, status=?, raw_json=?
        WHERE id=?
        """,
        (
            l.category, l.offer_type, l.url, l.title, l.price, l.area, l.price_per_m2,
            l.dispozice, l.penb, e_cost, e_note, e_ai, l.city, l.city_part,
            l.locality, l.land_type, l.land_type_raw, l.description, l.image_url, images,
            l.lat, l.lon, price_old, utils, l.access_road, date_added,
            today, today, STAV_AKTIVNI, l.raw_json(), listing_id,
        ),
    )
    # Historie ceny jen pri zmene.
    if l.price is not None and (old_price is None or abs((old_price or 0) - l.price) > 0.5):
        conn.execute(
            "INSERT INTO price_history (listing_id, checked_at, price) VALUES (?,?,?)",
            (listing_id, today, l.price),
        )
    # Kdyz se popis zmenil, znovu spustit AI extrakci faktoru (jinak zustanou zachovane).
    if (row["description"] or "") != (l.description or ""):
        conn.execute("UPDATE listings SET ai_factors_checked=0 WHERE id=?", (listing_id,))
    # Kdyz se zmenila sada fotek, znovu spustit AI posouzeni vzhledu.
    if (row["images_json"] or "") != images:
        conn.execute("UPDATE listings SET ai_vzhled_checked=0 WHERE id=?", (listing_id,))
    return listing_id


def mark_missing_listings(conn: sqlite3.Connection, source: str, category: str,
                          offer_type: str, seen_ids: set[str], dny_do_stazeno: int) -> None:
    """Nabidky ze zdroje v danem segmentu (kategorie+typ), ktere se v tomto behu
    neobjevily, oznaci - ale NIKDY netvrdi 'prodano'. Podle stari bud 'nelze overit',
    nebo 'pravdepodobne stazeno'. Zdroj byl dostupny (jinak se nevola).
    """
    today = date.today()
    cur = conn.execute(
        "SELECT id, source_id, last_seen_active, status FROM listings "
        "WHERE source=? AND category=? AND offer_type=?",
        (source, category, offer_type),
    )
    for row in cur.fetchall():
        if row["source_id"] in seen_ids:
            continue
        # spocitat dny od posledniho nalezeni
        try:
            last = date.fromisoformat((row["last_seen_active"] or "")[:10])
            dnu = (today - last).days
        except Exception:
            dnu = 0
        novy_stav = STAV_PRAVDEPODOBNE_STAZENO if dnu >= dny_do_stazeno else STAV_NELZE_OVERIT
        conn.execute(
            "UPDATE listings SET status=?, last_checked=? WHERE id=?",
            (novy_stav, today.isoformat(), row["id"]),
        )


def mark_source_unavailable(conn: sqlite3.Connection, source: str, category: str,
                            offer_type: str) -> None:
    """Zdroj se nepodarilo nacist v danem segmentu - u jeho drive aktivnich nabidek
    v tomto segmentu nastavi 'zdroj nedostupny'. Netvrdi prodej.
    """
    conn.execute(
        "UPDATE listings SET status=?, last_checked=? "
        "WHERE source=? AND category=? AND offer_type=? AND status=?",
        (STAV_ZDROJ_NEDOSTUPNY, _today(), source, category, offer_type, STAV_AKTIVNI),
    )


def log_run(conn: sqlite3.Connection, started: str, source: str, category: str,
            offer_type: str, status: str, found: int, message: str = "") -> None:
    conn.execute(
        "INSERT INTO runs (started_at, finished_at, source, category, offer_type, status, found, message) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (started, _now(), source, category, offer_type, status, found, message),
    )


def set_group_ids(conn: sqlite3.Connection, mapping: dict[int, int]) -> None:
    """mapping: listing_id -> group_id."""
    for lid, gid in mapping.items():
        conn.execute("UPDATE listings SET group_id=? WHERE id=?", (gid, lid))


def fetch_all_listings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM listings ORDER BY id").fetchall()


def listings_for_ai_energy(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Pronajmy s neznamou cenou energii, ktere maji popis a jeste nebyly overeny AI."""
    return conn.execute(
        """
        SELECT id, description FROM listings
        WHERE offer_type='pronajem' AND energy_cost IS NULL
          AND description IS NOT NULL AND TRIM(description) <> ''
          AND COALESCE(ai_energy_checked, 0) = 0
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()


def set_ai_energy(conn: sqlite3.Connection, listing_id: int,
                  cost: float | None, note: str | None) -> None:
    """Zapise vysledek AI. Ulozi castku i textovou poznamku (napr. "elektrina a plyn
    prevedeny na najemce"). Priznak ai_energy_checked=1 nastavi vzdy (i pri neuspechu),
    aby se stejny popis nedotazoval opakovane kazdy den."""
    if cost is not None or note:
        conn.execute(
            "UPDATE listings SET energy_cost=?, energy_note=?, energy_ai=1, "
            "ai_energy_checked=1 WHERE id=?",
            (cost, note, listing_id),
        )
    else:
        conn.execute(
            "UPDATE listings SET ai_energy_checked=1 WHERE id=?", (listing_id,),
        )


def listings_for_ai_factors(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Byty/domy s popisem, u kterych jeste nebezela AI extrakce faktoru."""
    return conn.execute(
        """
        SELECT id, title, description, category FROM listings
        WHERE category IN ('byty','domy')
          AND description IS NOT NULL AND TRIM(description) <> ''
          AND COALESCE(ai_factors_checked, 0) = 0
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()


def set_ai_factors(conn: sqlite3.Connection, listing_id: int,
                   factors_json: str | None, provider: str | None) -> None:
    """Ulozi vysledek AI extrakce faktoru. Priznak ai_factors_checked=1 nastavi vzdy
    (i pri prazdnem vysledku), aby se stejny popis nedotazoval opakovane."""
    conn.execute(
        "UPDATE listings SET ai_factors_json=?, ai_factors_provider=?, "
        "ai_factors_checked=1 WHERE id=?",
        (factors_json, provider, listing_id),
    )


def listings_for_ai_vzhled(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Byty/domy s fotkami, u kterych jeste nebezelo AI posouzeni vzhledu."""
    return conn.execute(
        """
        SELECT id, images_json FROM listings
        WHERE category IN ('byty','domy')
          AND images_json IS NOT NULL AND images_json NOT IN ('', '[]')
          AND COALESCE(ai_vzhled_checked, 0) = 0
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()


def set_ai_vzhled(conn: sqlite3.Connection, listing_id: int,
                  value: str | None, duvod: str | None) -> None:
    """Ulozi vysledek AI posouzeni vzhledu z fotek. ai_vzhled_checked=1 vzdy (i pri
    neuspechu), aby se stejne fotky nedotazovaly opakovane."""
    conn.execute(
        "UPDATE listings SET ai_vzhled=?, ai_vzhled_duvod=?, ai_vzhled_checked=1 WHERE id=?",
        (value, duvod, listing_id),
    )


def fetch_segment(conn: sqlite3.Connection, category: str, offer_type: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM listings WHERE category=? AND offer_type=? ORDER BY id",
        (category, offer_type),
    ).fetchall()


def segment_counts(conn: sqlite3.Connection) -> dict:
    """Pocet aktivnich nabidek podle segmentu (kategorie, typ)."""
    rows = conn.execute(
        "SELECT category, offer_type, COUNT(*) n FROM listings "
        "WHERE status=? GROUP BY category, offer_type", (STAV_AKTIVNI,),
    ).fetchall()
    return {(r["category"], r["offer_type"]): r["n"] for r in rows}


def days_on_market(conn: sqlite3.Connection, listing_id: int) -> Optional[int]:
    """Pocet dni v nabidce. Prednostne od REALNEHO data vlozeni ze zdroje
    (date_added, napr. Sreality 'Vloženo'); jinak od naseho prvniho nalezeni."""
    row = conn.execute(
        "SELECT date_added, first_seen FROM listings WHERE id=?", (listing_id,)
    ).fetchone()
    if not row:
        return None
    base = row["date_added"] or row["first_seen"]
    if not base:
        return None
    try:
        return (date.today() - date.fromisoformat(base[:10])).days
    except Exception:
        return None


def last_run_info(conn: sqlite3.Connection, category: str = None,
                  offer_type: str = None) -> list[sqlite3.Row]:
    """Posledni beh na kazdy zdroj (volitelne v danem segmentu)."""
    if category and offer_type:
        return conn.execute(
            """
            SELECT r.* FROM runs r
            JOIN (SELECT source, MAX(id) mid FROM runs
                  WHERE category=? AND offer_type=? GROUP BY source) m
              ON r.id = m.mid
            ORDER BY r.source
            """, (category, offer_type),
        ).fetchall()
    return conn.execute(
        """
        SELECT r.* FROM runs r
        JOIN (SELECT source, MAX(id) mid FROM runs GROUP BY source) m
          ON r.id = m.mid
        ORDER BY r.source
        """
    ).fetchall()
