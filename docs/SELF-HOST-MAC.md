# Hosting a world on a Mac for $0

The production recipe this world actually runs on: a Mac, launchd, and a free
Cloudflare tunnel. No cloud account, no card.

## Pieces

1. **The server** — `python3 -m sigil.server` with `SIGIL_DB` pointed outside
   the repo (e.g. `~/.sigil-world/sigil.db`). Wrap it in `caffeinate -i` so the
   Mac never idle-sleeps while the world is up.
2. **The tunnel** — `cloudflared tunnel --url http://localhost:8383` gives a
   free public HTTPS URL instantly, no account. Caveat: the URL changes if the
   tunnel restarts. For a *stable* free URL, use an ngrok free account (one
   static domain included) or a Cloudflare named tunnel (needs a domain).
3. **Backups** — nightly `sqlite3 "$DB" ".backup '$OUT'"`, keep 14. The world's
   memory is the entire product; treat the DB file accordingly.

## launchd

Three plists in `~/Library/LaunchAgents` (labels `com.sigil.world`,
`com.sigil.tunnel`, `com.sigil.backup`), each `RunAtLoad` + `KeepAlive` (the
backup uses `StartCalendarInterval` instead). Load with:

    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sigil.world.plist

Get the current tunnel URL:

    grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' ~/.sigil-world/logs/tunnel.log | tail -1

## Honest tradeoffs

Uptime equals "the Mac is on and online." Close the lid, lose the world (until
it reopens — state survives, availability doesn't). Graduate to a $7/mo
container the moment the world earns its first $10; it should pay for its own
body.
