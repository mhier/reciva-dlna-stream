# Testing Design

## Test Framework
- **pytest** with `pytest-asyncio` (asyncio_mode = auto)
- HTTP client: `aiohttp.ClientSession`
- UPnP client: `async_upnp_client` (AiohttpRequester + UpnpFactory + DmsDevice)

## Test Fixtures (`conftest.py`)

### `fake_radio_server` (aiohttp server)
A minimal HTTP server that serves dummy MP3 data **continuously**. Serves:
- `GET /radio` → returns `dummy_mp3_data` in 4 KB chunks in an infinite loop, has no Content-Length (streaming)

### `fake_radio_server_flakey` (aiohttp server)
A minimal HTTP server that serves **limited** dummy MP3 data per connection. Serves:
- `GET /radio/flakey` → returns up to 8 KB of data then ends the stream cleanly
- Used to test buffer auto-reconnect after remote stream end

### `dummy_mp3_data` (bytes)
16 KB of fake MP3 data starting with an MPEG frame sync word (`0xFF 0xF3`).

### `stream_forwarder`
Creates a `StreamForwarder` pointing at the fake radio URL.

### `dlna_server` (async fixture)
Uses `start_server()` (same as production) to start the full server with the stream forwarder. The server lifecycle starts the ring buffer background task. Yields a `ServerHandle`. Uses `make_device_class` from `server.py` (same factory as production).

### `dlna_base_uri`
The base URI of the running server (e.g. `http://127.0.0.1:12345`).

### Multi-stream fixtures

- `stream_forwarder_alt` — second `StreamForwarder` for multi-stream tests
- `dlna_device_class_multi` — `MediaServerDevice` subclass with 2 pre-wired forwarders
- `dlna_server_multi` — full multi-stream server with 2 streams
- `dlna_base_uri_multi` — base URI of the multi-stream server

## Tests (`test_integration.py`) — 30 tests

### Single-stream tests (18)

#### `test_reciva_dlna_stream_proxying`
Starts the full server, makes a full-stream (200) request to `/stream`, reads 8 KB of data, verifies it matches the fake radio source data (first 8 KB of dummy data).

#### `test_stream_stops_when_no_clients`
Verifies that the stream does not block indefinitely after clients disconnect — the forwarder can be started and stopped cleanly.

#### `test_browse_metadata`
Calls `Browse("0", "BrowseMetadata")` via SOAP POST — verifies container metadata in DIDL-Lite response.

#### `test_range_request`
Sends `Range: bytes=0-1023` to `/stream` — verifies 206 status, Content-Range header, and exact byte match.

#### `test_end_of_file_range_request`
Sends `Range: bytes=<footer_start>-<footer_end>` — verifies 206 status and the synthetic ID3v1.1 footer bytes.

#### `test_active_connection_count`
Sends three concurrent range requests — verifies active connection count reaches 3 then drops to 0.

#### `test_fake_content_length_property`
Verifies `StreamForwarder.fake_content_length` property returns the expected value.

#### `test_full_stream_response_headers`
Initiates a full (200) stream, checks response headers: `Content-Type`, `Content-Length`, `Accept-Ranges`, `TransferMode.DLNA.ORG`, `Cache-Control`, `Content-Disposition`.

#### `test_data_consistency_across_connections`
Two separate connections each request `bytes=0-4095` — verifies they get identical data.

#### `test_multi_chunk_range_request`
Range request for `bytes=0-16383` spanning multiple buffer chunks — verifies all 16 KB are returned correctly.

#### `test_connection_manager_actions`
Calls all three ConnectionManager SOAP actions (`GetProtocolInfo`, `GetCurrentConnectionIDs`, `GetCurrentConnectionInfo`) — verifies correct response values.

#### `test_search_action_returns_empty`
Calls the ContentDirectory `Search` action — verifies `Result=""`, `NumberReturned=0`, `TotalMatches=0`.

#### `test_device_xml_valid`
Fetches `/device.xml` and verifies UPnP device fields: device type, friendly name, manufacturer, model name, service list (ContentDirectory, ConnectionManager), SCPD/control/event URLs.

