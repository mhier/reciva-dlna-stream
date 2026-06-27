# REQ-6: Systemd Service Deployment

| Requirement ID | Title | Status |
|---|---|---|
| REQ-6.1 | Systemd Service Unit | ⬜ Not Started |
| REQ-6.2 | EnvironmentFile Configuration | ⬜ Not Started |
| REQ-6.3 | Restart Policy | ⬜ Not Started |
| REQ-6.4 | Logging to Journald | ⬜ Not Started |
| REQ-6.5 | Installation Instructions | ⬜ Not Started |
| REQ-6.6 | Auto-Start on Boot | ⬜ Not Started |
| REQ-6.7 | Install Script — Root Check | ⬜ Not Started |
| REQ-6.8 | Install Script — GNU-Standard Installation | ⬜ Not Started |
| REQ-6.9 | Install Script — Systemd Service Registration | ⬜ Not Started |
| REQ-6.10 | Install Script — Default Config Setup | ⬜ Not Started |

---

## REQ-6.1: Systemd Service Unit

**Status: ⬜ Not Started**

The project must ship a `reciva-dlna-stream.service` systemd unit file that starts the server as a systemd service.

### Details
- The unit file must be placed in `deploy/systemd/` within the repository.
- It must execute the `reciva-dlna-stream` console_scripts entry point (as defined in `pyproject.toml`).
- The unit must not contain hardcoded stream URLs or configuration — all runtime arguments are supplied via the environment file (REQ-6.2).
- The unit must define a `[Unit]` section with a Description and a `[Service]` section with the execution command, and an `[Install]` section for enablement.
- The `ExecStart` command must invoke the entry point (via `${RECIVA_DLNA_BIN}`) with `$CLI_ARGS`. The `RECIVA_DLNA_BIN` variable is set in the environment file by the install script and points to the virtual environment's entry point (REQ-6.8).

---

## REQ-6.2: EnvironmentFile Configuration

**Status: ⬜ Not Started**

The `.service` file must use `EnvironmentFile=/etc/default/reciva-dlna-stream` to supply CLI arguments.

### Details
- The environment file path shall be `/etc/default/reciva-dlna-stream`.
- The environment file must define `CLI_ARGS` as a shell variable containing the CLI arguments (e.g. `CLI_ARGS="--stream-url http://example.com/stream --name MyRadio"`).
- A template environment file must be shipped in `deploy/systemd/reciva-dlna-stream.default` with all supported CLI arguments documented and commented out.
- Supported CLI arguments that may appear in `CLI_ARGS`: `--stream-url`, `--config`, `--port`, `--bind-ip`, `--name`, `--mime-type`, `--verbose`.
- The environment file must also define `RECIVA_DLNA_BIN` with the path to the venv entry point (set automatically by the install script).
- The environment file may also optionally define `USER` and `GROUP` variables to specify the system user/group under which the service runs.

---

## REQ-6.3: Restart Policy

**Status: ⬜ Not Started**

The unit must set `Restart=on-failure` with `RestartSec=5s` so the server recovers from crashes.

### Details
- `Restart=on-failure`: systemd will restart the service when it exits with a non-zero exit code, is terminated by a signal (not due to systemctl stop), or times out.
- `RestartSec=5s`: wait 5 seconds before restarting to avoid rapid restart loops.
- This ensures the server is resilient to transient failures (e.g. temporary network issues).

---

## REQ-6.4: Logging to Journald

**Status: ⬜ Not Started**

The unit must not redirect stdout/stderr; systemd's default journald capture is used.

### Details
- No `StandardOutput=` or `StandardError=` directives shall be set in the unit file, so systemd captures both streams into the journal by default.
- No additional logging configuration is needed — the server already logs via Python's `logging` module which writes to stderr, which systemd captures.
- Users can view logs with `journalctl -u reciva-dlna-stream`.

---

## REQ-6.5: Installation Instructions

