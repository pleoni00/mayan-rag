#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# This verified cache is the original Mayan image, without added packages or code.
if test "$(docker image inspect mayan-base:4.12.2 --format '{{.Id}}' 2>/dev/null || true)" = 'sha256:c9988d9822cb01b4c63ba9b027fa45ec1e465eaca10208d91966da1e0ad99eb1'; then
  export MAYAN_IMAGE=mayan-base:4.12.2
fi
exec docker compose --project-name mayan-poc --env-file .env -f compose.yaml "$@"
