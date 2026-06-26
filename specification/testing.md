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

## Tests (`test_integration.py`)

### `test_dlna_stream_proxying`
Full integration test:
1. Discover the DMS via SSDP (async_search)
2. Browse DirectChildren of ContentDirectory → get stream URL
3. Fetch stream data via HTTP (200 OK, from ring buffer)
4. Verify data matches dummy MP3 input

### `test_stream_stops_when_no_clients`
Verify connection cleanup:
1. Read a small chunk from /stream
2. Disconnect
3. Verify a new connection works (old cleanup didn't break the server)

### `test_browse_metadata`
Test BrowseMetadata on root container:
1. Create device from XML
2. Browse "0" with BrowseMetadata
3. Verify container title, class, childCount

### `test_range_request`
Test range request for the main body (served from ring buffer):
1. Send `Range: bytes=0-16383` to /stream
2. Verify 206 status, Content-Range, Content-Length, Accept-Ranges
3. Read data, verify it matches dummy MP3

### `test_end_of_file_range_request`
Test synthetic footer (Reciva end-of-file probe):
1. Send `Range: bytes=<footer_start>-<file_end>` to /stream
2. Verify 206 status, correct Content-Range
3. Read data, verify it matches the synthetic ID3v1 footer
4. First 4 bytes should be `\x00\x54\x41\x47` (0x00 + "TAG")

### `test_active_connection_count`
Verify concurrent connections work:
1. Open two concurrent GET /stream connections
2. Both return 200 OK
3. Both return the same data from the ring buffer

### `test_fake_content_length_property`
Verify `StreamForwarder.fake_content_length` returns the expected constant value.

### `test_full_stream_response_headers`
Verify that a 200 OK response has the correct headers:
1. Content-Type: audio/mpeg
2. Accept-Ranges: bytes
3. TransferMode.DLNA.ORG: Streaming
4. Cache-Control: no-cache
5. Content-Length: fake file size

### `test_data_consistency_across_connections`
Critical for Reciva radios:
1. Request bytes=0-4095 over first HTTP connection → get data1
2. Request bytes=0-4095 over second HTTP connection → get data2
3. Assert data1 == data2 (ring buffer consistency)

### `test_multi_chunk_range_request`
Verify the chunked read loop works:
1. Request range covering all 16 KB of dummy data
2. Verify 206, correct headers, correct data

### `test_connection_manager_actions`
Test all three ConnectionManager:1 actions via raw SOAP:
1. GetProtocolInfo → response contains `http-get:*:audio/mpeg:*`
2. GetCurrentConnectionIDs → response contains `<ConnectionIDs>0</ConnectionIDs>`
3. GetCurrentConnectionInfo → response contains `<Status>OK</Status>` and `<Direction>Output</Direction>`

### `test_search_action_returns_empty`
Verify Search action returns empty result:
1. POST raw SOAP Search request to ContentDirectory
2. Response contains empty `<Result />` element

### `test_device_xml_valid`
Verify device description XML:
1. Fetch /device.xml
2. Verify well-formed XML
3. Verify correct device type, friendly name, UDN (not default placeholder)
4. Verify 2 services are listed

### `test_ssdp_location_port`
Verify SSDP LOCATION URL has correct port:
1. Send M-SEARCH query via UDP multicast
2. Listen for search responses
3. Find one matching our device's LOCATION URL
4. Verify URL matches expected `http://127.0.0.1:{port}/device.xml`

Note: This test uses M-SEARCH (active query) instead of waiting for NOTIFY (passive, every ~30s). It is not timing-dependent.

## Coverage Summary

| Spec file | Claims | Covered | Missing (intentionally low priority) |
|-----------|--------|---------|--------------------------------------|
| architecture.md | 5 key decisions | 5/5 | — |
| forwarder.md | ~15 claims | 12/15 | Buffer timeout, trim error, auto-reconnect (edge cases) |
| server.md | ~12 claims | 11/12 | Search action returns empty (NOW COVERED) |
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
