# Deployment Design

## Purpose

Document how the `reciva-dlna-stream` server is deployed as a systemd service on Linux, covering the unit file, environment configuration, restart behavior, logging, installation procedure, and automated install script.

## Systemd Service Unit

### Location

The unit file is shipped at `deploy/systemd/reciva-dlna-stream.service` and should be installed to `/etc/systemd/system/reciva-dlna-stream.service`.

### Unit File Contents

```ini
[Unit]
Description=Reciva DLNA Stream Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/default/reciva-dlna-stream
ExecStart=@ENTRY_POINT@ $CLI_ARGS
Restart=on-failure
RestartSec=5s
PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
```

### Key Design Decisions

1. **Python environment**: A dedicated virtual environment is created at `/usr/local/lib/reciva-dlna-stream/venv/` (instead of installing system-wide). This avoids PEP 668 (externally-managed-environment) errors on Debian 13+.

2. **`After=network-online.target`** — Ensures the network is fully up before the service starts, so the server can reach the remote stream URL immediately.

3. **`EnvironmentFile=/etc/default/reciva-dlna-stream`** — All runtime configuration is supplied via an environment file. The unit file itself contains no hardcoded stream URLs or options. This follows the convention used by many Debian/Ubuntu packages.

4. **`ExecStart=@ENTRY_POINT@ $CLI_ARGS`** — The service template uses an `@ENTRY_POINT@` placeholder. The install script replaces it with the absolute path to the venv entry point (`/usr/local/lib/reciva-dlna-stream/venv/bin/reciva-dlna-stream`). This avoids relying on systemd environment variable expansion for the binary path. The `$CLI_ARGS` variable is expanded from the environment file.

5. **`Restart=on-failure` + `RestartSec=5s`** — Automatically restarts the service on non-zero exit codes, signal termination (except `systemctl stop`), or operation timeouts. The 5-second delay prevents rapid restart loops.

6. **`PrivateTmp=yes`** — Provides a private `/tmp` and `/var/tmp` namespace for the service, a moderate hardening measure.

7. **`NoNewPrivileges=yes`** — Prevents the service and its children from gaining new privileges via `setuid`/`setgid` binaries or `capability` syscalls.

8. **No `StandardOutput=`/`StandardError=`** — systemd captures stdout/stderr into the journal by default. The server already logs via Python's `logging` module which writes to stderr.

### User/Group Configuration

The unit file does not hardcode `User=` or `Group=`. These can optionally be set in the environment file via `USER` and `GROUP` variables, but the unit file itself does not reference them. If a non-root user is desired, the administrator should:

1. Create a system user: `sudo useradd --system --no-create-home reciva-dlna`
2. In `/etc/systemd/system/reciva-dlna-stream.service.d/override.conf` (or via `systemctl edit`), add:
   ```
   [Service]
   User=reciva-dlna
   Group=reciva-dlna
   ```

## Environment File

### Location

The template is shipped at `deploy/systemd/reciva-dlna-stream.default` and should be installed to `/etc/default/reciva-dlna-stream`.

### Format

The file is a shell script sourced by systemd. It defines:

| Variable | Required | Description |
|---|---|---|
| `CLI_ARGS` | Yes | All CLI arguments passed to the server. See below for examples. |
| `USER` | No | System user to run the service as (requires a drop-in override in the unit). |
| `GROUP` | No | System group to run the service as (requires a drop-in override in the unit). |

Note: The binary path (`RECIVA_DLNA_BIN`) is no longer an environment variable. It is substituted directly into the systemd unit file at install time via the `@ENTRY_POINT@` placeholder mechanism.

### CLI_ARGS Examples

**Single-stream mode:**
```
CLI_ARGS="--stream-url http://icecast.example.com/stream.mp3 --name \"My Radio\" --mime-type audio/mpeg"
```

**Multi-stream mode:**
```
CLI_ARGS="--config /usr/local/etc/reciva-dlna-stream/config.json"
```

**With additional options:**
```
CLI_ARGS="--stream-url http://icecast.example.com/stream.ogg --name \"My Station\" --port 8888 --verbose"
```

### Supported CLI Arguments

| Argument | Description |
|---|---|
| `--stream-url` | URL of the internet radio stream (single-stream mode) |
| `--name` | Friendly name for the stream (single-stream mode) |
| `--mime-type` | Stream MIME type, e.g. `audio/mpeg` (single-stream mode) |
| `--config` | Path to JSON config file (multi-stream mode) |
| `--port` | HTTP server port (default: auto-assign) |
| `--bind-ip` | HTTP bind address (default: `0.0.0.0`) |
| `--verbose` / `-v` | Enable debug-level logging |

## Journald Logging

Logging requires no additional configuration. The server uses Python's `logging` module which writes log lines to stderr. systemd automatically captures stderr (and stdout) of all services into the journal.

### Viewing Logs

```bash
# Follow logs in real time
journalctl -u reciva-dlna-stream -f

# Show recent logs
journalctl -u reciva-dlna-stream -n 50

# Show logs since last boot
journalctl -u reciva-dlna-stream -b
```

## Install Script

### Purpose

The project ships `deploy/install.sh` — a single bash script that fully installs the server onto a system. It automates the installation procedure described below, converting the manual steps into a single command.

### Behavior

