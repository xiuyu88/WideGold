#!/usr/bin/env sh
set -eu

trigger=false
if [ "${1:-}" = "--analysis" ]; then
  trigger=true
elif [ -n "${1:-}" ]; then
  echo "Usage: $0 [--analysis]" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not on PATH" >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example to .env and configure it first." >&2
  exit 2
fi

docker compose config >/dev/null

echo "[1/3] Checking container state..."
docker compose ps

echo "[2/3] Running host-side doctor..."
python_cmd="python3"
command -v "$python_cmd" >/dev/null 2>&1 || python_cmd="python"
if command -v "$python_cmd" >/dev/null 2>&1; then
  "$python_cmd" scripts/doctor.py || {
    echo "ERROR: WideGold doctor reported a required dependency failure." >&2
    exit 2
  }
else
  echo "WARN: Python is not installed on host; skipping host-side doctor."
fi

echo "[3/3] Running containerized smoke test..."
if [ "$trigger" = "true" ]; then
  docker compose --profile tools run --rm \
    -e WIDEGOLD_SMOKE_TRIGGER_ANALYSIS=true \
    smoke
else
  docker compose --profile tools run --rm smoke
fi

echo "WideGold Docker E2E smoke passed."
