#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "First run, creating .venv and installing dependencies..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

if [ ! -f ".env" ]; then
    echo "Missing .env. Copy .env.example to .env and fill OPENAI_API_KEY."
    exit 1
fi

PORT="${SERVER_PORT:-7860}"
echo "Conteur server starting on http://localhost:$PORT"
.venv/bin/uvicorn standalone.server:app --host 0.0.0.0 --port "$PORT" --reload
