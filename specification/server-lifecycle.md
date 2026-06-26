# Server Lifecycle Design

## Purpose
Orchestrate the correct startup order of the HTTP server, stream buffer, and SSDP components. The upstream `async_upnp_client` library creates the device (and sends SSDP advertisements) before starting the HTTP server, which causes SSDP LOCATION URLs to contain port 0 when auto-assigning ports.

## Function: `start_server()`

### Signature
```python
async def start_server(
    device_class: type,        # MediaServerDevice subclass
    local_ip: str,             # Detected local IP
    http_bind: str,            # Bind address (e.g. "0.0.0.0")
    http_port: int,            # Port (0 = auto-assign)
    stream_url: str,           # Remote stream URL
    stream_title: str,         # Friendly name
    stream_mime_type: str,     # MIME type
    forwarder: object,         # StreamForwarder instance
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
   └── SsdpAdvertisementAnnouncer (sends NOTIFY every ~30s)
   
5. Start Stream Buffer
   │
   └── forwarder.start_buffer() → background ring buffer task begins reading
   
6. Return ServerHandle(port, responder, announcer, runner, forwarder)
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
- `_forwarder` — StreamForwarder instance (for buffer lifecycle)
- `_search_responder` — SSDP search responder
- `_advertisement_announcer` — SSDP advertisement announcer
- `_runner` — aiohttp AppRunner

### Methods

#### `async stop()`
Shutdown sequence:
1. Stop stream buffer (`forwarder.stop_buffer()`)
2. Stop advertisement announcer (stop sending NOTIFY)
3. Stop search responder (stop responding to M-SEARCH)
4. Cleanup aiohttp AppRunner

## CLI Entry Point (`__main__.py`)

### Argument Parser
| Argument | Default | Description |
|----------|---------|-------------|
| `--stream-url` | (required) | URL of the internet radio stream |
| `--name` | "Internet Radio Stream" | Friendly name |
| `--port` | 0 (auto-assign) | HTTP server port |
| `--mime-type` | "audio/mpeg" | Stream MIME type |
| `--bind-ip` | "0.0.0.0" | HTTP bind address |
| `--verbose` / `-v` | off | Enable debug logging |

### Flow
1. Detect local IP (via UDP connect to 8.8.8.8:80 or gethostbyname fallback)
2. Create `StreamForwarder` with the remote URL
3. Create a custom `MediaServerDevice` subclass with unique UDN and friendly name
4. Call `start_server()` with all parameters — this starts the HTTP server, SSDP, and ring buffer
5. Wait for SIGINT/SIGTERM
6. Call `ServerHandle.stop()` to cleanly shut down (buffer → SSDP → HTTP)

### SSDP TTL Monkey-Patch
The `async_upnp_client` library hard-codes SSDP multicast TTL = 2, but UPnP Device Architecture v2.0 mandates TTL = 4. The monkey-patch in `__main__.py` overrides `get_ssdp_socket()` to set `IP_MULTICAST_TTL = 4` on the SSDP socket.
