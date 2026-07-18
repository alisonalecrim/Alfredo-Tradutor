#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source engine/.venv/bin/activate
export PYTHONPATH="$ROOT"
exec uvicorn engine.api.main:app --reload --host 127.0.0.1 --port 8765
