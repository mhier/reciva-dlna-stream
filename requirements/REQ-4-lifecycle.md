# REQ-4: Server Lifecycle & Configuration

| Requirement ID | Title | Status |
|---|---|---|
| REQ-4.1 | Correct Startup Ordering | ✅ Implemented |
| REQ-4.2 | CLI Entry Point | ✅ Implemented |
| REQ-4.3 | Port Auto-Assignment | ✅ Implemented |
| REQ-4.4 | Clean Shutdown | ✅ Implemented |
| REQ-4.5 | Single-Stream Configuration | ✅ Implemented |
| REQ-4.6 | UDP-Style IP Detection | ✅ Implemented |

---

## REQ-4.1: Correct Startup Ordering

**Status: ✅ Implemented**

The server must start its components in the correct order so that SSDP advertisements contain the correct port number.

### Details
The correct startup sequence is:
1. **Determine the HTTP port** — If port 0 is requested, find a free port before creating any UPnP components.
2. **Build the UPnP device** — Create the device with the actual port in its base URL.
3. **Start the HTTP server** — Bind to the determined port.
4. **Start SSDP** — Start the search responder and advertisement announcer (now the LOCATION URL contains the correct port).
5. **Start stream buffers** — Begin reading from the remote internet radio stream into the ring buffers.

The server must not rely on the upstream UPnP library's default startup order, which advertises before the HTTP port is known.

---

## REQ-4.2: CLI Entry Point

**Status: ✅ Implemented**

The server must provide a command-line interface for configuration and starting.

### Details
The server must provide a command-line entry point accepting the following arguments:

Required CLI arguments:
- `--stream-url STR` : URL of the internet radio stream (Icecast/Shoutcast). Mutually exclusive with `--config`.
- `--name STR` : Friendly name of the UPnP device (default: "Internet Radio Stream"). Single-stream only.
- `--mime-type STR` : MIME type of the stream (default: "audio/mpeg"). Single-stream only.
- `--port INT` : HTTP server port (default: 0 = auto-assign).
- `--bind-ip STR` : HTTP bind address (default: "0.0.0.0").
- `--config PATH` : Path to JSON configuration file for multi-stream mode. Mutually exclusive with `--stream-url`.
- `-v` / `--verbose` : Enable debug logging.

---

## REQ-4.3: Port Auto-Assignment

**Status: ✅ Implemented**

When port 0 is specified (or no port is given), the server must find a free TCP port and use it.

### Details
- The server must find a free TCP port, report it in the startup log, and use it for the HTTP server — all before the UPnP device is created and SSDP starts.

---

## REQ-4.4: Clean Shutdown

**Status: ✅ Implemented**

On SIGINT or SIGTERM, the server must shut down cleanly.

### Details
Shutdown sequence:
1. Stop all stream buffers (cancel background reading tasks).
2. Stop the advertisement announcer (send `ssdp:byebye`).
3. Stop the search responder.
4. Clean up the aiohttp AppRunner (close all active connections).
5. Exit with status 0.

---

## REQ-4.5: Single-Stream Configuration

**Status: ✅ Implemented**

The simplest way to use the server is with a single stream URL, name, and MIME type.

### Details
- When `--stream-url` is provided on the command line, the server creates exactly one stream.
- The friendly name and MIME type are configurable via `--name` and `--mime-type`.
- For a single stream, the server must also serve the stream at a shorter, backward-compatible route (e.g. `/stream`) in addition to the indexed route (`/stream/0`).

---

## REQ-4.6: UDP-Style IP Detection

**Status: ✅ Implemented**

The server must auto-detect the local network IP address for use in the SSDP LOCATION URL and ContentDirectory stream URLs.

### Details
- The server must auto-detect its network-facing IP address (not 127.0.0.1).
- The detection method must work correctly even when multiple network interfaces are present.
- The detected IP must be logged at startup.
