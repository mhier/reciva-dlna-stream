#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup.sh — Create virtual environment and install dlna-stream.
#
# Usage:
#   ./setup.sh                  # uses python3, creates .venv
#   ./setup.sh /path/to/python  # uses a specific Python interpreter
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${1:-python3}"

echo "==> Setting up dlna-stream in $REPO_DIR"
echo "==> Using Python: $PYTHON ($("$PYTHON" --version))"

# Create virtual environment
VENV_DIR="$REPO_DIR/.venv"
if [ -d "$VENV_DIR" ]; then
    echo "==> Virtual environment already exists at $VENV_DIR"
else
    echo "==> Creating virtual environment at $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Activate and upgrade pip
echo "==> Upgrading pip"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

# Install the package in editable mode with test dependencies
echo "==> Installing dlna-stream and dependencies"
"$VENV_DIR/bin/pip" install -e "$REPO_DIR" --quiet
"$VENV_DIR/bin/pip" install pytest pytest-asyncio --quiet

echo ""
echo "==> Setup complete!"
echo ""
echo "To start the server:"
echo "  $VENV_DIR/bin/dlna-stream --stream-url <URL> [options]"
echo ""
echo "Or use the wrapper script:"
echo "  ./dlna-stream.sh --stream-url <URL> [options]"
echo ""
echo "To run tests:"
echo "  $VENV_DIR/bin/python -m pytest $REPO_DIR/tests/"
