#!/bin/bash
# Sets up router_proxy.py's Python environment. Run from anywhere; it cds
# into its own directory so `hybrid-local-router/install.sh` and
# `./install.sh` from inside the directory both work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.9+ first." >&2
  exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Using python3 ${PY_VERSION}"

if [ ! -d .venv ]; then
  echo "Creating virtual environment (.venv)..."
  python3 -m venv .venv
else
  echo "Reusing existing virtual environment (.venv)."
fi

# shellcheck source=/dev/null
source .venv/bin/activate

echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q fastapi "uvicorn[standard]" httpx psutil

# Pydantic's type-hint evaluation needs this shim on Python < 3.10 to
# handle `X | None` style annotations (see README Troubleshooting).
PY_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"
if [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; then
  echo "Python ${PY_VERSION} < 3.10 — installing eval_type_backport shim..."
  pip install -q eval_type_backport
fi

echo ""
echo "Done. Next steps:"
echo ""
echo "  1. Start your local LLM server (Exo, Ollama, LM Studio, vLLM, or llama.cpp)."
echo "  2. Start the proxy:"
echo "       source \"$SCRIPT_DIR/.venv/bin/activate\""
echo "       python3 \"$SCRIPT_DIR/scripts/router_proxy.py\""
echo "  3. Check it found your backend:"
echo "       curl -s http://localhost:8787/health | python3 -m json.tool"
echo "  4. Install the Claude Code plugin itself, if you haven't:"
echo "       cp -r \"$SCRIPT_DIR\" ~/.claude/plugins/hybrid-local-router"
echo ""
echo "See README.md for backend-specific configuration (LOCAL_ENDPOINT, etc)."
