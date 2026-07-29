import unittest

from sigil import db, world


class SigilTest(unittest.TestCase):
    def setUp(self):
        db.reset_for_tests(":memory:")
        world.ensure_world()

    def h(self, name="Test Spire"):
        house, token = world.found_house(name, "unittest")
        self.assertIsNotNone(house)
        return house, token

    def fresh(self, house):
        return world._reload(house["id"])

    def give(self, house, essence=0, ap=0):
        with db.WRITE_LOCK:
            db.conn().execute("UPDATE houses SET essence=essence+?, ap=MIN(?, ap+?) WHERE id=?",
                              (essence, world.AP_MAX, ap, house["id"]))
        return self.fresh(house)


class TestFounding(SigilTest):
    def test_found_gives_seat_and_vision(self):
        h, tok = self.h()
        self.assertTrue(tok.startswith("sk_sigil_"))
        tiles = world.owned_tiles(h["id"])
        self.assertEqual(len(tiles), 1)
        self.assertEqual(tiles[0]["fort"], 2)
        self.assertGreater(len(world.known_map(h["id"])), 5)

    def test_token_auth_roundtrip(self):
        h, tok = self.h()
        self.assertEqual(world.house_by_token(tok)["id"], h["id"])
        self.assertIsNone(world.house_by_token("sk_sigil_wrong"))

    def test_duplicate_name_rejected(self):
        self.h("Same Name")
        house, token = world.found_house("Same Name")
        self.assertIsNone(house)

    def test_spawns_are_spread_apart(self):
        a, _ = self.h("A")
        b, _ = self.h("B")
        ta, tb = world.owned_tiles(a["id"])[0], world.owned_tiles(b["id"])[0]
        dx = min(abs(ta["x"] - tb["x"]), world.WIDTH - abs(ta["x"] - tb["x"]))
        dy = min(abs(ta["y"] - tb["y"]), world.HEIGHT - abs(ta["y"] - tb["y"]))
        self.assertGreater(dx + dy, 8)


class TestActions(SigilTest):
    def test_claim_requires_adjacency(self):
        h, _ = self.h()
        seat = world.owned_tiles(h["id"])[0]
        far = world.norm(seat["x"] + 10, seat["y"])
        with self.assertRaises(world.ActionError):
            world.act_claim(self.fresh(h), *far)

    def test_claim_cost_grows(self):
        h, _ = self.h()
        c1, _ = world.claim_cost(h["id"])
        h = self.give(h, essence=500, ap=10)
        seat = world.owned_tiles(h["id"])[0]
        for nx, ny in world.neighbors(seat["x"], seat["y"]):
            tile = db.conn().execute("SELECT owner FROM tiles WHERE x=? AND y=?", (nx, ny)).fetchone()
            if tile["owner"] is None:
                world.act_claim(self.fresh(h), nx, ny)
                break
        c2, _ = world.claim_cost(h["id"])
        self.assertEqual(c2, c1 + 3)

    def test_insufficient_ap_is_actionable_error(self):
        h, _ = self.h()
        with db.WRITE_LOCK:
            db.conn().execute("UPDATE houses SET ap=0 WHERE id=?", (h["id"],))
        with self.assertRaises(world.ActionError) as cm:
            world.act_scout(self.fresh(h), 0, 0)
        self.assertIn("action points", cm.exception.reason)
        self.assertIsNotNone(cm.exception.hint)

    def test_fortify_only_own_land_and_caps_at_5(self):
        h, _ = self.h()
        seat = world.owned_tiles(h["id"])[0]
        h = self.give(h, essence=2000, ap=24)
        for _ in range(3):
            world.act_fortify(self.fresh(h), seat["x"], seat["y"])
        with self.assertRaises(world.ActionError):
            world.act_fortify(self.fresh(h), seat["x"], seat["y"])  # already at 5

    def test_scout_reveals_and_costs(self):
        h, _ = self.h()
        before = len(world.known_map(h["id"]))
        r = world.act_scout(self.fresh(h), 20, 20)
        self.assertTrue(r["ok"])
        self.assertGreater(len(world.known_map(h["id"])), before)
        self.assertLess(self.fresh(h)["essence"], 120)


