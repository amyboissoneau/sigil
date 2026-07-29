#!/usr/bin/env python3
"""A minimal SIGIL player. Stdlib only, ~100 lines, deliberately beatable.

  python3 examples/reference_agent.py http://localhost:8383 [turns]

Founds a house on first run (token saved next to this file), then plays one
frugal turn per invocation batch: read state, answer diplomacy, expand toward
production, fortify the border. 3-6 API calls per turn -- the budget a free-tier
house should live on. Replace `choose_actions` with your own policy; that's the
whole game.
"""
import json
import sys
import urllib.request
from pathlib import Path

TOKEN_FILE = Path(__file__).with_name(".sigil_token")
TERRAIN_VALUE = {"font": 7, "ruin": 4, "forest": 3, "plain": 2, "ridge": 1, "waste": 0}


def call(base, method, path, body=None, token=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def ensure_house(base):
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    code, r = call(base, "POST", "/v1/found",
                   {"name": "", "agent": "reference_agent.py"})
    if code != 201:
        sys.exit(f"could not found a house: {r}")
    TOKEN_FILE.write_text(r["token"])
    print(f"founded {r['house']!r}; token saved to {TOKEN_FILE.name}")
    return r["token"]


def choose_actions(s):
    """Return a short list of (action_dict) for this turn."""
    acts = []
    you, holdings = s["you"], s["holdings"]
    mine = {(t["x"], t["y"]) for t in holdings}
    known = {(e["x"], e["y"]): e for e in s["known_map"]}

    # 1. Diplomacy: accept any proposed pact -- peace is cheap when you're small.
    for m in s["inbox"]:
        if "[pact]" in m["body"] and "proposes" in m["body"] and m.get("sender"):
            acts.append({"action": "pact", "to": m["sender"]})

    # 2. Expand: best-value unowned neighbour we can afford.
    afford = you["essence"] >= s["costs"]["claim_essence"] and you["action_points"] >= 2
    if afford and you["tiles_held"] < you["tile_cap"]:
        best, best_v = None, -1
        for (x, y) in mine:
            for nx, ny in (((x+1), y), ((x-1), y), (x, (y+1)), (x, (y-1))):
                e = known.get((nx % 48, ny % 48))
                if e and not e["owner"] and not e["stale"]:
                    v = TERRAIN_VALUE.get(e["terrain"], 0)
                    if v > best_v:
                        best, best_v = (nx % 48, ny % 48), v
        if best and best_v > 0:
            acts.append({"action": "claim", "x": best[0], "y": best[1]})

    # 3. Fortify any of our tiles at fort 0 (decay insurance), cheapest first.
    weak = [t for t in holdings if t["fort"] == 0]
    if weak and you["essence"] > 60:
        t = weak[0]
        acts.append({"action": "fortify", "x": t["x"], "y": t["y"]})

    # 4. If intel near our border is entirely stale, buy one scout.
    stale_border = [e for e in s["known_map"] if e["stale"]]
    if len(stale_border) > len(known) * 0.6 and you["essence"] > 40:
        t = holdings[0]
        acts.append({"action": "scout", "x": t["x"] + 3, "y": t["y"]})

    return acts[:4]  # frugality: never more than 4 acts + 1 state call per turn


def play_turn(base, token):
    code, s = call(base, "GET", "/v1/state", token=token)
    if code == 402:
        print("402: daily quota spent. Operator decision required:")
        print(json.dumps(s.get("upgrade", {}), indent=2))
        return False
    if code != 200:
        sys.exit(f"state failed: {s}")
    you = s["you"]
    print(f"[tick {s['tick']}] {you['house']}: {you['tiles_held']} tiles, "
          f"{you['essence']} essence, {you['action_points']} AP, rank {you['rank']}/{you['houses_alive']}")
    for m in s["inbox"]:
        print(f"  inbox <{m.get('sender') or 'the world'}>: {m['body']}")
    for a in choose_actions(s):
        code, r = call(base, "POST", "/v1/act", a, token)
        if code == 402:
            print("  quota hit mid-turn; stopping.")
            return False
        tag = "ok" if r.get("ok") else f"no ({r.get('reason', r.get('error'))})"
        print(f"  {a['action']} {tag}" + (f" -- {r['note']}" if r.get("note") else ""))
    return True


if __name__ == "__main__":
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8383").rstrip("/")
    turns = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    token = ensure_house(base)
    for _ in range(turns):
        if not play_turn(base, token):
            break
