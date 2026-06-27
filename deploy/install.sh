#!/usr/bin/env bash
#
# install.sh — Install reciva-dlna-stream onto the system.
#
# This script must be run as root. It:
#   1. Installs the Python package into the system environment
#   2. Copies the systemd unit file and environment file
#   3. Sets up the default config (from example-config.json)
#   4. Enables the systemd service (but does NOT start it)
#
# Usage: sudo ./deploy/install.sh
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SERVICE_NAME="reciva-dlna-stream"
CONFIG_DIR="/usr/local/etc/${SERVICE_NAME}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/default/${SERVICE_NAME}"

# Source paths relative to the repository root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# Root check
# ---------------------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root." >&2
    echo "Usage: sudo $0" >&2
    exit 1
fi

echo "[*] Installing ${SERVICE_NAME}..."

# ---------------------------------------------------------------------------
# 1. Install Python package
# ---------------------------------------------------------------------------
cd "$REPO_DIR"
pip install .
echo "[*] Python package installed."

# ---------------------------------------------------------------------------
# 2. Install systemd unit file
# ---------------------------------------------------------------------------
cp "${SCRIPT_DIR}/systemd/${SERVICE_NAME}.service" "$UNIT_FILE"
echo "[*] Systemd unit installed: ${UNIT_FILE}"

# ---------------------------------------------------------------------------
# 3. Install environment file
# ---------------------------------------------------------------------------
cp "${SCRIPT_DIR}/systemd/${SERVICE_NAME}.default" "$ENV_FILE"
echo "[*] Environment file installed: ${ENV_FILE}"

# ---------------------------------------------------------------------------
# 4. Create config directory and install default config
# ---------------------------------------------------------------------------
mkdir -p "$CONFIG_DIR"
cp "${REPO_DIR}/example-config.json" "$CONFIG_FILE"
echo "[*] Default config installed: ${CONFIG_FILE}"

# ---------------------------------------------------------------------------
# 5. Update environment file to use the installed config by default
# ---------------------------------------------------------------------------
# Replace the commented-out --config line with the active one pointing
# to the installed config file.
sed -i "s|^#CLI_ARGS=\"--config /etc/reciva-dlna-stream/config.json\"|CLI_ARGS=\"--config ${CONFIG_FILE}\"|" "$ENV_FILE"
echo "[*] Environment file updated with default CLI_ARGS."

# ---------------------------------------------------------------------------
# 6. Reload systemd and enable the service
# ---------------------------------------------------------------------------
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
echo "[*] Service ${SERVICE_NAME} enabled."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "[*] Installation complete!"
echo ""
echo "    Next steps:"
echo "      1. Edit config:  sudo vi ${CONFIG_FILE}"
echo "      2. Start:        sudo systemctl start ${SERVICE_NAME}"
echo "      3. Status:       sudo systemctl status ${SERVICE_NAME}"
echo "      4. Logs:         journalctl -u ${SERVICE_NAME} -f"
