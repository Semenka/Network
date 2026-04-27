#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspaces/network-chief}"

mkdir -p "$DEST"
cp -R "$ROOT/openclaw/." "$DEST/"

cat > "$DEST/README.local.md" <<EOF
# Network Chief OpenClaw Workspace

Installed from:

$ROOT

Run from the repository root:

network-chief brief --limit 10 --out data/today.md
EOF

echo "Installed OpenClaw workspace assets to $DEST"
