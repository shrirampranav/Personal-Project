#!/usr/bin/env bash
# One-command start for macOS / Linux
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "Open http://localhost:8000"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
