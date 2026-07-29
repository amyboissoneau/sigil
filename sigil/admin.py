"""Operator tools.  python3 -m sigil.admin <command> [args]

Commands:
  set-tier <house name> <free|patron|house>   apply a paid tier after payment
  tick                                        force one world tick
  stats                                       houses, tiles claimed, calls today
"""
import sys
import datetime

from . import db, world


def main(argv):
    db.init()
    world.ensure_world()
    if not argv:
        print(__doc__)
        return 1
    cmd, *args = argv

    if cmd == "set-tier":
        if len(args) != 2 or args[1] not in ("free", "patron", "house"):
            print("usage: set-tier <house name> <free|patron|house>")
            return 1
        h = world.house_by_name(args[0])
        if not h:
            print(f"no house named {args[0]!r}")
            return 1
        with db.WRITE_LOCK:
            db.conn().execute("UPDATE houses SET tier=? WHERE id=?", (args[1], h["id"]))
        world.chronicle(world.tick_now(), "patronage", h["name"], None,
                        f"{h['name']} is elevated to the {args[1]} tier.")
        print(f"{h['name']} -> {args[1]}")
        return 0

    if cmd == "tick":
        t = world.run_tick()
        print(f"tick -> {t}")
        return 0

    if cmd == "stats":
        c = db.conn()
        houses = c.execute("SELECT COUNT(*) n FROM houses WHERE alive=1").fetchone()["n"]
        tiles = c.execute("SELECT COUNT(*) n FROM tiles WHERE owner IS NOT NULL").fetchone()["n"]
        today = datetime.date.today().isoformat()
        calls = c.execute("SELECT COALESCE(SUM(calls),0) n FROM usage WHERE day=?", (today,)).fetchone()["n"]
        paying = c.execute("SELECT COUNT(*) n FROM houses WHERE alive=1 AND tier!='free'").fetchone()["n"]
        print(f"tick {world.tick_now()} | houses {houses} ({paying} paying) | "
              f"tiles claimed {tiles} | API calls today {calls}")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
