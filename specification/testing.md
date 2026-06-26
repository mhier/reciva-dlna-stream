# Testing Design

## Test Framework
- **pytest** with `pytest-asyncio` (asyncio_mode = auto)
- HTTP client: `aiohttp.ClientSession`
- UPnP client: `async_upnp_client` (AiohttpRequester + UpnpFactory + DmsDevice)

## Test Fixtures (`conftest.py`)

### `fake_radio_server` (aiohttp server)
A minimal HTTP server that serves 16 KB of dummy MP3 data. Serves:
- `GET /radio` → returns `dummy_mp3_data` in 4 KB chunks, has no Content-Length (streaming)

### `dummy_mp3_data` (bytes)
16 KB of fake MP3 data starting with an MPEG frame sync word (`0xFF 0xF3`).

### `stream_forwarder`
Creates a `StreamForwarder` pointing at the fake radio URL.

### `dlna_server` (async fixture)
Uses `start_server()` (same as production) to start the full server with the stream forwarder. The server lifecycle starts the ring buffer background task. Yields a `ServerHandle`.

### `dlna_base_uri`
The base URI of the running server (e.g. `http://127.0.0.1:12345`).

### Multi-stream fixtures

- `stream_forwarder_alt` — second `StreamForwarder` for multi-stream tests
- `dlna_device_class_multi` — `MediaServerDevice` subclass with 2 pre-wired forwarders
- `dlna_server_multi` — full multi-stream server with 2 streams
- `dlna_base_uri_multi` — base URI of the multi-stream server

## Tests (`test_integration.py`)

### `test_reciva_dlna_stream_proxying`
...
### `test_ssdp_location_port`
...

### `test_multi_stream_browse_direct_children`
Multi-stream mode: Browse children of root — verify 2 items returned with correct URLs (`/stream/0`, `/stream/1`) and titles.

### `test_multi_stream_browse_metadata_root`
Multi-stream mode: BrowseMetadata on root container — verify `childCount == "2"`.

### `test_multi_stream_browse_item_metadata`
Multi-stream mode: BrowseMetadata on item "1" — verify title and URL for second stream.

### `test_multi_stream_range_request`
Multi-stream mode: Range requests to `/stream/0` and `/stream/1` — both return correct 206 data.

### `test_multi_stream_full_request`
Multi-stream mode: Full (200) requests to `/stream/0` and `/stream/1`.

### `test_multi_stream_end_of_file`
Multi-stream mode: Synthetic footer served at `/stream/0` and `/stream/1`.

### `test_single_stream_backward_compat_route`
Single-stream mode: legacy `/stream` works as alias.

### `test_multi_stream_no_legacy_route`
Multi-stream mode: legacy `/stream` returns 404 (only indexed routes exist).

## Coverage Summary

| Spec file | Claims | Covered | Missing (intentionally low priority) |
|-----------|--------|---------|--------------------------------------|
| architecture.md | 6 key decisions | 6/6 | — |
| forwarder.md | ~15 claims | 12/15 | Buffer timeout, trim error, auto-reconnect (edge cases) |
| server.md | ~15 claims | 14/15 | Search action returns empty |
| server-lifecycle.md | ~5 claims | 3/5 | Startup ordering (hard to verify externally), SSDP TTL value |
| radio-behavior.md | ~7 claims | 5/7 | Retry behavior (loop simulation), full 128KB probe size |

## Known Gaps (Not Covered by Tests)
- **Buffer timeout** (returns empty bytes): edge case requiring stopping the remote stream mid-test
- **Buffer trimmed error** (ValueError): edge case requiring filling 512 MB buffer
- **Auto-reconnect on stream failure**: the fake radio never fails, and simulating a failure is complex
- **Startup ordering**: the fixture tests the end result (correct port in SSDP) rather than the sequence
- **SSDP TTL = 4**: difficult to test programmatically without root on the test socket
- **Full 128KB probe size**: the radio probes 128KB; the test uses 16KB (limited by dummy data size)

## Fixture Lifecycle
Each test gets a fresh server instance (new port, new forwarder, new UPnP device with unique UDN). The buffer background task starts with the server and is stopped during teardown. Fixture scope is `function` by default (pytest-asyncio default test loop scope).
