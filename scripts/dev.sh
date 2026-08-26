#!/usr/bin/env bash
# Run the API and the web app together.
#
# Two processes rather than one: the backend is a long-running Python service
# and the frontend has its own dev server with hot reload. Keeping them
# separate is also what the deployed shape looks like.
set -euo pipefail
cd "$(dirname "$0")/.."

cleanup() { kill 0; }
trap cleanup EXIT INT TERM

echo "api  -> http://127.0.0.1:8000  (docs at /docs)"
echo "web  -> http://127.0.0.1:3000"
echo

uv run uvicorn apps.api.main:app --reload --port 8000 &
(cd apps/web && npm run dev) &
wait
