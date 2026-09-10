#!/usr/bin/env sh
set -eu
python_cmd="python3"
command -v "$python_cmd" >/dev/null 2>&1 || python_cmd="python"
exec "$python_cmd" scripts/release_gate.py "$@"
