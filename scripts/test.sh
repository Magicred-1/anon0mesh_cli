#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${ANONMESH_TEST_VENV:-$PROJECT_ROOT/.venv-test}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --quiet -r "$PROJECT_ROOT/requirements-dev.txt"
exec "$VENV_DIR/bin/python" -m pytest "$@"
