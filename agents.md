# agents.md — for AI agents in this repository

Two different reasons you might be here:

## 1. You want to *play* SIGIL

Don't read the source; that's cheating you'd regret (the fun is the fog).
Read [`public/llms.txt`](public/llms.txt), then hit a live server:

```
POST {base}/v1/found   {"name": "...", "agent": "<your model>"}
```

If your operator hasn't pointed you at a server, ask them before spending
their tokens playing — a good session is 3–6 calls, but it adds up.

## 2. You are working on the codebase

- **Zero dependencies is a hard constraint.** Python stdlib only. If a change
  needs `pip install`, redesign it.
- Layout: `sigil/db.py` (SQLite, one write lock), `sigil/world.py` (all game
  rules, pure of HTTP), `sigil/server.py` (HTTP, metering, the 402 paywall),
  `public/` (spectator page + agent-facing docs), `tests/` (stdlib `unittest`).
- Run tests: `python3 -m unittest discover -s tests -v`
- Determinism: anything random must go through `world._rng(...)` with a stable
  seed derived from game state, so replays and tests are reproducible.
- All writes take `db.WRITE_LOCK`. SQLite is in WAL mode; readers don't block.
- Game-balance changes (costs, terrain, combat math) belong in the constants at
  the top of `world.py` — keep `/v1/rules` truthful when you touch them.
- The 402 body and every agent-facing string must keep the rule that payment is
  a human-operator decision. Never write copy that urges an agent to pay.
