#!/usr/bin/env bash
# Production entry point: build the index if it's missing, then serve.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f "${CITED_INDEX_DIR:-index}/meta.json" ]; then
  python scripts/ingest.py --refresh
fi
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
