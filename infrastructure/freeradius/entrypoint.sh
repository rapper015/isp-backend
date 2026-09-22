#!/bin/sh
set -eu

python3 - <<'PY'
import json
import os

with open("/run/freeradius-aaa.json", "w", encoding="utf-8") as handle:
    json.dump({
        "base_url": os.environ.get("AAA_BASE_URL", "http://aaa-service:8000"),
        "service_key": os.environ["AAA_INTERNAL_API_KEY"],
    }, handle)
os.chmod("/run/freeradius-aaa.json", 0o600)
PY

exec /docker-entrypoint.sh "$@"

