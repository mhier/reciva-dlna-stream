# REQ-6: Systemd Service Deployment

| Requirement ID | Title | Status |
|---|---|---|---|
| REQ-6.1 | Systemd Service Unit | ✅ Implemented |
| REQ-6.2 | EnvironmentFile Configuration | ✅ Implemented |
| REQ-6.3 | Restart Policy | ✅ Implemented |
| REQ-6.4 | Logging to Journald | ✅ Implemented |
| REQ-6.5 | Installation Instructions | ✅ Implemented |
| REQ-6.6 | Auto-Start on Boot | ✅ Implemented |
| REQ-6.7 | Install Script — Root Check | ✅ Implemented |
| REQ-6.8 | Install Script — Virtual Environment Installation | ✅ Implemented |
| REQ-6.9 | Install Script — Systemd Service Registration | ✅ Implemented |
| REQ-6.10 | Install Script — Default Config Setup | ✅ Implemented |
| REQ-6.11 | Non-Root Service User | ✅ Implemented |
| REQ-6.12 | Install Script — Idempotency | ✅ Implemented |

---

## REQ-6.1: Systemd Service Unit

**Status: ✅ Implemented**

The project must ship a `reciva-dlna-stream.service` systemd unit file that starts the server as a systemd service.

### Details
- The unit file must be placed in `deploy/systemd/` within the repository.
- It must execute the `reciva-dlna-stream` console_scripts entry point (as defined in `pyproject.toml`).
- The unit must not contain hardcoded stream URLs or configuration — all runtime arguments are supplied via the environment file (REQ-6.2).
- The unit must define a `[Unit]` section with a Description and a `[Service]` section with the execution command, and an `[Install]` section for enablement.
- The `ExecStart` command uses an `@ENTRY_POINT@` placeholder which the install script replaces with the absolute path to the venv entry point. The `$CLI_ARGS` variable is expanded from the environment file.
- The unit file must hardcode `User=reciva-dlna` and `Group=reciva-dlna` to run the service under a dedicated system user (see REQ-6.11).

---

## REQ-6.2: EnvironmentFile Configuration

**Status: ✅ Implemented**

The `.service` file must use `EnvironmentFile=/etc/default/reciva-dlna-stream` to supply CLI arguments.

### Details
- The environment file path shall be `/etc/default/reciva-dlna-stream`.
- The environment file must define `CLI_ARGS` as a shell variable containing the CLI arguments (e.g. `CLI_ARGS="--stream-url http://example.com/stream --name MyRadio"`).
- A template environment file must be shipped in `deploy/systemd/reciva-dlna-stream.default` with all supported CLI arguments documented and commented out.
- Supported CLI arguments that may appear in `CLI_ARGS`: `--stream-url`, `--config`, `--port`, `--bind-ip`, `--name`, `--mime-type`, `--verbose`.
- The path to the entry point binary is NOT defined in the environment file. Instead, it is substituted directly into the systemd unit file at install time via the `@ENTRY_POINT@` placeholder mechanism.
- The environment file does not define `USER` or `GROUP` — the service user/group is hardcoded in the systemd unit file directly (see REQ-6.11).

---

## REQ-6.3: Restart Policy

**Status: ✅ Implemented**

The unit must set `Restart=on-failure` with `RestartSec=5s` so the server recovers from crashes.

### Details
- `Restart=on-failure`: systemd will restart the service when it exits with a non-zero exit code, is terminated by a signal (not due to systemctl stop), or times out.
- `RestartSec=5s`: wait 5 seconds before restarting to avoid rapid restart loops.
- This ensures the server is resilient to transient failures (e.g. temporary network issues).

---

## REQ-6.4: Logging to Journald

**Status: ✅ Implemented**

The unit must not redirect stdout/stderr; systemd's default journald capture is used.

### Details
- No `StandardOutput=` or `StandardError=` directives shall be set in the unit file, so systemd captures both streams into the journal by default.
- No additional logging configuration is needed — the server already logs via Python's `logging` module which writes to stderr, which systemd captures.
- Users can view logs with `journalctl -u reciva-dlna-stream`.

---

## REQ-6.5: Installation Instructions

**Status: ✅ Implemented**

The project must include documentation (in the specification) on how to install the systemd service.

### Details
- Copy the unit file to `/etc/systemd/system/reciva-dlna-stream.service` and substitute `@ENTRY_POINT@` with the actual venv binary path.
- Copy the environment file template to `/etc/default/reciva-dlna-stream` and edit it to set the desired stream URL and options.
- Run `systemctl daemon-reload` to make systemd aware of the new unit.
- Run `systemctl enable reciva-dlna-stream` to enable auto-start on boot.
- Run `systemctl start reciva-dlna-stream` to start the service immediately.
- Check status with `systemctl status reciva-dlna-stream`.

---

## REQ-6.6: Auto-Start on Boot

**Status: ✅ Implemented**

The unit must have `WantedBy=multi-user.target` for auto-start on boot.

