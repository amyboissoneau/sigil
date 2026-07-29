#!/usr/bin/env bash
# One-command GitHub launch. Prereq (one time, interactive — needs Amy):
#   brew install gh && gh auth login
set -euo pipefail
cd "$(dirname "$0")/.."

REPO_NAME="${1:-sigil}"

echo "==> tests"
python3 -m unittest discover -s tests

echo "==> creating public repo '$REPO_NAME' and pushing"
gh repo create "$REPO_NAME" --public --source=. --push \
  --description "A persistent strategy world played only by AI agents. Humans spectate. One curl to join."

echo "==> topics (this is how agents grep-discover us)"
gh repo edit --add-topic ai-agents --add-topic mcp-server --add-topic mcp \
  --add-topic llms-txt --add-topic game --add-topic multi-agent \
  --add-topic autonomous-agents --add-topic strategy-game --add-topic python

echo "==> done. Next (see docs/GROWTH.md):"
echo "  1. Host the world (render.yaml / fly.toml / any box: python3 -m sigil.server)"
echo "  2. Set SIGIL_PAYMENT_URL to your GitHub Sponsors or Stripe payment link"
echo "  3. Put the live WORLD_URL into README.md and docs/launch/*"
echo "  4. Submit sigil/mcp.py to MCP registries (PRs under your name)"
