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
WorkingDirectory=/var/lib/reciva-dlna-stream
User=reciva-dlna
Group=reciva-dlna
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

7. **`WorkingDirectory=/var/lib/reciva-dlna-stream`** — Sets the working directory to the service's state directory. This directory exists on the filesystem as the user's home and can be used for any runtime state files if needed in the future.

8. **`User=reciva-dlna` / `Group=reciva-dlna`** — The service runs under a dedicated system user and group instead of root. The install script creates these automatically. This limits the security impact should the service be compromised.

9. **`NoNewPrivileges=yes`** — Prevents the service and its children from gaining new privileges via `setuid`/`setgid` binaries or `capability` syscalls.

10. **No `StandardOutput=`/`StandardError=`** — systemd captures stdout/stderr into the journal by default. The server already logs via Python's `logging` module which writes to stderr.

### User/Group Configuration

The service runs under the `reciva-dlna` system user and group. These are created automatically by the install script and hardcoded into the systemd unit file via `User=` and `Group=` directives. No administrator intervention is required.

- The user is created with `--system --no-create-home` and its home directory set to `/var/lib/reciva-dlna-stream`.
- The group is also named `reciva-dlna` and is the user's primary group.
- Ownership of installed files:
  - `/usr/local/lib/reciva-dlna/` (venv) → `reciva-dlna:reciva-dlna`
  - `/usr/local/etc/reciva-dlna-stream/` (config) → `reciva-dlna:reciva-dlna`
  - `/etc/systemd/system/reciva-dlna-stream.service` → `root:reciva-dlna`
  - `/etc/default/reciva-dlna-stream` → `root:reciva-dlna`
  - `/var/lib/reciva-dlna-stream/` (state dir) → `reciva-dlna:reciva-dlna`

The user/group creation is idempotent: `getent` is used to check existence before calling `groupadd`/`useradd`.

## Environment File

### Location

The template is shipped at `deploy/systemd/reciva-dlna-stream.default` and should be installed to `/etc/default/reciva-dlna-stream`.

### Format

The file is a shell script sourced by systemd. It defines:

| Variable | Required | Description |
|---|---|---|
| `CLI_ARGS` | Yes | All CLI arguments passed to the server. See below for examples. |

Note: The binary path (`RECIVA_DLNA_BIN`) is substituted directly into the systemd unit file at install time via the `@ENTRY_POINT@` placeholder mechanism.

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

| Step | Action | Detail | Idempotent? |
|------|--------|--------|-------------|
| 1 | Root check | Refuse to run unless `EUID` is 0. Print error and exit 1 if not root. | — |
| 2 | System user/group creation | Create the `reciva-dlna` system user and group using `groupadd --system` + `useradd --system`. Checks with `getent` first to skip if already exists. | ✅ |
| 3 | State directory creation | Create `/var/lib/reciva-dlna-stream/` as the user's home directory. Checked with `[ -d ]` to skip if exists. | ✅ |
| 4 | Virtual environment creation | Create `/usr/local/lib/reciva-dlna-stream/venv/` using `python3 -m venv`. | No (overwrites) |
| 5 | Python package install | Run `${VENV_DIR}/bin/pip install .` from the repository root to install into the venv. | No (re-installs) |
| 6 | Systemd unit install with placeholder substitution | Copy `deploy/systemd/reciva-dlna-stream.service` → `/etc/systemd/system/reciva-dlna-stream.service`, then replace `@ENTRY_POINT@` with the actual venv binary path using `sed`. | No (overwrites) |
| 7 | Environment file install | Copy `deploy/systemd/reciva-dlna-stream.default` → `/etc/default/reciva-dlna-stream`. | No (overwrites) |
| 8 | Config directory setup | Create `/usr/local/etc/reciva-dlna-stream/` (if not present). | ✅ (mkdir -p) |
| 9 | Default config install | Copy `example-config.json` → `/usr/local/etc/reciva-dlna-stream/config.json` (renamed from `example-` prefix). | No (overwrites) |
| 10 | Default CLI_ARGS in env file | Enable the `--config` line in `/etc/default/reciva-dlna-stream` pointing to `/usr/local/etc/reciva-dlna-stream/config.json`. The script does this by uncommenting and editing the appropriate line in place. | No (re-applies) |
| 11 | Daemon reload | Run `systemctl daemon-reload`. | ✅ (safe) |
| 12 | Enable service | Run `systemctl enable reciva-dlna-stream`. | ✅ (idempotent) |
| 13 | Fix file ownership | `chown -R` venv and config dirs to `reciva-dlna:reciva-dlna`; set unit/env file ownership to `root:reciva-dlna` with `chmod 644`. | ✅ (re-applies same) |

### Idempotency

The install script is designed to be re-runnable. Operations that skip or safely re-apply:

| Check | Behavior on re-run |
|---|---|
| User/group exists (`getent`) | Skipped |
| State directory exists (`[ -d ]`) | Skipped |
| `mkdir -p` | No-op if exists |
| File copy with `cp` | Overwrites (may update to newer version) |
| `sed -i` config substitution | Re-applies the same line |
| `systemctl daemon-reload` | Safe to run repeatedly |
| `systemctl enable` | Idempotent by design |
| `chown` / `chmod` | Re-applies same permissions |

This means administrators can re-run `deploy/install.sh` after pulling an updated repository to upgrade the service.

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
| `/var/lib/reciva-dlna-stream/` | Service state directory (home of the `reciva-dlna` user) |

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
# [*] System group created: reciva-dlna
# [*] System user created: reciva-dlna
# [*] State directory created: /var/lib/reciva-dlna-stream
# [*] Python package installed.
# [*] Systemd unit installed.
# [*] Environment file installed.
# [*] Default config installed at /usr/local/etc/reciva-dlna-stream/config.json
# [*] Service reciva-dlna-stream enabled.
# [*] File ownership set.
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
