#!/bin/sh
# Run the demo without Docker: the API that asks the library (demo/api.py) and
# the page that draws the map (demo/web), until Ctrl-C.
#
#   scripts/demo.sh
#
#   DPC_RADAR_PORT       the port of the page (default 3000)
#   DPC_RADAR_API_PORT   the port of the API (default 8000)
#
# It needs uv and Node 20 or later.
set -eu
cd "$(dirname "$0")/.."

PORT="${DPC_RADAR_PORT:-3000}"
API_PORT="${DPC_RADAR_API_PORT:-8000}"

if ! command -v npm >/dev/null 2>&1; then
    echo "Node is not installed (https://nodejs.org/). Or use Docker: docker compose up" >&2
    exit 1
fi
[ -d demo/web/node_modules ] || (cd demo/web && npm install --no-audit --no-fund)

uv run --all-extras --group demo uvicorn --app-dir demo api:app --port "$API_PORT" &
API=$!
trap 'kill "$API" 2>/dev/null' EXIT INT TERM

echo "The demo: http://localhost:$PORT"
cd demo/web
DPC_RADAR_API="http://127.0.0.1:$API_PORT" npm run dev -- --port "$PORT"
