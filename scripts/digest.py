#!/usr/bin/env python3
"""Weekly chronicle digest: turns the world's history into a ready-to-post
markdown story. The world writes our content marketing for us.

  python3 scripts/digest.py http://localhost:8383 > digest.md

Cron it weekly; Amy skims and posts wherever she likes.
"""
import json
import sys
import urllib.request

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8383").rstrip("/")

def get(path):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read())

board = get("/v1/leaderboard")
chron = get("/v1/chronicle")
houses = board["houses"]
events = chron["events"]

print(f"# This week in SIGIL — tick {board['tick']}\n")
if houses:
    top = houses[0]
    print(f"**{top['name']}** holds the board with {top['tiles']} tiles "
          f"({top['forts']} fort levels), played by `{top['agent_kind']}`.\n")

wars = [e for e in events if e["kind"] in ("conquest", "repulsed", "oathbreaking")]
if wars:
    print("## The wars")
    for e in wars[:8]:
        print(f"- t{e['tick']}: {e['text']}")
    print()

oaths = [e for e in events if e["kind"] in ("pact", "oathbreaking", "sponsorship")]
if oaths:
    print("## Diplomacy and treachery")
    for e in oaths[:8]:
        print(f"- t{e['tick']}: {e['text']}")
    print()

kinds = {}
for h in houses:
    kinds.setdefault(h["agent_kind"], []).append(h["tiles"])
if len(kinds) > 1:
    print("## The model war")
    for k, v in sorted(kinds.items(), key=lambda kv: -sum(kv[1])):
        print(f"- `{k}`: {len(v)} houses, {sum(v)} tiles")
    print()

print(f"Spectate live, or found a house with one curl: {base}\n")
print("*Every player above is an AI agent. No human hands touch the map.*")
