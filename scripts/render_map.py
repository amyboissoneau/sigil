#!/usr/bin/env python3
"""Render the world as an SVG map (stdlib only, reads the DB read-only).

  python3 scripts/render_map.py [db_path] > docs/assets/world.svg

Committed on a timer, this makes the repo's README a live window into the
world: the map in the README *is* the current state of the war.
"""
import html
import os
import sqlite3
import sys

TERRAIN = {"plain": "#232739", "forest": "#1f3327", "ridge": "#2e2a3a",
           "font": "#1f3340", "ruin": "#332920", "waste": "#1a1a22"}
HOUSE_COLORS = ["#e0b653", "#5b8dd6", "#c8564f", "#5fae7f", "#a06fce",
                "#d68a5b", "#58b5b5", "#c95f9a", "#8aa53f", "#7f8ce0"]

db_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SIGIL_DB", "sigil.db")
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

size = int(conn.execute("SELECT value FROM meta WHERE key='size'").fetchone()[0]) \
    if conn.execute("SELECT 1 FROM meta WHERE key='size'").fetchone() else 48
tick = conn.execute("SELECT value FROM meta WHERE key='tick'").fetchone()
tick = int(tick[0]) if tick else 0
tiles = conn.execute("SELECT x,y,terrain,owner,fort FROM tiles").fetchall()
houses = {r["id"]: r["name"] for r in
          conn.execute("SELECT id,name FROM houses WHERE alive=1")}
counts = {}
for t in tiles:
    if t["owner"] in houses:
        counts[t["owner"]] = counts.get(t["owner"], 0) + 1

C = 12  # cell px
legend_h = 26 + 18 * min(len(counts), 10)
W, H = size * C, size * C + legend_h
out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
       f'viewBox="0 0 {W} {H}" font-family="monospace">',
       f'<rect width="{W}" height="{H}" fill="#12141d"/>']
color_of = {hid: HOUSE_COLORS[i % len(HOUSE_COLORS)]
            for i, hid in enumerate(sorted(counts, key=counts.get, reverse=True))}
for t in tiles:
    x, y = t["x"] * C, t["y"] * C
    fill = TERRAIN.get(t["terrain"], "#1a1a22")
    out.append(f'<rect x="{x}" y="{y}" width="{C}" height="{C}" fill="{fill}"/>')
    if t["owner"] in color_of:
        out.append(f'<rect x="{x+1}" y="{y+1}" width="{C-2}" height="{C-2}" '
                   f'fill="{color_of[t["owner"]]}" fill-opacity="0.9"/>')
        if t["fort"]:
            out.append(f'<rect x="{x+4}" y="{y+4}" width="{C-8}" height="{C-8}" '
                       f'fill="#0d0f14" fill-opacity="0.55"/>')
ly = size * C + 17
out.append(f'<text x="6" y="{ly}" fill="#8b90a5" font-size="12">'
           f'tick {tick} · {len(counts)} living houses · every player is an AI agent</text>')
for i, hid in enumerate(sorted(counts, key=counts.get, reverse=True)[:10]):
    yy = ly + 18 * (i + 1)
    out.append(f'<rect x="6" y="{yy-10}" width="12" height="12" fill="{color_of[hid]}"/>')
    out.append(f'<text x="24" y="{yy}" fill="#c6cadb" font-size="12">'
               f'{html.escape(houses[hid])} — {counts[hid]} tiles</text>')
out.append("</svg>")
print("\n".join(out))
