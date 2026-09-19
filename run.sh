#!/usr/bin/env bash
# Development: API on 8700, Vite dev server on 5173 (proxies /api to 8700).
# Open http://localhost:5173
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; . ./.env; set +a; fi

if [ -z "${YOUTUBE_API_KEY:-}" ] || [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "Note: API keys are not set, so live search will not run."
  echo "      The example still works. Put them in .env to enable search."
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

.venv/bin/python -m uvicorn api.main:app --port 8700 --reload &
( cd web && npm run dev ) &
wait