### Details
- The `[Install]` section must contain `WantedBy=multi-user.target`.
- This ensures the service starts automatically when the system enters multi-user mode (normal boot).
- Combined with `systemctl enable`, this provides auto-start behavior.

---

## REQ-6.7: Install Script — Root Check

**Status: ✅ Implemented**

There must be an install script (`deploy/install.sh`) that checks it is running with root (EUID 0) permissions before proceeding.

### Details
- Immediately after starting, the script must verify it is run as root (e.g. `[ "$EUID" -eq 0 ]` or `id -u`).
- If not running as root, the script must print an error message and exit with a non-zero exit code.
- The script should advise the user to re-run with `sudo`.

---

## REQ-6.8: Install Script — Virtual Environment Installation

**Status: ✅ Implemented**

The install script must create a dedicated Python virtual environment and install the package into it, rather than installing system-wide. This is required to comply with PEP 668 (externally-managed-environment), which is enforced by Debian 13+.

### Details
- The virtual environment shall be created at `/usr/local/lib/reciva-dlna-stream/venv/` using `python3 -m venv`.
- The package shall be installed into this venv using the venv's pip: `${VENV_DIR}/bin/pip install .`.
- The entry point path is `${VENV_DIR}/bin/reciva-dlna-stream`.
- The install script substitutes the entry point path into the systemd unit file by replacing the `@ENTRY_POINT@` placeholder. This is done during the unit file copy step, so the resulting unit file contains a hardcoded absolute path.
- Config files go to `/usr/local/etc/reciva-dlna-stream/` (unchanged from GNU standard layout).

---

## REQ-6.9: Install Script — Systemd Service Registration

**Status: ✅ Implemented**

The install script must copy the systemd unit and environment file into their runtime locations and enable the service.

### Details
- Copy `deploy/systemd/reciva-dlna-stream.service` → `/etc/systemd/system/reciva-dlna-stream.service` and replace `@ENTRY_POINT@` with the actual venv binary path.
- Copy `deploy/systemd/reciva-dlna-stream.default` → `/etc/default/reciva-dlna-stream` (as a template; user edits later).
- Run `systemctl daemon-reload`.
- Run `systemctl enable reciva-dlna-stream`.
- The service must not be started automatically by the install script — the user configures the environment file first, then starts manually.

---

## REQ-6.10: Install Script — Default Config Setup

**Status: ✅ Implemented**

After installation, the example config file must be placed at the standard config location (renamed so it is no longer called "example").

### Details
- The source file is `example-config.json` from the repository root.
- It shall be installed to `/usr/local/etc/reciva-dlna-stream/config.json` (dropping the `example-` prefix).
- The installation script must also update the environment file (`/etc/default/reciva-dlna-stream`) so that the default `CLI_ARGS` references this config file (the `--config` option pointing to `/usr/local/etc/reciva-dlna-stream/config.json`).
- This means the service can be started immediately after installation (the user only needs to edit the config if they want different streams).

---

## REQ-6.11: Non-Root Service User

**Status: ✅ Implemented**

The server must run under a dedicated non-root system user (`reciva-dlna`) instead of running as root.

### Details
- The systemd unit file must set `User=reciva-dlna` and `Group=reciva-dlna` in the `[Service]` section.
- The install script must create the system user and group if they do not already exist.
- User creation: `useradd --system --no-create-home --gid reciva-dlna --home-dir /var/lib/reciva-dlna-stream --comment "Reciva DLNA Stream Server" reciva-dlna`
- Group creation: `groupadd --system reciva-dlna`
- A state directory at `/var/lib/reciva-dlna-stream/` must be created and owned by `reciva-dlna:reciva-dlna` with permissions `750`.
- The install script must set ownership of all installed files:
  - `/usr/local/lib/reciva-dlna/` (venv) → `reciva-dlna:reciva-dlna`
  - `/usr/local/etc/reciva-dlna-stream/` (config) → `reciva-dlna:reciva-dlna`
  - `/etc/systemd/system/reciva-dlna-stream.service` → `root:reciva-dlna`
  - `/etc/default/reciva-dlna-stream` → `root:reciva-dlna`
  - `/var/lib/reciva-dlna-stream/` (state dir) → `reciva-dlna:reciva-dlna`
- The systemd unit file must also set `WorkingDirectory=/var/lib/reciva-dlna-stream` so the service's working directory is the state dir.

---

## REQ-6.12: Install Script — Idempotency

**Status: ✅ Implemented**

The install script must be idempotent — re-running it must be safe and should only create or fix what is missing or incorrect.

### Details
- User/group creation must check existence (e.g. `getent passwd` / `getent group`) before attempting to create.
- State directory creation must check existence (`[ -d "$STATE_DIR" ]`) before creating.
- File copy operations with `cp` are not idempotent internally but will overwrite with the latest version (acceptable for upgrades).
- `chown`/`chmod` operations are safe to re-run as they re-apply the same permissions.
- `systemctl daemon-reload` and `systemctl enable` are idempotent by design.
- Important variables like user/group names, paths, and comments must not change between runs (remain constant via variable references).
- All operations that could fail on re-run must be guarded (e.g. `getent` check before `useradd`).
