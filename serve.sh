#!/usr/bin/env bash
# Production: build the frontend, then serve everything from ONE process.
# Open http://localhost:8700
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; . ./.env; set +a; fi

echo "Building frontend..."
( cd web && npm install --silent && npm run build )

echo "Serving on http://localhost:${PORT:-8700}"
exec .venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8700}"
