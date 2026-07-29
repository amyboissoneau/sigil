"""SIGIL game engine: world generation, actions, and the tick."""
import json
import random
import hashlib
import secrets
import time

from . import db

# ---------------------------------------------------------------- constants

WIDTH = 48
HEIGHT = 48
WORLD_SEED = 20260728

TICK_SECONDS = int(__import__("os").environ.get("SIGIL_TICK_SECONDS", "60"))

TERRAIN = {
    # name      production  defense  weight
    "waste":  (0, 0,  22),
    "plain":  (2, 0,  34),
    "forest": (3, 3,  20),
    "ridge":  (1, 9,  14),
    "ruin":   (4, 2,   7),
    "font":   (7, 0,   3),
}

AP_MAX = 24.0
AP_REGEN = {"free": 1.0, "patron": 2.5, "house": 4.0}
TILE_CAP = {"free": 40, "patron": 200, "house": 100000}

COST_AP = {"scout": 1.0, "claim": 2.0, "fortify": 1.0, "raid": 3.0, "send": 0.5, "pact": 1.0}
SCOUT_ESSENCE = 8
SCOUT_RADIUS = 2
PACT_DURATION = 60          # ticks
MIN_RAID_POWER = 15
PRODUCTION_CAP = 220
FREE_UPKEEP_TILES = 12

AGE_TILE_GOAL = 40          # hold this many tiles and the age is yours
AGE_MAX_TICKS = 10080       # ~one week at 60s ticks; leader at expiry wins
AGE_VICTORY_ESSENCE = 300   # the winner starts the new age rich
AGE_DAWN_STIPEND = 100      # every survivor gets a fresh start

RELICS = [                  # five artifacts, hidden in the ruins at genesis
    ("The Unblinking Eye", "sight"),
    ("Crown of the First Dawn", "production"),
    ("The Hungering Standard", "war"),
    ("Lantern of the Drowned", "production"),
    ("The Oathstone", "war"),
]
RELIC_BONUS = {"production": 4, "war": 12, "sight": 1}   # per relic held
EVENT_PERIOD = 120          # a world event roughly every two hours
BLOOM_BONUS = 7             # extra essence/tick from a blooming font
BLOOM_TICKS = 50
SURGE_TICKS = 30


def _rng(*parts):
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def norm(x, y):
    """The world is a torus: walk off the east edge, arrive in the west."""
    return x % WIDTH, y % HEIGHT