#### `test_ssdp_location_port`
Starts the server, sends an M-SEARCH query via SSDP, captures the LOCATION header from the response, and verifies the port in the URL matches the server's actual HTTP port.

#### `test_buffer_trim_error_returns_416`
Fills the ring buffer past 4 MB by injecting data directly, then sends a range request for byte offset 0 (which has been trimmed). Verifies the server returns `416 Range Not Satisfiable` with a `Content-Range: bytes */...` header.

#### `test_buffer_reconnects_after_stream_end`
Creates a bare `StreamBuffer` pointed at the flakey radio URL (which only serves 8 KB per connection). Verifies that after the first stream ends, the buffer reconnects (after `_RECONNECT_DELAY`) and accumulates more data.

#### `test_buffer_read_timeout_returns_empty`
Creates a bare `StreamBuffer` with no stream source. Calls `read()` with a 0.1s timeout. Verifies that `read()` returns `b""` (empty bytes) instead of blocking or raising an exception.

#### `test_session_closed_after_cancelled_run`
Starts a `StreamBuffer`, verifies that `_session` and `_connector` are created, then calls `stop()` (which cancels `_run()`). Verifies that both `_session` and `_connector` are `None` after cleanup, proving `_close_session()` runs on cancellation.

### Multi-stream tests (8)

#### `test_multi_stream_browse_direct_children`
Multi-stream mode: Browse children of root — verify 2 items returned with correct URLs (`/stream/0`, `/stream/1`) and titles.

#### `test_multi_stream_browse_metadata_root`
Multi-stream mode: BrowseMetadata on root container — verify `childCount == "2"`.

#### `test_multi_stream_browse_item_metadata`
Multi-stream mode: BrowseMetadata on item "1" — verify title and URL for second stream.

#### `test_multi_stream_range_request`
Multi-stream mode: Range requests to `/stream/0` and `/stream/1` — both return correct 206 data.

#### `test_multi_stream_full_request`
Multi-stream mode: Full (200) requests to `/stream/0` and `/stream/1`.

#### `test_multi_stream_end_of_file`
Multi-stream mode: Synthetic footer served at `/stream/0` and `/stream/1`.

#### `test_single_stream_backward_compat_route`
Single-stream mode: legacy `/stream` works as alias.

#### `test_multi_stream_no_legacy_route`
Multi-stream mode: legacy `/stream` returns 404 (only indexed routes exist).

## Coverage Summary

| Spec file | Claims | Covered | Missing (intentionally low priority) |
|-----------|--------|---------|--------------------------------------|
| architecture.md | 7 key decisions | 7/7 | — |
| forwarder.md | ~18 claims | 18/18 | — |
| server.md | ~15 claims | 14/15 | Search action returns empty |
| server-lifecycle.md | ~6 claims | 3/6 | Startup ordering (hard to verify externally), SSDP TTL value, self-contained shutdown via cancel_all inside stop() |
| radio-behavior.md | ~7 claims | 5/7 | Retry behavior (loop simulation), full 128KB probe size |

## Known Gaps (Not Covered by Tests)
- **Startup ordering**: the fixture tests the end result (correct port in SSDP) rather than the sequence
- **Full 128KB probe size**: the radio probes 128KB; the test uses 16KB (limited by dummy data size)
- **Footer early-return optimization**: not directly tested — benefits appear as reduced unnecessary buffer start/stop cycles, which is currently invisible in the test harness
- **Condition-as-sole-lock refactor**: tested implicitly by all existing buffer tests continuing to pass with the simplified synchronization

## Fixture Lifecycle
Each test gets a fresh server instance (new port, new forwarder, new UPnP device with unique UDN). The buffer background task starts with the server and is stopped during teardown. Fixture scope is `function` by default (pytest-asyncio default test loop scope).

## Implementation Status

**Status: CHANGED** — Coverage summary and known gaps have been updated to
reflect the new spec changes.

| Aspect | Status |
|--------|--------|
| All existing 30 tests | Implemented (passing) |
| Coverage summary matches updated spec claims | **Spec changed — coverage doc updated, tests unchanged** |
| `make_device_class` in `conftest.py` uses shared factory from `server.py` | **Spec changed, code not updated** |
