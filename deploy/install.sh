#!/usr/bin/env bash
#
# install.sh — Install reciva-dlna-stream onto the system.
#
# This script must be run as root. It:
#   1. Creates a dedicated system user/group for the service
#   2. Creates a virtual environment and installs the Python package into it
#   3. Copies the systemd unit file and environment file
#   4. Sets up the default config (from example-config.json)
#   5. Fixes ownership of all installed files
#   6. Enables the systemd service
#   7. (Re)starts the service so it is active immediately
#
# The script is idempotent — re-running it is safe and will only
# create or fix what is missing or incorrect.
#
# Usage: sudo ./deploy/install.sh
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SERVICE_NAME="reciva-dlna-stream"
SERVICE_USER="reciva-dlna"
SERVICE_GROUP="reciva-dlna"
CONFIG_DIR="/usr/local/etc/${SERVICE_NAME}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/default/${SERVICE_NAME}"
VENV_DIR="/usr/local/lib/${SERVICE_NAME}/venv"
STATE_DIR="/var/lib/${SERVICE_NAME}"
ENTRY_POINT="${VENV_DIR}/bin/reciva-dlna-stream"

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
# 1. Create system user and group (idempotent)
# ---------------------------------------------------------------------------
if ! getent group "$SERVICE_GROUP" > /dev/null 2>&1; then
    groupadd --system "$SERVICE_GROUP"
    echo "[*] System group created: ${SERVICE_GROUP}"
else
    echo "[*] System group already exists: ${SERVICE_GROUP}"
fi

if ! getent passwd "$SERVICE_USER" > /dev/null 2>&1; then
    useradd --system --no-create-home --gid "$SERVICE_GROUP" \
        --home-dir "$STATE_DIR" \
        --comment "Reciva DLNA Stream Server" \
        "$SERVICE_USER"
    echo "[*] System user created: ${SERVICE_USER}"
else
    echo "[*] System user already exists: ${SERVICE_USER}"
fi

# Create state directory for the service user's home
if [ ! -d "$STATE_DIR" ]; then
    mkdir -p "$STATE_DIR"
    chown "${SERVICE_USER}:${SERVICE_GROUP}" "$STATE_DIR"
    chmod 750 "$STATE_DIR"
    echo "[*] State directory created: ${STATE_DIR}"
else
    echo "[*] State directory already exists: ${STATE_DIR}"
fi

# ---------------------------------------------------------------------------
# 2. Create virtual environment and install Python package
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$VENV_DIR")"
python3 -m venv "$VENV_DIR"
cd "$REPO_DIR"
"${VENV_DIR}/bin/pip" install .
echo "[*] Python package installed in virtual environment: ${VENV_DIR}"

# ---------------------------------------------------------------------------
# 3. Install systemd unit file (with placeholder substitution)
# ---------------------------------------------------------------------------
cp "${SCRIPT_DIR}/systemd/${SERVICE_NAME}.service" "$UNIT_FILE"
sed -i "s|@ENTRY_POINT@|${ENTRY_POINT}|g" "$UNIT_FILE"
echo "[*] Systemd unit installed: ${UNIT_FILE}"

# ---------------------------------------------------------------------------
# 4. Install environment file
# ---------------------------------------------------------------------------
cp "${SCRIPT_DIR}/systemd/${SERVICE_NAME}.default" "$ENV_FILE"
echo "[*] Environment file installed: ${ENV_FILE}"

# ---------------------------------------------------------------------------
# 5. Create config directory and install default config
# ---------------------------------------------------------------------------
mkdir -p "$CONFIG_DIR"
cp "${REPO_DIR}/example-config.json" "$CONFIG_FILE"
echo "[*] Default config installed: ${CONFIG_FILE}"

# ---------------------------------------------------------------------------
# 6. Update environment file to use the installed config by default
# ---------------------------------------------------------------------------
# Replace the commented-out --config line with the active one pointing
# to the installed config file.
sed -i "s|^#CLI_ARGS=\"--config /usr/local/etc/reciva-dlna-stream/config.json\"|CLI_ARGS=\"--config ${CONFIG_FILE}\"|" "$ENV_FILE"
echo "[*] Environment file updated with default CLI_ARGS."

# ---------------------------------------------------------------------------
# 7. Reload systemd and enable the service
# ---------------------------------------------------------------------------
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
echo "[*] Service ${SERVICE_NAME} enabled."

# ---------------------------------------------------------------------------
# 8. (Re)start the service
# ---------------------------------------------------------------------------
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl restart "$SERVICE_NAME"
    echo "[*] Service ${SERVICE_NAME} restarted."
else
    systemctl start "$SERVICE_NAME"
    echo "[*] Service ${SERVICE_NAME} started."
fi

# ---------------------------------------------------------------------------
# 9. Fix ownership of installed files (idempotent)
# ---------------------------------------------------------------------------
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$(dirname "$VENV_DIR")"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$CONFIG_DIR"
chown "root:${SERVICE_GROUP}" "$UNIT_FILE"
chmod 644 "$UNIT_FILE"
chown "root:${SERVICE_GROUP}" "$ENV_FILE"
chmod 644 "$ENV_FILE"
echo "[*] File ownership set."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "[*] Installation complete!"
echo ""
echo "    Next steps:"
echo "      1. Edit config:  sudo vi ${CONFIG_FILE}"
echo "      2. Status:       sudo systemctl status ${SERVICE_NAME}"
echo "      3. After config change:  sudo systemctl restart ${SERVICE_NAME}"
echo "      4. Logs:         journalctl -u ${SERVICE_NAME} -f"
