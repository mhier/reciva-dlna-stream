#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# reciva-dlna-stream.sh — Launch reciva-dlna-stream using the virtual environment.
#
# Usage:
#   ./reciva-dlna-stream.sh --stream-url "https://example.com/radio.mp3" [options]
#
# This script locates the virtual environment created by setup.sh and
# runs reciva-dlna-stream inside it, without requiring the venv to be activated.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Error: virtual environment not found at $VENV_DIR" >&2
    echo "Run ./setup.sh first to create it." >&2
    exit 1
fi

RECIVA_DLNA_STREAM="$VENV_DIR/bin/reciva-dlna-stream"

if [ ! -x "$RECIVA_DLNA_STREAM" ]; then
    echo "Error: reciva-dlna-stream not found in virtual environment." >&2
    echo "Run ./setup.sh to reinstall it." >&2
    exit 1
fi

exec "$RECIVA_DLNA_STREAM" "$@"
