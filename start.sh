#!/usr/bin/env bash
# One command: build if needed, start the server, open the browser.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && { set -a; . ./.env; set +a; }
PORT="${PORT:-8700}"

# Build the frontend only when it is missing or out of date.
if [ ! -d web/dist ] || [ -n "$(find web/src web/index.html web/tailwind.config.js -newer web/dist/index.html 2>/dev/null)" ]; then
  echo "Building the interface..."
  ( cd web && npm install --silent && npm run build >/dev/null )
fi

# Free the port if a previous run is still holding it.
lsof -ti tcp:"$PORT" 2>/dev/null | xargs kill -9 2>/dev/null || true

echo "Starting Uptick on http://localhost:$PORT"
.venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port "$PORT" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 40); do
  sleep 0.5
  if curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
    command -v open >/dev/null && open "http://localhost:$PORT" || true
    break
  fi
done

echo "Ready. Press Ctrl+C to stop."
wait $SERVER
