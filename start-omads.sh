#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found in PATH." >&2
  echo "Install Python 3.11+ and try again." >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating local virtual environment in .venv..."
  python3 -m venv .venv
fi

if [ ! -x ".venv/bin/pip" ]; then
  echo "The local virtual environment is missing pip. Recreate .venv and try again." >&2
  exit 1
fi

if [ ! -x ".venv/bin/omads" ]; then
  echo "Installing OMADS into the local virtual environment..."
  .venv/bin/pip install -e .
fi

exec .venv/bin/omads gui "$@"
