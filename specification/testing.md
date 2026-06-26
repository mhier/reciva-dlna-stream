# Testing Design

## Test Framework
- **pytest** with `pytest-asyncio` (asyncio_mode = auto)
- HTTP client: `aiohttp.ClientSession`
- UPnP client: `async_upnp_client` (AiohttpRequester + UpnpFactory + DmsDevice)

## Test Fixtures (`conftest.py`)

### `fake_radio` (aiohttp server)
A minimal HTTP server that serves dummy MP3 data. Serves:
- `GET /radio` → returns `dummy_mp3_data` in 4 KB chunks, has no Content-Length (streaming)

### `dummy_mp3_data` (bytes)
4096 bytes of fake MP3 data starting with an MPEG frame sync word (`0xFF 0xF3`).

### `stream_forwarder`
Creates a `StreamForwarder` pointing at the fake radio URL.

### `dlna_server` (async fixture)
Uses `start_server()` (same as production) to start the full server with the stream forwarder. The server lifecycle starts the ring buffer background task. Yields a `ServerHandle`.

### `dlna_base_uri`
The base URI of the running server (e.g. `http://127.0.0.1:12345`).

## Tests (`test_integration.py`)

### `test_dlna_stream_proxying`
Full integration test:
1. Discover the DMS via SSDP
2. Browse ContentDirectory → get stream URL
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

## Fixture Lifecycle
Each test gets a fresh server instance (new port, new forwarder, new UPnP device with unique UDN). The buffer background task starts with the server and is stopped during teardown. Fixture scope is `function` by default (pytest-asyncio default test loop scope).
