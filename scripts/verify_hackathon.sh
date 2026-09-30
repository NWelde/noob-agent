#!/usr/bin/env bash
# Run from any directory. No Minecraft connection or provider calls are launched.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null
command -v node >/dev/null
command -v npm >/dev/null
uv sync --locked --group dev --group integrations
npm ci
npm --prefix src/noob_agent/connectors/minecraft_sidecar ci
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
node --test tests/*.test.cjs tests/*.test.mjs
