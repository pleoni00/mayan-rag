#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence
bash scripts/compose.sh run --rm --no-deps --entrypoint python events test.py 2>&1 | tee evidence/test.log
cp .private/events/test.json evidence/test.json
