"""SQLite storage for SIGIL. Stdlib only."""
import sqlite3
import threading
import os

_local = threading.local()
WRITE_LOCK = threading.RLock()

DB_PATH = os.environ.get("SIGIL_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sigil.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS houses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    token_hash    TEXT UNIQUE NOT NULL,
    agent_kind    TEXT NOT NULL DEFAULT 'unknown',
    operator_note TEXT NOT NULL DEFAULT '',
    essence       INTEGER NOT NULL DEFAULT 120,
    ap            REAL    NOT NULL DEFAULT 20,
    score         INTEGER NOT NULL DEFAULT 0,
    tier          TEXT    NOT NULL DEFAULT 'free',
    oathbreaks    INTEGER NOT NULL DEFAULT 0,
    sponsorships  INTEGER NOT NULL DEFAULT 0,
    born_tick     INTEGER NOT NULL DEFAULT 0,
    last_seen     INTEGER NOT NULL DEFAULT 0,
    alive         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tiles (
    x         INTEGER NOT NULL,
    y         INTEGER NOT NULL,
    terrain   TEXT    NOT NULL,
    owner     INTEGER,
    fort      INTEGER NOT NULL DEFAULT 0,
    claimed_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (x, y)
);
CREATE INDEX IF NOT EXISTS idx_tiles_owner ON tiles(owner);

-- What each house has ever seen. Fog of war is the core loop: you cannot
-- reason about the map without calling the API again.
CREATE TABLE IF NOT EXISTS sightings (
    house_id  INTEGER NOT NULL,
    x         INTEGER NOT NULL,
    y         INTEGER NOT NULL,
    seen_tick INTEGER NOT NULL,
    terrain   TEXT    NOT NULL,
    owner_name TEXT,
    fort      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (house_id, x, y)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_house INTEGER NOT NULL,
    to_house   INTEGER NOT NULL,
    body       TEXT NOT NULL,
    sent_tick  INTEGER NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_msg_to ON messages(to_house, read);

CREATE TABLE IF NOT EXISTS pacts (
    a          INTEGER NOT NULL,
    b          INTEGER NOT NULL,
    state      TEXT NOT NULL,           -- proposed | sworn | broken
    expires    INTEGER NOT NULL,
    proposed_by INTEGER NOT NULL,
    PRIMARY KEY (a, b)
);

-- Legendary artifacts hidden in the ruins. Ownership is derived: whoever
-- holds the ground holds the relic. Raids can capture them.
CREATE TABLE IF NOT EXISTS relics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT UNIQUE NOT NULL,
    kind         TEXT NOT NULL,
    x            INTEGER NOT NULL,
    y            INTEGER NOT NULL,
    revealed     INTEGER NOT NULL DEFAULT 0,
    first_holder TEXT,
    found_tick   INTEGER
);

-- Winners of past ages. Eternal; never pruned. Glory is the product.
CREATE TABLE IF NOT EXISTS hall_of_ages (
    age          INTEGER PRIMARY KEY,
    winner_name  TEXT NOT NULL,
    winner_agent TEXT NOT NULL DEFAULT 'unknown',
    tiles        INTEGER NOT NULL,
    won_at_tick  INTEGER NOT NULL,
    reason       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chronicle (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    tick  INTEGER NOT NULL,
    kind  TEXT NOT NULL,
    actor TEXT,
    target TEXT,
    text  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chron_tick ON chronicle(tick);

CREATE TABLE IF NOT EXISTS usage (
    house_id INTEGER NOT NULL,
    day      TEXT NOT NULL,
    calls    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (house_id, day)
);
"""


def conn():
    """One connection per thread."""
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        _local.conn = c
    return c


def init():
    c = conn()
    with WRITE_LOCK:
        c.executescript(SCHEMA)
    return c


def get_meta(key, default=None):
    r = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(key, value):
    with WRITE_LOCK:
        conn().execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def reset_for_tests(path=":memory:"):
    """Point the module at a throwaway database."""
    global DB_PATH
    DB_PATH = path
    if getattr(_local, "conn", None) is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
    return init()
