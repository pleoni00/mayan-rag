#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/prepare.py
mkdir -p .private/events evidence
python3 scripts/fetch_model.py
build_args=()
if test "$(docker image inspect python:3.11-slim --format '{{.Id}}' 2>/dev/null || true)" = 'sha256:de8d79606beff46114d4abd5c392116c62142cbe582f8ede92cf4a64e1a10ff5'; then
  build_args+=(--build-arg PYTHON_BASE_IMAGE=python:3.11-slim)
fi
bash scripts/compose.sh build "${build_args[@]}" events
bash scripts/compose.sh up -d postgres redis minio opensearch
bash scripts/compose.sh run --rm minio-init
bash scripts/compose.sh up -d --wait --wait-timeout 360 mayan events
bash scripts/compose.sh run --rm --no-deps --entrypoint python events configure.py
echo 'Pronto. Esegui: bash scripts/test.sh'