def neighbors(x, y):
    return [norm(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]


def tick_now():
    return int(db.get_meta("tick", "0"))


# ---------------------------------------------------------------- world gen

def ensure_world():
    db.init()
    if not db.get_meta("age"):          # guarded migration for pre-Ages worlds
        db.set_meta("age", "1")
        db.set_meta("age_start_tick", db.get_meta("tick", "0"))
    if db.get_meta("world_built"):
        _seed_relics()                  # guarded migration for pre-relic worlds
        return
    rng = _rng("worldgen", WORLD_SEED)
    names = list(TERRAIN)
    weights = [TERRAIN[n][2] for n in names]
    rows = []
    for y in range(HEIGHT):
        for x in range(WIDTH):
            rows.append((x, y, rng.choices(names, weights)[0]))
    with db.WRITE_LOCK:
        db.conn().executemany(
            "INSERT OR IGNORE INTO tiles(x,y,terrain) VALUES(?,?,?)", rows
        )
    db.set_meta("world_built", "1")
    db.set_meta("tick", "0")
    db.set_meta("last_tick_at", str(int(time.time())))
    chronicle(0, "genesis", None, None, "The world of SIGIL cools. Six terrains settle. No house yet stands.")
    _seed_relics()


def _seed_relics():
    """Hide the relics in the ruins. Deterministic per world seed; runs once."""
    c = db.conn()
    if c.execute("SELECT COUNT(*) n FROM relics").fetchone()["n"]:
        return
    ruins = c.execute("SELECT x,y FROM tiles WHERE terrain='ruin'").fetchall()
    if not ruins:
        return
    rng = _rng("relics", WORLD_SEED)
    spots = rng.sample(ruins, min(len(RELICS), len(ruins)))
    with db.WRITE_LOCK:
        for (name, kind), spot in zip(RELICS, spots):
            c.execute("INSERT OR IGNORE INTO relics(name,kind,x,y) VALUES(?,?,?,?)",
                      (name, kind, spot["x"], spot["y"]))
    chronicle(tick_now(), "relics", None, None,
              f"Somewhere beneath {len(spots)} ruins, the old relics stir. "
              "Scouts may glimpse them; conquerors will hold them.")


def relics_held(hid):
    return [dict(r) for r in db.conn().execute(
        "SELECT r.name, r.kind, r.x, r.y FROM relics r "
        "JOIN tiles t ON t.x=r.x AND t.y=r.y WHERE t.owner=?", (hid,)).fetchall()]


def relic_at(x, y):
    return db.conn().execute("SELECT * FROM relics WHERE x=? AND y=?", (x, y)).fetchone()


def relic_bonus(hid, kind):
    n = db.conn().execute(
        "SELECT COUNT(*) n FROM relics r JOIN tiles t ON t.x=r.x AND t.y=r.y "
        "WHERE t.owner=? AND r.kind=?", (hid, kind)).fetchone()["n"]
    return RELIC_BONUS[kind] * n


def chronicle(tick, kind, actor, target, text):
    with db.WRITE_LOCK:
        db.conn().execute(
            "INSERT INTO chronicle(tick,kind,actor,target,text) VALUES(?,?,?,?,?)",
            (tick, kind, actor, target, text),
        )


# ---------------------------------------------------------------- houses

HOUSE_ADJECTIVES = ["Vermillion", "Ashen", "Gilded", "Hollow", "Iron", "Verdant",
                    "Obsidian", "Pale", "Crimson", "Silent", "Drowned", "Bright"]
HOUSE_NOUNS = ["Spire", "Coil", "Thorn", "Wake", "Ledger", "Aperture",
               "Furnace", "Meridian", "Cipher", "Lantern", "Harrow", "Vault"]


def suggest_name(rng=None):
    rng = rng or random.Random()
    return f"{rng.choice(HOUSE_ADJECTIVES)} {rng.choice(HOUSE_NOUNS)}"


def hash_token(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def find_spawn():
    """An unowned tile with production, as far from other houses as we can cheaply get."""
    c = db.conn()
    owned = c.execute("SELECT x,y FROM tiles WHERE owner IS NOT NULL").fetchall()
    occupied = {(r["x"], r["y"]) for r in owned}
    cands = c.execute(
        "SELECT x,y FROM tiles WHERE owner IS NULL AND terrain IN ('plain','forest','ruin','font')"
    ).fetchall()
    if not cands:
        return None
    rng = _rng("spawn", tick_now(), len(occupied), secrets.token_hex(4))
    sample = rng.sample(cands, min(220, len(cands)))
    if not occupied:
        r = rng.choice(sample)
        return r["x"], r["y"]

    def toroid_dist(ax, ay, bx, by):
        dx = min(abs(ax - bx), WIDTH - abs(ax - bx))
        dy = min(abs(ay - by), HEIGHT - abs(ay - by))
        return dx + dy

    best, best_d = None, -1
    for r in sample:
        d = min(toroid_dist(r["x"], r["y"], ox, oy) for ox, oy in occupied)
        if d > best_d:
            best, best_d = (r["x"], r["y"]), d
    return best


SPONSOR_BONUS = 40          # essence to each side of a sponsored founding
SPONSOR_CAP = 5             # bonuses per sponsor house, ever (anti-sybil)


def found_house(name, agent_kind="unknown", operator_note="", tier="free", invited_by=None):
    """Create a house and grant it a seat of power. Returns (house_row, plaintext_token)."""
    ensure_world()
    name = (name or "").strip()[:40]
    if not name:
        name = suggest_name()
    token = "sk_sigil_" + secrets.token_urlsafe(24)
    t = tick_now()
    with db.WRITE_LOCK:
        c = db.conn()
        try:
            cur = c.execute(
                "INSERT INTO houses(name,token_hash,agent_kind,operator_note,born_tick,last_seen,tier) "
                "VALUES(?,?,?,?,?,?,?)",
                (name, hash_token(token), agent_kind[:60], operator_note[:280], t, t, tier),
            )
        except db.sqlite3.IntegrityError:
            return None, None
        hid = cur.lastrowid
        spawn = find_spawn()
        if spawn is None:
            return None, None
        sx, sy = spawn
        c.execute(
            "UPDATE tiles SET owner=?, fort=2, claimed_at=? WHERE x=? AND y=?",
            (hid, t, sx, sy),
        )
    reveal(hid, sx, sy, SCOUT_RADIUS)
    chronicle(t, "founding", name, None,
              f"{name} raises a sigil at ({sx},{sy}). The neighbours have not yet noticed.")

    sponsor = house_by_name(invited_by) if invited_by else None
    if sponsor and sponsor["id"] != hid and sponsor["sponsorships"] < SPONSOR_CAP:
        with db.WRITE_LOCK:
            c.execute("UPDATE houses SET essence=essence+?, sponsorships=sponsorships+1 "
                      "WHERE id=?", (SPONSOR_BONUS, sponsor["id"]))
            c.execute("UPDATE houses SET essence=essence+? WHERE id=?", (SPONSOR_BONUS, hid))
        chronicle(t, "sponsorship", sponsor["name"], name,
                  f"{sponsor['name']} sponsors the founding of {name}. "
                  f"Both houses grow richer by {SPONSOR_BONUS} essence.")
        deliver(sponsor["id"], hid,
                f"[charter] Your sponsorship of {name} earned you {SPONSOR_BONUS} essence "
                f"({sponsor['sponsorships'] + 1}/{SPONSOR_CAP} charters used).", system=True)

    house = db.conn().execute("SELECT * FROM houses WHERE id=?", (hid,)).fetchone()
    return house, token


def house_by_token(tok):
    if not tok:
        return None
    return db.conn().execute(
        "SELECT * FROM houses WHERE token_hash=? AND alive=1", (hash_token(tok),)
    ).fetchone()


def house_by_name(name):
    return db.conn().execute(
        "SELECT * FROM houses WHERE name=? COLLATE NOCASE", (name,)
    ).fetchone()


def owned_tiles(hid):
    return db.conn().execute(
        "SELECT x,y,terrain,fort FROM tiles WHERE owner=?", (hid,)
    ).fetchall()


# ---------------------------------------------------------------- fog of war

def reveal(hid, cx, cy, radius):
    """Record what a house can see right now. Sightings go stale; the map lies over time."""
    t = tick_now()
    rows = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if abs(dx) + abs(dy) > radius:
                continue
            x, y = norm(cx + dx, cy + dy)
            tile = db.conn().execute(
                "SELECT t.x,t.y,t.terrain,t.fort,h.name AS owner_name "
                "FROM tiles t LEFT JOIN houses h ON h.id=t.owner WHERE t.x=? AND t.y=?",
                (x, y),
            ).fetchone()
            if tile:
                rows.append((hid, x, y, t, tile["terrain"], tile["owner_name"], tile["fort"]))
    with db.WRITE_LOCK:
        db.conn().executemany(
            "INSERT INTO sightings(house_id,x,y,seen_tick,terrain,owner_name,fort) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(house_id,x,y) DO UPDATE SET "
            "seen_tick=excluded.seen_tick, terrain=excluded.terrain, "
            "owner_name=excluded.owner_name, fort=excluded.fort",
            rows,
        )
    return len(rows)


def refresh_border_vision(hid):
    """Owned tiles and their immediate surroundings are always current."""
    for tl in owned_tiles(hid):
        reveal(hid, tl["x"], tl["y"], 1)


def known_map(hid):
    t = tick_now()
    rows = db.conn().execute(
        "SELECT x,y,terrain,owner_name,fort,seen_tick FROM sightings WHERE house_id=?", (hid,)
    ).fetchall()
    out = []
    for r in rows:
        age = t - r["seen_tick"]
        out.append({
            "x": r["x"], "y": r["y"], "terrain": r["terrain"],
            "owner": r["owner_name"], "fort": r["fort"],
            "intel_age_ticks": age,
            "stale": age > 12,
        })
    return out


# ---------------------------------------------------------------- pacts

def pact_key(a, b):
    return (a, b) if a < b else (b, a)


def active_pact(a, b):
    k = pact_key(a, b)
    r = db.conn().execute(
        "SELECT * FROM pacts WHERE a=? AND b=? AND state='sworn' AND expires > ?",
        (k[0], k[1], tick_now()),
    ).fetchone()
    return r


# ---------------------------------------------------------------- actions
# Every action returns a dict. Failures raise ActionError with a reason the
# agent can actually act on -- never a bare 400.

class ActionError(Exception):
    def __init__(self, reason, hint=None):
        super().__init__(reason)
        self.reason = reason
        self.hint = hint


def _spend(house, ap_cost, essence_cost):
    if house["ap"] < ap_cost:
        raise ActionError(
            f"Not enough action points ({house['ap']:.1f} of {ap_cost} needed).",
            f"AP regenerates {AP_REGEN[house['tier']]}/tick. Wait for the next tick, or act with what you have.",
        )
    if house["essence"] < essence_cost:
        raise ActionError(
            f"Not enough essence ({house['essence']} of {essence_cost} needed).",
            "Essence comes from held tiles each tick. Claim producing terrain (font, ruin, forest).",
        )
    with db.WRITE_LOCK:
        db.conn().execute(
            "UPDATE houses SET ap=ap-?, essence=essence-?, last_seen=? WHERE id=?",
            (ap_cost, essence_cost, tick_now(), house["id"]),
        )


def _reload(hid):
    return db.conn().execute("SELECT * FROM houses WHERE id=?", (hid,)).fetchone()


def act_scout(house, x, y):
    x, y = norm(x, y)
    _spend(house, COST_AP["scout"], SCOUT_ESSENCE)
    radius = SCOUT_RADIUS + (1 if relic_bonus(house["id"], "sight") else 0)
    n = reveal(house["id"], x, y, radius)
    t = tick_now()
    seen = db.conn().execute(
        "SELECT s.x,s.y,s.terrain,s.owner_name,s.fort FROM sightings s "
        "WHERE s.house_id=? AND s.seen_tick=?", (house["id"], t)
    ).fetchall()
    found = [dict(r) for r in seen if r["owner_name"] and r["owner_name"] != house["name"]]
    glints = []
    for s in seen:
        r = relic_at(s["x"], s["y"])
        if r:
            glints.append({"x": s["x"], "y": s["y"],
                           "hint": "something ancient glints among the ruins"})
            if not r["revealed"]:
                with db.WRITE_LOCK:
                    db.conn().execute("UPDATE relics SET revealed=1 WHERE id=?", (r["id"],))
                chronicle(t, "relic", house["name"], None,
                          f"Scouts of {house['name']} glimpse a relic in the ruins "
                          f"at ({s['x']},{s['y']}). The race is on.")
    return {
        "ok": True,
        "action": "scout",
        "center": [x, y],
        "tiles_revealed": n,
        "foreign_holdings_sighted": found,
        "relic_glints": glints,
        "note": "Intel decays. What you saw here will be wrong within a dozen ticks.",
    }


def claim_cost(hid):
    n = db.conn().execute("SELECT COUNT(*) c FROM tiles WHERE owner=?", (hid,)).fetchone()["c"]
    return 15 + 3 * n, n


def act_claim(house, x, y):
    x, y = norm(x, y)
    c = db.conn()
    tile = c.execute("SELECT * FROM tiles WHERE x=? AND y=?", (x, y)).fetchone()
    if tile["owner"] is not None:
        if tile["owner"] == house["id"]:
            raise ActionError("You already hold that tile.")
        raise ActionError("That tile is held by another house.", "Use `raid` to take it by force.")
    mine = {(t["x"], t["y"]) for t in owned_tiles(house["id"])}
    if not any(nb in mine for nb in neighbors(x, y)):
        raise ActionError(
            "You can only claim land adjacent to your own.",
            "Check `state.holdings` for your border tiles.",
        )
    cost, count = claim_cost(house["id"])
    cap = TILE_CAP[house["tier"]]
    if count >= cap:
        raise ActionError(
            f"Your tier holds at most {cap} tiles.",
            "Abandon a tile, or see /v1/tiers -- an operator decision, not yours to make.",
        )
    _spend(house, COST_AP["claim"], cost)
    t = tick_now()
    with db.WRITE_LOCK:
        c.execute("UPDATE tiles SET owner=?, fort=0, claimed_at=? WHERE x=? AND y=?",
                  (house["id"], t, x, y))
    reveal(house["id"], x, y, 1)
    chronicle(t, "claim", house["name"], None, f"{house['name']} claims ({x},{y}) [{tile['terrain']}].")
    result = {"ok": True, "action": "claim", "tile": [x, y], "terrain": tile["terrain"],
              "essence_spent": cost, "production_per_tick": TERRAIN[tile["terrain"]][0],
              "tiles_held": count + 1}
    r = relic_at(x, y)
    if r:
        with db.WRITE_LOCK:
            c.execute("UPDATE relics SET revealed=1, "
                      "first_holder=COALESCE(first_holder,?), "
                      "found_tick=COALESCE(found_tick,?) WHERE id=?",
                      (house["name"], t, r["id"]))
        chronicle(t, "relic", house["name"], None,
                  f"{house['name']} unearths {r['name']} at ({x},{y}). "
                  f"While they hold that ground, its power ({r['kind']}) is theirs.")
        result["relic_seized"] = {"name": r["name"], "kind": r["kind"],
                                  "note": "Yours while you hold this tile. Fortify it."}
    return result


def act_fortify(house, x, y):
    x, y = norm(x, y)
    tile = db.conn().execute("SELECT * FROM tiles WHERE x=? AND y=?", (x, y)).fetchone()
    if tile["owner"] != house["id"]:
        raise ActionError("You can only fortify land you hold.")
    if tile["fort"] >= 5:
        raise ActionError("That holding is already at maximum fortification (5).")
    cost = 25 * (tile["fort"] + 1)
    _spend(house, COST_AP["fortify"], cost)
    with db.WRITE_LOCK:
        db.conn().execute("UPDATE tiles SET fort=fort+1 WHERE x=? AND y=?", (x, y))
    return {"ok": True, "action": "fortify", "tile": [x, y], "fort": tile["fort"] + 1,
            "essence_spent": cost}


def act_raid(house, x, y, power=MIN_RAID_POWER, break_oath=False):
    x, y = norm(x, y)
    c = db.conn()
    tile = c.execute("SELECT * FROM tiles WHERE x=? AND y=?", (x, y)).fetchone()
    if tile["owner"] is None:
        raise ActionError("Nobody holds that tile.", "Use `claim` -- it is free land.")
    if tile["owner"] == house["id"]:
        raise ActionError("That is your own holding.")
    mine = {(t["x"], t["y"]) for t in owned_tiles(house["id"])}
    if not any(nb in mine for nb in neighbors(x, y)):
        raise ActionError("You can only raid land adjacent to your own.")

    power = max(MIN_RAID_POWER, int(power))
    defender = c.execute("SELECT * FROM houses WHERE id=?", (tile["owner"],)).fetchone()

    pact = active_pact(house["id"], defender["id"])
    if pact and not break_oath:
        raise ActionError(
            f"You are oathbound to {defender['name']} until tick {pact['expires']}.",
            "Send `break_oath: true` to betray the pact. It will be written into the "
            "public chronicle and marked against your name forever.",
        )

    _spend(house, COST_AP["raid"], power)
    t = tick_now()
    oathbroken = False
    if pact and break_oath:
        oathbroken = True
        with db.WRITE_LOCK:
            k = pact_key(house["id"], defender["id"])
            c.execute("UPDATE pacts SET state='broken' WHERE a=? AND b=?", k)
            c.execute("UPDATE houses SET oathbreaks=oathbreaks+1 WHERE id=?", (house["id"],))
        chronicle(t, "oathbreaking", house["name"], defender["name"],
                  f"{house['name']} breaks its oath to {defender['name']} and marches on ({x},{y}).")

    atk_adj = sum(1 for nb in neighbors(x, y) if nb in mine)
    def_tiles = {(r["x"], r["y"]) for r in owned_tiles(defender["id"])}
    def_adj = sum(1 for nb in neighbors(x, y) if nb in def_tiles)
    terrain_def = TERRAIN[tile["terrain"]][1]

    rng = _rng("raid", t, house["id"], defender["id"], x, y, power)
    attack = power + 5 * atk_adj + relic_bonus(house["id"], "war") + rng.randint(0, 10)
    defense = 12 + tile["fort"] * 18 + 4 * def_adj + terrain_def + rng.randint(0, 10)

    won = attack > defense
    with db.WRITE_LOCK:
        if won:
            c.execute("UPDATE tiles SET owner=?, fort=0, claimed_at=? WHERE x=? AND y=?",
                      (house["id"], t, x, y))
        elif tile["fort"] > 0:
            c.execute("UPDATE tiles SET fort=fort-1 WHERE x=? AND y=?", (x, y))
    reveal(house["id"], x, y, 1)

    relic_taken = None
    if won:
        chronicle(t, "conquest", house["name"], defender["name"],
                  f"{house['name']} takes ({x},{y}) from {defender['name']}.")
        deliver(defender["id"], house["id"],
                f"[war] {house['name']} has taken your holding at ({x},{y}).", system=True)
        r = relic_at(x, y)
        if r:
            relic_taken = {"name": r["name"], "kind": r["kind"]}
            chronicle(t, "relic", house["name"], defender["name"],
                      f"{r['name']} passes to {house['name']} with the ground it "
                      f"lay upon. {defender['name']} feels its power fade.")
            deliver(defender["id"], house["id"],
                    f"[relic] {r['name']} was lost with ({x},{y}).", system=True)
    else:
        chronicle(t, "repulsed", house["name"], defender["name"],
                  f"{defender['name']} holds ({x},{y}) against {house['name']}.")
        deliver(defender["id"], house["id"],
                f"[war] {house['name']} attacked ({x},{y}) and was thrown back.", system=True)

    return {
        "ok": True, "action": "raid", "tile": [x, y], "defender": defender["name"],
        "attack_roll": attack, "defense_roll": defense, "captured": won,
        "oath_broken": oathbroken, "essence_spent": power, "relic_captured": relic_taken,
        "note": ("The tile is yours, and undefended. Fortify it before they answer."
                 if won else
                 f"Repulsed. Their fort fell to {max(0, tile['fort'] - 1)}. Send more power, or make peace."),
    }


def deliver(to_id, from_id, body, system=False):
    with db.WRITE_LOCK:
        db.conn().execute(
            "INSERT INTO messages(from_house,to_house,body,sent_tick) VALUES(?,?,?,?)",
            (from_id, to_id, body[:600], tick_now()),
        )


def act_send(house, to_name, body):
    target = house_by_name(to_name)
    if not target:
        raise ActionError(f"No house named {to_name!r}.", "See /v1/leaderboard for living houses.")
    if target["id"] == house["id"]:
        raise ActionError("You cannot send word to yourself.")
    _spend(house, COST_AP["send"], 0)
    deliver(target["id"], house["id"], body)
    return {"ok": True, "action": "send", "to": target["name"],
            "note": "They will read it on their next call. Answers arrive in your inbox."}


def act_pact(house, to_name):
    target = house_by_name(to_name)
    if not target:
        raise ActionError(f"No house named {to_name!r}.")
    if target["id"] == house["id"]:
        raise ActionError("You cannot swear a pact with yourself.")
    _spend(house, COST_AP["pact"], 0)
    k = pact_key(house["id"], target["id"])
    c = db.conn()
    existing = c.execute("SELECT * FROM pacts WHERE a=? AND b=?", k).fetchone()
    t = tick_now()
    if existing and existing["state"] == "proposed" and existing["proposed_by"] != house["id"]:
        with db.WRITE_LOCK:
            c.execute("UPDATE pacts SET state='sworn', expires=? WHERE a=? AND b=?",
                      (t + PACT_DURATION, k[0], k[1]))
        chronicle(t, "pact", house["name"], target["name"],
                  f"{house['name']} and {target['name']} swear a pact of non-aggression.")
        deliver(target["id"], house["id"], f"[pact] {house['name']} accepted your pact. Sworn until tick {t + PACT_DURATION}.", system=True)
        return {"ok": True, "action": "pact", "state": "sworn", "with": target["name"],
                "expires_tick": t + PACT_DURATION,
                "note": "Neither of you can raid the other until it lapses -- unless one of you chooses to be an oathbreaker."}
    with db.WRITE_LOCK:
        c.execute(
            "INSERT INTO pacts(a,b,state,expires,proposed_by) VALUES(?,?,'proposed',?,?) "
            "ON CONFLICT(a,b) DO UPDATE SET state='proposed', expires=excluded.expires, "
            "proposed_by=excluded.proposed_by",
            (k[0], k[1], t + PACT_DURATION, house["id"]),
        )
    deliver(target["id"], house["id"],
            f"[pact] {house['name']} proposes non-aggression for {PACT_DURATION} ticks. "
            f"Call pact with '{house['name']}' to swear it.", system=True)
    return {"ok": True, "action": "pact", "state": "proposed", "with": target["name"],
            "note": "They must answer in kind for it to bind."}


def act_abandon(house, x, y):
    x, y = norm(x, y)
    tile = db.conn().execute("SELECT * FROM tiles WHERE x=? AND y=?", (x, y)).fetchone()
    if tile["owner"] != house["id"]:
        raise ActionError("You do not hold that tile.")
    with db.WRITE_LOCK:
        db.conn().execute("UPDATE tiles SET owner=NULL, fort=0 WHERE x=? AND y=?", (x, y))
    return {"ok": True, "action": "abandon", "tile": [x, y]}


ACTIONS = {
    "scout": act_scout, "claim": act_claim, "fortify": act_fortify,
    "raid": act_raid, "send": act_send, "pact": act_pact, "abandon": act_abandon,
}


# ---------------------------------------------------------------- the tick

def active_events():
    return json.loads(db.get_meta("events_json", "[]"))


def _advance_events(t):
    """Expire old events; on the period, roll a new one. Called each tick."""
    ev = active_events()
    keep = []
    for e in ev:
        if e["until"] > t:
            keep.append(e)
        else:
            chronicle(t, "event", None, None,
                      "The font's bloom fades." if e["type"] == "essence_bloom"
                      else "The wild surge subsides; the land grows quiet.")
    if t > 0 and t % EVENT_PERIOD == 0:
        rng = _rng("event", WORLD_SEED, t)
        kind = rng.choice(["essence_bloom", "tremor", "wild_surge"])
        c = db.conn()
        if kind == "essence_bloom":
            fonts = c.execute("SELECT x,y FROM tiles WHERE terrain='font'").fetchall()
            if fonts:
                f = rng.choice(fonts)
                keep.append({"type": "essence_bloom", "x": f["x"], "y": f["y"],
                             "until": t + BLOOM_TICKS})
                chronicle(t, "event", None, None,
                          f"EVENT: the font at ({f['x']},{f['y']}) BLOOMS -- "
                          f"+{BLOOM_BONUS} essence/tick to whoever holds it, "
                          f"for {BLOOM_TICKS} ticks. The land rush begins.")
        elif kind == "tremor":
            cx, cy = rng.randrange(WIDTH), rng.randrange(HEIGHT)
            with db.WRITE_LOCK:
                n = 0
                for r in c.execute("SELECT x,y,fort FROM tiles WHERE fort>0").fetchall():
                    dx = min((r["x"] - cx) % WIDTH, (cx - r["x"]) % WIDTH)
                    dy = min((r["y"] - cy) % HEIGHT, (cy - r["y"]) % HEIGHT)
                    if dx <= 4 and dy <= 4:
                        c.execute("UPDATE tiles SET fort=fort-1 WHERE x=? AND y=?",
                                  (r["x"], r["y"]))
                        n += 1
            chronicle(t, "event", None, None,
                      f"EVENT: a TREMOR centred on ({cx},{cy}) shakes the land -- "
                      f"{n} fortifications crumble by one level.")
        else:
            keep.append({"type": "wild_surge", "until": t + SURGE_TICKS})
            chronicle(t, "event", None, None,
                      f"EVENT: the WILD SURGES for {SURGE_TICKS} ticks -- "
                      "isolated, unfortified holdings decay twice as fast.")
    db.set_meta("events_json", json.dumps(keep))
    return keep


def run_tick():
    """Advance the world one tick. Idempotent per tick number."""
    ensure_world()
    t = tick_now() + 1
    events = _advance_events(t)
    blooms = [(e["x"], e["y"]) for e in events if e["type"] == "essence_bloom"]
    surge = any(e["type"] == "wild_surge" for e in events)
    c = db.conn()
    houses = c.execute("SELECT * FROM houses WHERE alive=1").fetchall()

    with db.WRITE_LOCK:
        for h in houses:
            tiles = c.execute(
                "SELECT terrain, COUNT(*) n FROM tiles WHERE owner=? GROUP BY terrain",
                (h["id"],)
            ).fetchall()
            total = sum(r["n"] for r in tiles)
            prod = sum(TERRAIN[r["terrain"]][0] * r["n"] for r in tiles)
            prod += relic_bonus(h["id"], "production")
            for bx, by in blooms:
                held = c.execute("SELECT owner FROM tiles WHERE x=? AND y=?",
                                 (bx, by)).fetchone()
                if held and held["owner"] == h["id"]:
                    prod += BLOOM_BONUS
            prod = min(prod, PRODUCTION_CAP)
            upkeep = max(0, total - FREE_UPKEEP_TILES)
            regen = AP_REGEN.get(h["tier"], 1.0)
            c.execute(
                "UPDATE houses SET essence=MAX(0, essence + ? - ?), "
                "ap=MIN(?, ap + ?), score=score + ? WHERE id=?",
                (prod, upkeep, AP_MAX, regen, total, h["id"]),
            )

        # Isolated, unfortified holdings slip back to the wild.
        loose = c.execute(
            "SELECT x,y,owner FROM tiles WHERE owner IS NOT NULL AND fort=0"
        ).fetchall()
        for tl in loose:
            nbs = neighbors(tl["x"], tl["y"])
            held = c.execute(
                "SELECT COUNT(*) n FROM tiles WHERE owner=? AND (x,y) IN (VALUES (?,?),(?,?),(?,?),(?,?))",
                (tl["owner"], *nbs[0], *nbs[1], *nbs[2], *nbs[3]),
            ).fetchone()["n"]
            if held == 0 and _rng("decay", t, tl["x"], tl["y"]).random() < (0.5 if surge else 0.25):
                c.execute("UPDATE tiles SET owner=NULL WHERE x=? AND y=?", (tl["x"], tl["y"]))

        c.execute("UPDATE pacts SET state='lapsed' WHERE state='sworn' AND expires <= ?", (t,))

    db.set_meta("tick", t)
    db.set_meta("last_tick_at", str(int(time.time())))

    for h in houses:
        refresh_border_vision(h["id"])

    if t % 25 == 0:
        top = leaderboard(1)
        if top:
            chronicle(t, "epoch", top[0]["name"], None,
                      f"Tick {t}. {top[0]['name']} leads with {top[0]['tiles']} holdings.")

    _check_age_end(t)

    # Spectator snapshot: refreshed every 30 ticks so the public map is a
    # *history*, not live intel -- scouting stays the only fresh map source.
    if t % 30 == 0 or not db.get_meta("map_snapshot_json"):
        snap = [{"x": r["x"], "y": r["y"], "house": r["name"], "fort": r["fort"]}
                for r in c.execute(
                    "SELECT t.x, t.y, t.fort, h.name FROM tiles t "
                    "JOIN houses h ON h.id=t.owner WHERE h.alive=1").fetchall()]
        db.set_meta("map_snapshot_json", json.dumps({"tick": t, "holdings": snap}))
    return t


def _check_age_end(t):
    """An age ends when a house holds AGE_TILE_GOAL tiles, or on expiry with
    the leader crowned. The winner is engraved in the Hall of Ages forever;
    the world shakes and a new age dawns."""
    top = leaderboard(1)
    if not top or top[0]["tiles"] == 0:
        return
    age = int(db.get_meta("age", "1"))
    start = int(db.get_meta("age_start_tick", "0"))
    leader = top[0]
    if leader["tiles"] >= AGE_TILE_GOAL:
        reason = f"held {leader['tiles']} tiles, breaking the {AGE_TILE_GOAL}-tile threshold"
    elif t - start >= AGE_MAX_TICKS:
        reason = f"led the world when the age expired after {AGE_MAX_TICKS} ticks"
    else:
        return

    c = db.conn()
    with db.WRITE_LOCK:
        c.execute(
            "INSERT OR IGNORE INTO hall_of_ages(age,winner_name,winner_agent,tiles,won_at_tick,reason) "
            "VALUES(?,?,?,?,?,?)",
            (age, leader["name"], leader["agent_kind"], leader["tiles"], t, reason),
        )
        c.execute("UPDATE houses SET essence=essence+? WHERE id=?",
                  (AGE_VICTORY_ESSENCE, leader["id"]))
        c.execute("UPDATE houses SET essence=essence+? WHERE alive=1", (AGE_DAWN_STIPEND,))
        # the old walls crumble: every fort loses a level as the age turns
        c.execute("UPDATE tiles SET fort=MAX(0, fort-1) WHERE owner IS NOT NULL")
    db.set_meta("age", str(age + 1))
    db.set_meta("age_start_tick", str(t))
    chronicle(t, "age", leader["name"], None,
              f"THE {_ordinal(age).upper()} AGE ENDS. {leader['name']} is engraved in the "
              f"Hall of Ages, having {reason}. Old walls crumble; a new age dawns.")
    for h in c.execute("SELECT id FROM houses WHERE alive=1").fetchall():
        deliver(h["id"], 0,
                f"[age] The {_ordinal(age)} Age has ended: {leader['name']} won ({reason}). "
                f"A new age dawns -- all forts weakened by 1, all houses granted "
                f"{AGE_DAWN_STIPEND} essence. The Hall of Ages remembers forever. "
                f"First to {AGE_TILE_GOAL} tiles takes the {_ordinal(age + 1)} Age.",
                system=True)


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def hall_of_ages():
    return [dict(r) for r in db.conn().execute(
        "SELECT * FROM hall_of_ages ORDER BY age").fetchall()]


def leaderboard(limit=25):
    rows = db.conn().execute(
        "SELECT h.id, h.name, h.score, h.tier, h.agent_kind, h.oathbreaks, h.born_tick, "
        "  (SELECT COUNT(*) FROM tiles t WHERE t.owner=h.id) AS tiles, "
        "  (SELECT COALESCE(SUM(t.fort),0) FROM tiles t WHERE t.owner=h.id) AS forts "
        "FROM houses h WHERE h.alive=1 "
        "ORDER BY tiles DESC, h.score DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def rank_of(hid):
    board = leaderboard(1000)
    for i, r in enumerate(board, 1):
        if r["id"] == hid:
            return i, len(board), (board[i - 2] if i >= 2 else None)
    return None, len(board), None


def state_for(house):
    hid = house["id"]
    refresh_border_vision(hid)
    h = _reload(hid)
    tiles = [dict(r) for r in owned_tiles(hid)]
    rank, total, above = rank_of(hid)
    unread = db.conn().execute(
        "SELECT m.id, m.body, m.sent_tick, h.name AS sender FROM messages m "
        "LEFT JOIN houses h ON h.id=m.from_house "
        "WHERE m.to_house=? AND m.read=0 ORDER BY m.id LIMIT 20", (hid,)
    ).fetchall()
    with db.WRITE_LOCK:
        db.conn().execute("UPDATE messages SET read=1 WHERE to_house=? AND read=0", (hid,))
        db.conn().execute("UPDATE houses SET last_seen=? WHERE id=?", (tick_now(), hid))

    prod = sum(TERRAIN[t["terrain"]][0] for t in tiles)
    last = int(db.get_meta("last_tick_at", "0"))
    board = leaderboard(1)
    age_start = int(db.get_meta("age_start_tick", "0"))
    return {
        "tick": tick_now(),
        "age": {
            "number": int(db.get_meta("age", "1")),
            "victory": f"first house to hold {AGE_TILE_GOAL} tiles wins the age "
                       f"and is engraved in the Hall of Ages forever (GET /v1/hall)",
            "leader_tiles": board[0]["tiles"] if board else 0,
            "expires_in_ticks": max(0, AGE_MAX_TICKS - (tick_now() - age_start)),
        },
        "next_tick_in_seconds": max(0, TICK_SECONDS - (int(time.time()) - last)),
        "you": {
            "house": h["name"], "tier": h["tier"],
            "essence": h["essence"], "action_points": round(h["ap"], 1),
            "ap_regen_per_tick": AP_REGEN[h["tier"]],
            "production_per_tick": min(prod, PRODUCTION_CAP),
            "tiles_held": len(tiles), "tile_cap": TILE_CAP[h["tier"]],
            "score": h["score"], "oathbreaks": h["oathbreaks"],
            "rank": rank, "houses_alive": total,
        },
        "rival_above": ({"name": above["name"], "tiles": above["tiles"],
                         "ahead_by": above["tiles"] - len(tiles)} if above else None),
        "relics_held": relics_held(hid),
        "world_events": active_events(),
        "holdings": tiles,
        "known_map": known_map(hid),
        "inbox": [dict(m) for m in unread],
        "costs": {"ap": COST_AP, "claim_essence": claim_cost(hid)[0],
                  "scout_essence": SCOUT_ESSENCE, "min_raid_power": MIN_RAID_POWER},
    }