**Status: ⬜ Not Started**

The project must include documentation (in the specification) on how to install the systemd service.

### Details
- Copy the unit file to `/etc/systemd/system/reciva-dlna-stream.service`.
- Copy the environment file template to `/etc/default/reciva-dlna-stream` and edit it to set the desired stream URL and options.
- Run `systemctl daemon-reload` to make systemd aware of the new unit.
- Run `systemctl enable reciva-dlna-stream` to enable auto-start on boot.
- Run `systemctl start reciva-dlna-stream` to start the service immediately.
- Check status with `systemctl status reciva-dlna-stream`.

---

## REQ-6.6: Auto-Start on Boot

**Status: ⬜ Not Started**

The unit must have `WantedBy=multi-user.target` for auto-start on boot.

### Details
- The `[Install]` section must contain `WantedBy=multi-user.target`.
- This ensures the service starts automatically when the system enters multi-user mode (normal boot).
- Combined with `systemctl enable`, this provides auto-start behavior.

---

## REQ-6.7: Install Script — Root Check

**Status: ⬜ Not Started**

There must be an install script (`deploy/install.sh`) that checks it is running with root (EUID 0) permissions before proceeding.

### Details
- Immediately after starting, the script must verify it is run as root (e.g. `[ "$EUID" -eq 0 ]` or `id -u`).
- If not running as root, the script must print an error message and exit with a non-zero exit code.
- The script should advise the user to re-run with `sudo`.

---

## REQ-6.8: Install Script — Virtual Environment Installation

**Status: ⬜ Not Started**

The install script must create a dedicated Python virtual environment and install the package into it, rather than installing system-wide. This is required to comply with PEP 668 (externally-managed-environment), which is enforced by Debian 13+.

### Details
- The virtual environment shall be created at `/usr/local/lib/reciva-dlna-stream/venv/` using `python3 -m venv`.
- The package shall be installed into this venv using the venv's pip: `${VENV_DIR}/bin/pip install .`.
- The entry point path is `${VENV_DIR}/bin/reciva-dlna-stream`.
- The install script must write a `RECIVA_DLNA_BIN` variable pointing to the entry point into the environment file (`/etc/default/reciva-dlna-stream`), so the systemd unit can find the executable.
- Config files go to `/usr/local/etc/reciva-dlna-stream/` (unchanged from GNU standard layout).

---

## REQ-6.9: Install Script — Systemd Service Registration

**Status: ⬜ Not Started**

The install script must copy the systemd unit and environment file into their runtime locations and enable the service.

### Details
- Copy `deploy/systemd/reciva-dlna-stream.service` → `/etc/systemd/system/reciva-dlna-stream.service`.
- Copy `deploy/systemd/reciva-dlna-stream.default` → `/etc/default/reciva-dlna-stream` (as a template; user edits later).
- Run `systemctl daemon-reload`.
- Run `systemctl enable reciva-dlna-stream`.
- The service must not be started automatically by the install script — the user configures the environment file first, then starts manually.

---

## REQ-6.10: Install Script — Default Config Setup

**Status: ⬜ Not Started**

After installation, the example config file must be placed at the standard config location (renamed so it is no longer called "example").

### Details
- The source file is `example-config.json` from the repository root.
- It shall be installed to `/usr/local/etc/reciva-dlna-stream/config.json` (dropping the `example-` prefix).
- The installation script must also update the environment file (`/etc/default/reciva-dlna-stream`) so that the default `CLI_ARGS` references this config file (the `--config` option pointing to `/usr/local/etc/reciva-dlna-stream/config.json`).
- The install script must also write `RECIVA_DLNA_BIN` into the environment file, pointing at the venv entry point (e.g. `/usr/local/lib/reciva-dlna-stream/venv/bin/reciva-dlna-stream`).
- This means the service can be started immediately after installation (the user only needs to edit the config if they want different streams).