| Step | Action | Detail |
|------|--------|--------|
| 1 | Root check | Refuse to run unless `EUID` is 0. Print error and exit 1 if not root. |
| 2 | Virtual environment creation | Create `/usr/local/lib/reciva-dlna-stream/venv/` using `python3 -m venv`. |
| 3 | Python package install | Run `${VENV_DIR}/bin/pip install .` from the repository root to install into the venv. |
| 4 | Systemd unit install with placeholder substitution | Copy `deploy/systemd/reciva-dlna-stream.service` → `/etc/systemd/system/reciva-dlna-stream.service`, then replace `@ENTRY_POINT@` with the actual venv binary path using `sed`. |
| 5 | Environment file install | Copy `deploy/systemd/reciva-dlna-stream.default` → `/etc/default/reciva-dlna-stream`. |
| 6 | Config directory setup | Create `/usr/local/etc/reciva-dlna-stream/` (if not present). |
| 7 | Default config install | Copy `example-config.json` → `/usr/local/etc/reciva-dlna-stream/config.json` (renamed from `example-` prefix). |
| 8 | Default CLI_ARGS in env file | Enable the `--config` line in `/etc/default/reciva-dlna-stream` pointing to `/usr/local/etc/reciva-dlna-stream/config.json`. The script does this by uncommenting and editing the appropriate line in place. |
| 9 | Daemon reload | Run `systemctl daemon-reload`. |
| 10 | Enable service | Run `systemctl enable reciva-dlna-stream`. |
| 11 | Print success message | Notify the user that installation is complete and they should edit config and start the service. |

### Root Check

The script must check `EUID` (or `id -u`) at the very start, before any write operations:

```bash
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root. Use: sudo $0" >&2
    exit 1
fi
```

### GNU-Standard Directory Layout

The install script follows the GNU coding standards for directory layout:

| Path | Purpose |
|------|---------|
| `/usr/local/lib/reciva-dlna-stream/venv/bin/reciva-dlna-stream` | Console_scripts entry point (inside venv, installed by pip) |
| `/usr/local/etc/reciva-dlna-stream/config.json` | Default multi-stream config file |
| `/usr/local/share/reciva-dlna-stream/` | Reserved for supporting data files (future use) |
| `/etc/systemd/system/reciva-dlna-stream.service` | Systemd unit file |
| `/etc/default/reciva-dlna-stream` | Environment file with CLI_ARGS |

### Pre-Installation Requirements

The user must:
1. Have the project source code checked out.
2. Run `sudo deploy/install.sh` from the repository root.

The script does NOT start the service — the user should edit the config files first, then start manually.

### Example Usage

```bash
# From the repository root
sudo deploy/install.sh
# Output:
# [*] Installing reciva-dlna-stream...
# [*] Python package installed.
# [*] Systemd unit installed.
# [*] Environment file installed.
# [*] Default config installed at /usr/local/etc/reciva-dlna-stream/config.json
# [*] Service reciva-dlna-stream enabled.
# [*] Installation complete!
#
# Next steps:
#   1. Edit config:  sudo vi /usr/local/etc/reciva-dlna-stream/config.json
#   2. Start:        sudo systemctl start reciva-dlna-stream
#   3. Status:       sudo systemctl status reciva-dlna-stream
```

## Manual Installation Procedure

### Steps

1. **Copy the unit file and substitute the entry point path:**
   ```bash
   sudo cp deploy/systemd/reciva-dlna-stream.service /etc/systemd/system/
   sudo sed -i "s|@ENTRY_POINT@|/usr/local/lib/reciva-dlna-stream/venv/bin/reciva-dlna-stream|g" /etc/systemd/system/reciva-dlna-stream.service
   ```

2. **Install and configure the environment file:**
   ```bash
   sudo cp deploy/systemd/reciva-dlna-stream.default /etc/default/reciva-dlna-stream
   sudo vi /etc/default/reciva-dlna-stream  # Edit CLI_ARGS
   ```

3. **Reload systemd:**
   ```bash
   sudo systemctl daemon-reload
   ```

4. **Enable auto-start on boot:**
   ```bash
   sudo systemctl enable reciva-dlna-stream
   ```

5. **Start the service:**
   ```bash
   sudo systemctl start reciva-dlna-stream
   ```

6. **Verify status:**
   ```bash
   systemctl status reciva-dlna-stream
   ```

### Modifying Configuration

After changing `/etc/default/reciva-dlna-stream`, restart the service:

```bash
sudo systemctl restart reciva-dlna-stream
```

## Restart Behavior

The service uses `Restart=on-failure` with `RestartSec=5s`. systemd will restart the service when:

- The process exits with a non-zero exit code.
- The process is terminated by a signal (other than signals sent by `systemctl stop` or `systemctl restart`).
- An operation times out.

This ensures the server recovers from transient failures such as:

- Temporary network outage preventing connection to the remote stream.
- Remote stream server temporarily unavailable.
- Resource exhaustion (e.g., file descriptor limit) — though this should not occur under normal operation.

**Note:** `RestartSec=5s` means the service will wait 5 seconds before attempting a restart. If the service fails repeatedly within a short time, systemd's `StartLimitIntervalSec` and `StartLimitBurst` defaults (10 failures in 10 seconds) will eventually stop the service from being restarted to prevent a crash loop.

## Relation to Other Specifications

- The systemd unit calls the same CLI entry point defined in [`server-lifecycle.md`](server-lifecycle.md).
- No changes to the Python codebase are needed — the unit simply wraps the existing `reciva-dlna-stream` command with arguments supplied via the environment file.
- Testing the systemd unit is out of scope for pytest-based integration tests; it is a deployment concern.
