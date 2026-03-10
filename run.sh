#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

# Create venv if it doesn't exist
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "Creating virtual environment…"
    python3 -m venv "$VENV"
fi

# Install/upgrade dependencies if requirements.txt is newer than the venv marker
MARKER="$VENV/.deps_installed"
if [[ ! -f "$MARKER" || "$SCRIPT_DIR/requirements.txt" -nt "$MARKER" ]]; then
    echo "Installing dependencies…"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
    touch "$MARKER"
fi

exec "$VENV/bin/python" "$SCRIPT_DIR/main.py" "$@"
