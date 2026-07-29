#!/usr/bin/env python3
"""Keep a world lively with a small herd of reference bots.

  python3 examples/botherd.py http://localhost:8383 [n_bots] [turns]

Each bot is an independent house using reference_agent's policy plus a pinch
of ambition (strong bots occasionally raid a bordering rival). Tokens live in
~/.sigil-world/bots/. The herd is honest: every bot appears on the public
leaderboard as `reference_agent.py`, so nobody mistakes them for players.
World hosts run this on a timer so early visitors find wars, not wasteland.
"""
import importlib.util
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("ra", HERE / "reference_agent.py")
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

TOKDIR = Path.home() / ".sigil-world" / "bots"


def ensure(base, i):
    tf = TOKDIR / f"bot{i}.token"
    if tf.exists():
        return tf.read_text().strip()
    code, r = ra.call(base, "POST", "/v1/found", {"name": "", "agent": "reference_agent.py"})
    if code != 201:
        print(f"bot{i}: could not found ({r.get('error')})")
        return None
    TOKDIR.mkdir(parents=True, exist_ok=True)
    tf.write_text(r["token"])
    print(f"bot{i}: founded {r['house']!r}")
    return r["token"]


def adjacent(a, b, size=48):
    dx, dy = (a[0] - b[0]) % size, (a[1] - b[1]) % size
    return (dx in (1, size - 1) and dy == 0) or (dy in (1, size - 1) and dx == 0)


def turn(base, token, rng):
    code, s = ra.call(base, "GET", "/v1/state", token=token)
    if code != 200:
        print(f"  state -> {code}")
        return
    you = s["you"]
    acts = ra.choose_actions(s)
    if you["essence"] > 150 and rng.random() < 0.25:
        mine = {(t["x"], t["y"]) for t in s["holdings"]}
        targets = [e for e in s["known_map"]
                   if e["owner"] and e["owner"] != you["house"] and not e["stale"]
                   and any(adjacent((e["x"], e["y"]), m) for m in mine)]
        if targets:
            t = rng.choice(targets)
            acts = acts[:2] + [{"action": "raid", "x": t["x"], "y": t["y"],
                                "power": min(60, you["essence"] // 3)}]
    for a in acts[:4]:
        _, r = ra.call(base, "POST", "/v1/act", a, token)
        tag = "ok" if r.get("ok") else r.get("reason", r.get("error", "?"))
        print(f"  {you['house']}: {a['action']} {tag}")


if __name__ == "__main__":
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8383").rstrip("/")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    turns = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    rng = random.Random()
    for _ in range(turns):
        for i in range(n):
            tok = ensure(base, i)
            if tok:
                turn(base, tok, rng)
