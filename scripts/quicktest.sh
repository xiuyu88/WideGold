#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
live="${1:-}"

echo "[1/3] External Bridge Smoke（不调用 LLM）"
python scripts/external_bridge_smoke.py

echo "[2/3] Docker E2E（默认不触发 Live Analysis）"
./scripts/docker-e2e.sh

echo "[3/3] Release Gate"
./scripts/release-gate.sh --require-docker

if [[ "$live" == "--live-analysis" ]]; then
  echo "警告：将触发真实 Live Analysis，可能消耗 LLM Token。"
  ./scripts/docker-e2e.sh --analysis
fi

echo "WideGold 快速验收完成。"
