#!/usr/bin/env bash
# Cross-platform dev bootstrap (used by CI and scripts/)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

pnpm install
(cd services/api && uv sync)
pnpm build
