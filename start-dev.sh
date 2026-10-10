#!/usr/bin/env bash
# Local development: API hot reload, independent Worker, Vite hot reload.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

for command in uv node npm; do
  command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done

export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1

# Secrets are loaded by metadata_service from .env; never source .env as code.
uv sync
if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm ci)
fi
# Python owns real OS PIDs for reliable Windows/Git Bash and Unix tree cleanup.
exec uv run python -m metadata_service.dev
