# Server Lifecycle Design

## Purpose
Orchestrate the correct startup order of the HTTP server, stream buffer, and SSDP components. The upstream `async_upnp_client` library creates the device (and sends SSDP advertisements) before starting the HTTP server, which causes SSDP LOCATION URLs to contain port 0 when auto-assigning ports.

## Function: `start_server()`

### Signature
```python
async def start_server(
    device_class: type,              # MediaServerDevice subclass
    local_ip: str,                   # Detected local IP
    http_bind: str,                  # Bind address (e.g. "0.0.0.0")
    http_port: int,                  # Port (0 = auto-assign)
    streams: list[StreamConfig],     # List of stream configurations
    forwarders: Sequence[object],    # One StreamForwarder per stream
) -> ServerHandle
```

### Startup Sequence

```
1. Determine port
   │
   ├── http_port == 0?
   │   ├── Bind temp socket to find free port
   │   └── Release temp socket
   │
   └── Use http_port as-is
   
2. Build UPnP Device
   │
   ├── base_uri = f"http://{local_ip}:{actual_port}"
   ├── Create device instance with base_uri
   ├── Configure services (stream details, host URL)
   └── Build aiohttp Application with all routes:
       ├── GET /device.xml
       ├── GET /{service}_1.xml (SCPD)
       ├── POST /upnp/control/{service} (actions)
       ├── SUBSCRIBE /upnp/event/{service}
       └── UNSUBSCRIBE /upnp/event/{service}
       └── Device routes (→ /stream → forwarder)
       
3. Start HTTP Server
   │
   ├── AppRunner setup
   └── TCPSite on http_bind:actual_port
   
4. Start SSDP
   │
   ├── SsdpSearchResponder (responds to M-SEARCH)
   └── FastSsdpAdvertisementAnnouncer (sends ALL NOTIFY entries every ~5s)
       │
       └── All ~5 NT/USN entries sent at once per interval
           → server visible on first beacon

5. Return ServerHandle(port, responder, announcer, runner, forwarders)

Note: Stream buffers are NOT started during server startup. Each buffer starts
on demand when the first client connects to the corresponding stream, and stops
when the last client disconnects. See forwarder.md for details.
```

### Port Auto-Assignment Bug Fix
The issue with the upstream library: `UpnpServer.async_start()` calls `_create_device()` (which needs the port for the LOCATION URL) before `_async_start_http_server()` (which actually binds the port). When port=0:
1. Device is created with `LOCATION: http://IP:0/device.xml`
2. SSDP starts broadcasting broken URLs
3. Later, HTTP server binds to a real port

**Fix**: Determine the port upfront by temporarily binding a socket to port 0, reading the assigned port, then using that port for everything.

## Class: `ServerHandle`

### Attributes
- `port: int` — The actual HTTP server port
- `_forwarders: list[StreamForwarder]` — All StreamForwarder instances (for buffer lifecycle)
- `_search_responder` — SSDP search responder
- `_advertisement_announcer` — SSDP advertisement announcer
- `_runner` — aiohttp AppRunner

### Methods

#### `async stop()`
Shutdown sequence:
1. Stop all stream buffers (for each forwarder: `forwarder.stop_buffer()`)
2. Stop advertisement announcer (stop sending NOTIFY)
3. Stop search responder (stop responding to M-SEARCH)
4. Cleanup aiohttp AppRunner

## CLI Entry Point (`__main__.py`)

### Argument Parser
| Argument | Default | Description |
|----------|---------|-------------|
| `--stream-url` | — | URL of the internet radio stream (mutually exclusive with `--config`) |
| `--name` | "Internet Radio Stream" | Friendly name (single-stream mode only) |
| `--config` | — | Path to JSON config file listing streams |
| `--port` | 0 (auto-assign) | HTTP server port |
| `--mime-type` | "audio/mpeg" | Stream MIME type (single-stream mode only) |
| `--bind-ip` | "0.0.0.0" | HTTP bind address |
| `--verbose` / `-v` | off | Enable debug logging |

### Flow
1. Detect local IP (via UDP connect to 8.8.8.8:80 or gethostbyname fallback)
2. If `--config` is given:
   - Parse `load_config()` → list of `StreamConfig`
   - Create one `StreamForwarder` per stream
3. If `--stream-url` is given:
   - Build a single `StreamConfig` with `--name` and `--mime-type`
   - Create one `StreamForwarder`
4. Create a custom `MediaServerDevice` subclass with unique UDN and friendly name
5. Call `set_forwarders(forwarders)` on the device class constructor
6. Call `start_server()` with all streams, forwarders, and parameters — this starts the HTTP server, SSDP, and all ring buffers
7. Wait for SIGINT/SIGTERM
8. Call `ServerHandle.stop()` to cleanly shut down (all buffers → SSDP → HTTP)

### SSDP TTL Monkey-Patch
The `async_upnp_client` library hard-codes SSDP multicast TTL = 2, but UPnP Device Architecture v2.0 mandates TTL = 4. The monkey-patch in `__main__.py` overrides `get_ssdp_socket()` to set `IP_MULTICAST_TTL = 4` on the SSDP socket.

### SSDP NOTIFY: `FastSsdpAdvertisementAnnouncer`

The upstream `SsdpAdvertisementAnnouncer` cycles through NT/USN pairs, sending **one** per interval (~5 entries, 30s each → ~150s full cycle). Reciva radios seem to only respond to specific NT/USN entries (e.g. `upnp:rootdevice`), so they miss the server for multiple cycles.

`server_lifecycle.py` provides `FastSsdpAdvertisementAnnouncer`, a subclass that:
- Sets `ANNOUNCE_INTERVAL = timedelta(seconds=5)`
- Replaces the `itertools.cycle` with a plain list of all advertisements
- Overrides `_announce_next()` to send **all** entries at once every 5 seconds

This means every beacon contains every NT/USN combination, so the radio discovers the server on the first beacon it receives.

Network overhead: ~500 bytes × 5 entries / 5s = ~500 bytes/sec — negligible.