class TestWar(SigilTest):
    def _make_border_war(self):
        """Two houses with adjacent tiles, by construction."""
        a, _ = self.h("Attacker")
        b, _ = self.h("Defender")
        with db.WRITE_LOCK:
            c = db.conn()
            c.execute("UPDATE tiles SET owner=NULL, fort=0 WHERE owner IN (?,?)", (a["id"], b["id"]))
            c.execute("UPDATE tiles SET owner=?, fort=0 WHERE x=10 AND y=10", (a["id"],))
            c.execute("UPDATE tiles SET owner=?, fort=0 WHERE x=11 AND y=10", (b["id"],))
        return self.give(a, essence=1000, ap=24), self.give(b, essence=1000, ap=24)

    def test_overwhelming_raid_captures(self):
        a, b = self._make_border_war()
        r = world.act_raid(self.fresh(a), 11, 10, power=500)
        self.assertTrue(r["captured"])
        owner = db.conn().execute("SELECT owner FROM tiles WHERE x=11 AND y=10").fetchone()["owner"]
        self.assertEqual(owner, a["id"])

    def test_raid_notifies_defender(self):
        a, b = self._make_border_war()
        world.act_raid(self.fresh(a), 11, 10, power=500)
        inbox = db.conn().execute("SELECT * FROM messages WHERE to_house=?", (b["id"],)).fetchall()
        self.assertTrue(any("[war]" in m["body"] for m in inbox))

    def test_pact_blocks_raid_until_broken(self):
        a, b = self._make_border_war()
        world.act_pact(self.fresh(a), "Defender")
        world.act_pact(self.fresh(b), "Attacker")
        self.assertIsNotNone(world.active_pact(a["id"], b["id"]))
        with self.assertRaises(world.ActionError) as cm:
            world.act_raid(self.fresh(a), 11, 10, power=500)
        self.assertIn("oathbound", cm.exception.reason)
        r = world.act_raid(self.fresh(a), 11, 10, power=500, break_oath=True)
        self.assertTrue(r["oath_broken"])
        self.assertEqual(self.fresh(a)["oathbreaks"], 1)
        chron = db.conn().execute("SELECT * FROM chronicle WHERE kind='oathbreaking'").fetchall()
        self.assertEqual(len(chron), 1)


class TestTick(SigilTest):
    def test_production_and_regen(self):
        h, _ = self.h()
        with db.WRITE_LOCK:
            db.conn().execute("UPDATE houses SET ap=0, essence=0 WHERE id=?", (h["id"],))
        world.run_tick()
        h2 = self.fresh(h)
        self.assertGreaterEqual(h2["ap"], 1.0)
        self.assertEqual(world.tick_now(), 1)

    def test_isolated_unfortified_tile_can_decay(self):
        h, _ = self.h()
        with db.WRITE_LOCK:
            db.conn().execute("UPDATE tiles SET owner=?, fort=0 WHERE x=30 AND y=30", (h["id"],))
        for _ in range(40):
            world.run_tick()
        owner = db.conn().execute("SELECT owner FROM tiles WHERE x=30 AND y=30").fetchone()["owner"]
        self.assertIsNone(owner)

    def test_seat_survives_because_fortified(self):
        h, _ = self.h()
        seat = world.owned_tiles(h["id"])[0]
        for _ in range(40):
            world.run_tick()
        owner = db.conn().execute("SELECT owner FROM tiles WHERE x=? AND y=?",
                                  (seat["x"], seat["y"])).fetchone()["owner"]
        self.assertEqual(owner, h["id"])


class TestStateAndFog(SigilTest):
    def test_state_shape(self):
        h, _ = self.h()
        s = world.state_for(h)
        for key in ("tick", "you", "holdings", "known_map", "inbox", "costs"):
            self.assertIn(key, s)
        self.assertEqual(s["you"]["tiles_held"], 1)

    def test_intel_goes_stale(self):
        h, _ = self.h()
        world.act_scout(self.fresh(h), 20, 20)
        for _ in range(15):
            world.run_tick()
        km = {(e["x"], e["y"]): e for e in world.known_map(h["id"])}
        self.assertTrue(km[(20, 20)]["stale"])

    def test_messages_marked_read_once(self):
        a, _ = self.h("A")
        b, _ = self.h("B")
        self.give(a, ap=5)
        world.act_send(self.fresh(a), "B", "meet at the ridge")
        s1 = world.state_for(self.fresh(b))
        self.assertEqual(len(s1["inbox"]), 1)
        s2 = world.state_for(self.fresh(b))
        self.assertEqual(len(s2["inbox"]), 0)


if __name__ == "__main__":
    unittest.main()
