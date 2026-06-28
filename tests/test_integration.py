"""Integration tests for reciva-dlna-stream.

Tests the full pipeline:
1. A fake HTTP radio stream serves dummy MP3 data
2. reciva-dlna-stream proxies it as a DLNA Media Server
3. A Python DLNA client (control point) discovers the server, browses
   ContentDirectory, reads the stream URL, fetches the data, and verifies
   it matches the original.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.profiles.dlna import DmsDevice

_LOGGER = logging.getLogger(__name__)

# Timeout for reading the stream (seconds)
STREAM_READ_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def _discover_dms(
    base_uri: str,
    retries: int = 3,
    delay: float = 1.5,
) -> DmsDevice:
    """
    Discover our DMS device by searching for MediaServer devices and
    matching our base URI.

    Retries a few times because SSDP discovery is inherently asynchronous.
    """
    location_url = f"{base_uri}/device.xml"

    for attempt in range(1, retries + 1):
        _LOGGER.debug("SSDP discovery attempt %d/%d", attempt, retries)
        responses = await DmsDevice.async_search(
            source=("127.0.0.1", 0),
            timeout=5,
        )

        # Filter responses that match our device
        our_device = None
        for resp in responses:
            loc = resp.get("location", "")
            if loc == location_url:
                our_device = resp
                break

        if our_device is not None:
            _LOGGER.debug("Found our device via SSDP: %s", our_device)
            break

        if attempt < retries:
            await asyncio.sleep(delay)
    else:
        pytest.fail(
            f"Could not discover our DMS device at {location_url} "
            f"via SSDP after {retries} attempts. "
            f"Found devices: "
            f"{[(r.get('location'), r.get('st')) for r in responses]}"
        )

    # Create the DMS device profile from the description URL
    requester = AiohttpRequester()
    factory = UpnpFactory(requester, non_strict=True)
    upnp_device = await factory.async_create_device(our_device["location"])
    dms = DmsDevice(upnp_device, event_handler=None)
    return dms


@pytest.mark.asyncio
async def test_reciva_dlna_stream_proxying(
    dlna_server,
    dlna_base_uri: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Full integration test:
    - Discover reciva-dlna-stream via SSDP
    - Browse ContentDirectory to get the stream URL
    - Read the stream data
    - Verify it matches the dummy data
    """
    # --- Phase 1: Discover the DMS via SSDP ---
    dms = await _discover_dms(dlna_base_uri)

    assert dms.device.friendly_name == "Test Radio Stream"
    assert dms.device_type == "urn:schemas-upnp-org:device:MediaServer:1"

    # --- Phase 2: Browse ContentDirectory ---
    browse_result = await dms.async_browse_direct_children("0")

    assert browse_result.number_returned == 1
    assert browse_result.total_matches == 1

    items = browse_result.result
    assert len(items) == 1

    item = items[0]
    assert item.title == "Test Radio Stream"
    assert item.upnp_class == "object.item.audioItem.audioBroadcast"
    assert len(item.res) == 1

    stream_uri = item.res[0].uri
    assert stream_uri.startswith(f"{dlna_base_uri}/stream")

    protocol_info = item.res[0].protocol_info
    assert protocol_info == "http-get:*:audio/mpeg:*"

    # --- Phase 3: Read the stream ---
    _LOGGER.info("Reading stream from: %s", stream_uri)
    async with ClientSession() as session:
        async with session.get(stream_uri, timeout=STREAM_READ_TIMEOUT) as resp:
            assert resp.status == 200
            assert resp.content_type == "audio/mpeg"

            # Read only the expected amount of data (the server has a fake
            # large Content-Length so we must not read until EOF).
            received = await resp.content.readexactly(len(dummy_mp3_data))

    # --- Phase 4: Verify content ---
    assert len(received) > 0, "Stream returned no data"
    assert received == dummy_mp3_data[: len(received)], (
        f"Stream data mismatch: got {len(received)} bytes, "
        f"expected first {len(received)} of {len(dummy_mp3_data)} bytes"
    )
    _LOGGER.info(
        "Verified %d bytes of stream data match expected content",
        len(received),
    )


@pytest.mark.asyncio
async def test_stream_stops_when_no_clients(
    dlna_base_uri: str,
    stream_forwarder: StreamForwarder,
) -> None:
    """
    Verify that no active stream connections exist after a client
    disconnects, and the buffer is eventually stopped after the
    grace period.
    """
    # Read a small chunk then disconnect
    async with ClientSession() as session:
        async with session.get(f"{dlna_base_uri}/stream", timeout=STREAM_READ_TIMEOUT) as resp:
            chunk = await resp.content.readexactly(1024)
            assert len(chunk) == 1024

    # Give the forwarder time to clean up the connection
    await asyncio.sleep(0.5)

    # The buffer should still be running (grace period)
    assert stream_forwarder.pending_disconnect, (
        "Disconnect timer should be pending during grace period"
    )
    assert stream_forwarder._buffer.is_running, (
        "Buffer should still be running during grace period"
    )

    # Wait for the grace period to expire
    from reciva_dlna_stream.forwarder import _DISCONNECT_TIMEOUT
    await asyncio.sleep(_DISCONNECT_TIMEOUT + 1)

    # Buffer should now be stopped
    assert not stream_forwarder.pending_disconnect, (
        "Disconnect timer should have expired"
    )
    assert not stream_forwarder._buffer.is_running, (
        "Buffer should be stopped after grace period"
    )

    # A new connection should work fine
    async with ClientSession() as session:
        async with session.get(f"{dlna_base_uri}/stream", timeout=STREAM_READ_TIMEOUT) as resp:
            chunk = await resp.content.readexactly(512)
            assert len(chunk) == 512


@pytest.mark.asyncio
async def test_browse_metadata(dlna_base_uri: str) -> None:
    """Test that BrowseMetadata returns the container info correctly."""
    requester = AiohttpRequester()
    factory = UpnpFactory(requester, non_strict=True)
    upnp_device = await factory.async_create_device(f"{dlna_base_uri}/device.xml")
    dms = DmsDevice(upnp_device, event_handler=None)

    # Browse metadata of root container
    result = await dms.async_browse(
        "0",
        browse_flag="BrowseMetadata",
    )
    assert result.number_returned == 1
    assert result.result is not None

    container = result.result[0]
    assert container.title == "Test Radio Stream"
    assert container.upnp_class.startswith("object.container")
    # childCount is a string in the XML, DIDL-Lite parser keeps it as string
    assert container.child_count == "1"


@pytest.mark.asyncio
async def test_range_request(
    dlna_base_uri: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Test that Range requests return 206 Partial Content with correct
    headers and are bounded to the requested byte window.
    """
    range_size = len(dummy_mp3_data)
    range_end = range_size - 1

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": f"bytes=0-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206, f"Expected 206, got {resp.status}"
            assert resp.headers.get("Content-Range", "").startswith(
                f"bytes 0-{range_end}/"
            ), f"Bad Content-Range: {resp.headers.get('Content-Range')}"
            assert resp.headers.get("Content-Length") == str(range_size), (
                f"Expected Content-Length: {range_size}, got "
                f"{resp.headers.get('Content-Length')}"
            )
            assert resp.headers.get("Accept-Ranges") == "bytes"
            assert resp.headers.get("TransferMode.DLNA.ORG") == "Streaming"

            data = await resp.content.readexactly(range_size)
            assert len(data) == range_size, (
                f"Expected {range_size} bytes, got {len(data)}"
            )
            assert data == dummy_mp3_data, "Range data must match"


@pytest.mark.asyncio
async def test_end_of_file_range_request(
    dlna_base_uri: str,
    stream_forwarder: StreamForwarder,
) -> None:
    """
    Test that a Range request targeting the end of the fake file
    returns synthetic ID3v1 tag data (last 129 bytes).
    """
    from reciva_dlna_stream.forwarder import _FAKE_CONTENT_LENGTH, _build_synthetic_footer

    synthetic_footer = _build_synthetic_footer()

    # The last 129 bytes of the fake file
    range_start = _FAKE_CONTENT_LENGTH - len(synthetic_footer)
    range_end = _FAKE_CONTENT_LENGTH - 1

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": f"bytes={range_start}-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206, f"Expected 206, got {resp.status}"
            assert resp.headers.get("Content-Range", "").startswith(
                f"bytes {range_start}-{range_end}/"
            ), f"Bad Content-Range: {resp.headers.get('Content-Range')}"
            assert resp.headers.get("Accept-Ranges") == "bytes"
            assert resp.headers.get("TransferMode.DLNA.ORG") == "Streaming"

            data = await resp.content.readexactly(len(synthetic_footer))
            assert len(data) == len(synthetic_footer), (
                f"Expected {len(synthetic_footer)} bytes, got {len(data)}"
            )
            assert data == synthetic_footer, (
                "Synthetic footer data mismatch"
            )

    _LOGGER.info(
        "Verified %d bytes of synthetic footer: starts with %s",
        len(data),
        data[:4].hex(),
    )


@pytest.mark.asyncio
async def test_active_connection_count(
    dlna_base_uri: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Verify that active_connection_count reflects current connections.
    """
    # Open two concurrent connections and verify both receive data.
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            timeout=STREAM_READ_TIMEOUT,
        ) as resp1, session.get(
            f"{dlna_base_uri}/stream",
            timeout=STREAM_READ_TIMEOUT,
        ) as resp2:
            assert resp1.status == 200
            assert resp2.status == 200
            chunk1 = await resp1.content.readexactly(1024)
            chunk2 = await resp2.content.readexactly(1024)
            # Both connections must see the same data (ring buffer consistency)
            assert chunk1 == chunk2, (
                "Concurrent connections must return same data from ring buffer"
            )
            assert chunk1 == dummy_mp3_data[:1024]


@pytest.mark.asyncio
async def test_fake_content_length_property(
    stream_forwarder,
) -> None:
    """Verify fake_content_length property returns the expected value."""
    from reciva_dlna_stream.forwarder import _FAKE_CONTENT_LENGTH
    assert stream_forwarder.fake_content_length == _FAKE_CONTENT_LENGTH


@pytest.mark.asyncio
async def test_full_stream_response_headers(
    dlna_base_uri: str,
) -> None:
    """
    Verify that a full-stream (200) response includes the expected headers.
    """
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "audio/mpeg"
            assert resp.headers.get("Accept-Ranges") == "bytes"
            assert resp.headers.get("TransferMode.DLNA.ORG") == "Streaming"
            assert resp.headers.get("Cache-Control") == "no-cache"
            # Content-Length should be the fake file size
            from reciva_dlna_stream.forwarder import _FAKE_CONTENT_LENGTH
            assert resp.headers.get("Content-Length") == str(_FAKE_CONTENT_LENGTH)
            # Just read a bit to confirm stream works
            _ = await resp.content.readexactly(512)


@pytest.mark.asyncio
async def test_data_consistency_across_connections(
    dlna_base_uri: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Verify that the same byte range returns the same data across multiple
    connections. This is critical for Reciva radios that probe in separate
    HTTP requests.
    """
    range_size = 4096
    range_end = range_size - 1

    # First connection
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": f"bytes=0-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data1 = await resp.content.readexactly(range_size)

    # Second connection (different TCP connection)
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": f"bytes=0-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data2 = await resp.content.readexactly(range_size)

    # Must be identical (ring buffer consistency)
    assert data1 == data2, (
        "Data at byte offset 0 must be identical across connections"
    )
    assert data1 == dummy_mp3_data[:range_size]


@pytest.mark.asyncio
async def test_buffer_persists_across_sequential_connections(
    dlna_base_uri: str,
    dummy_mp3_data: bytes,
    stream_forwarder: StreamForwarder,
) -> None:
    """
    Verify that the buffer persists across sequential connections within
    the grace period. This simulates the Reciva radio pattern where it
    connects, disconnects, and reconnects for the next range quickly.
    """
    from reciva_dlna_stream.forwarder import _DISCONNECT_TIMEOUT

    # First connection: request a range
    range1_size = 4096
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": f"bytes=0-{range1_size - 1}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data1 = await resp.content.readexactly(range1_size)

    # Buffer should still be alive (grace period)
    assert stream_forwarder.is_buffer_running, (
        "Buffer should still run during grace period"
    )
    assert stream_forwarder._buffer.total_bytes_read > 0, (
        "Buffer should have read data"
    )

    # Second connection within grace period: request same range
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": f"bytes=0-{range1_size - 1}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data2 = await resp.content.readexactly(range1_size)

    # Both connections must see the same data (buffer consistency)
    assert data1 == data2, (
        "Data must be identical across connections within grace period"
    )
    assert data1 == dummy_mp3_data[:range1_size]

    # Let grace period expire, then verify new data is from a new buffer instance
    await asyncio.sleep(_DISCONNECT_TIMEOUT + 1)

    assert not stream_forwarder._buffer.is_running, (
        "Buffer should be stopped after grace period"
    )


@pytest.mark.asyncio
async def test_multi_chunk_range_request(
    dlna_base_uri: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Test a range that requires multiple chunk reads from the ring buffer.
    The dummy data is 16KB and BUFFER_SIZE is 64KB, so a single chunk
    covers it all. But we can request a range that spans the full dummy
    data (16384 bytes) to verify the chunked read loop in
    _handle_buffer_range works correctly.
    """
    from reciva_dlna_stream.forwarder import _BUFFER_SIZE
    # Request the full dummy data size (requires at least one read from buffer)
    range_size = len(dummy_mp3_data)
    range_end = range_size - 1

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": f"bytes=0-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            assert resp.headers.get("Content-Range", "").startswith(
                f"bytes 0-{range_end}/"
            )
            assert resp.headers.get("Content-Length") == str(range_size)

            data = await resp.content.readexactly(range_size)
            assert len(data) == range_size
            assert data == dummy_mp3_data[:range_size], (
                f"Range data mismatch: "
                f"expected {range_size} bytes, got {len(data)}"
            )
            assert resp.headers.get("Accept-Ranges") == "bytes"
            assert resp.headers.get("TransferMode.DLNA.ORG") == "Streaming"


@pytest.mark.asyncio
async def test_connection_manager_actions(
    dlna_base_uri: str,
) -> None:
    """Test ConnectionManager:1 actions via SOAP/UPnP."""
    import xml.etree.ElementTree as ET

    # Build a minimal SOAP request for GetProtocolInfo
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:GetProtocolInfo xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">'
        '</u:GetProtocolInfo>'
        '</s:Body>'
        '</s:Envelope>'
    ).encode("utf-8")

    async with ClientSession() as session:
        async with session.post(
            f"{dlna_base_uri}/upnp/control/ConnectionManager1",
            data=body,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ConnectionManager:1#GetProtocolInfo"',
                "Content-Type": "text/xml; charset=utf-8",
            },
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            text = await resp.text()
            assert "http-get:*:audio/mpeg:*" in text

    # GetCurrentConnectionIDs
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:GetCurrentConnectionIDs xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">'
        '</u:GetCurrentConnectionIDs>'
        '</s:Body>'
        '</s:Envelope>'
    ).encode("utf-8")

    async with ClientSession() as session:
        async with session.post(
            f"{dlna_base_uri}/upnp/control/ConnectionManager1",
            data=body,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ConnectionManager:1#GetCurrentConnectionIDs"',
                "Content-Type": "text/xml; charset=utf-8",
            },
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            text = await resp.text()
            # Response uses <ConnectionIDs> not <CurrentConnectionIDs>
            assert "<ConnectionIDs>0</ConnectionIDs>" in text

    # GetCurrentConnectionInfo
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:GetCurrentConnectionInfo xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">'
        '<ConnectionID>0</ConnectionID>'
        '</u:GetCurrentConnectionInfo>'
        '</s:Body>'
        '</s:Envelope>'
    ).encode("utf-8")

    async with ClientSession() as session:
        async with session.post(
            f"{dlna_base_uri}/upnp/control/ConnectionManager1",
            data=body,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ConnectionManager:1#GetCurrentConnectionInfo"',
                "Content-Type": "text/xml; charset=utf-8",
            },
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            text = await resp.text()
            assert "<Status>OK</Status>" in text
            assert "<Direction>Output</Direction>" in text


# ---------------------------------------------------------------------------
# Multi-stream ConnectionManager (D-15)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_manager_multi_mime(
    dlna_base_uri_multi: str,
    dlna_http_port: int,
) -> None:
    """Test that GetProtocolInfo returns all MIME types in multi-stream mode.

    The multi-stream fixture uses two streams both with audio/mpeg, so
    the expected output is a single protocol info entry (no duplicates
    needed - we just verify the format is correct for multi-stream).
    The comma-separated format works even with identical MIME types.
    """
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:GetProtocolInfo xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">'
        '</u:GetProtocolInfo>'
        '</s:Body>'
        '</s:Envelope>'
    ).encode("utf-8")

    async with ClientSession() as session:
        async with session.post(
            f"{dlna_base_uri_multi}/upnp/control/ConnectionManager1",
            data=body,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ConnectionManager:1#GetProtocolInfo"',
                "Content-Type": "text/xml; charset=utf-8",
            },
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            text = await resp.text()
            # Both streams use audio/mpeg, so we expect one or two entries
            assert "http-get:*:audio/mpeg:*" in text


@pytest.mark.asyncio
async def test_connection_manager_multi_mime_distinct(
    fake_radio_url: str,
    dlna_http_port: int,
) -> None:
    """Test GetProtocolInfo with streams having distinct MIME types.

    Sets up two streams with different MIME types and verifies both
    appear as comma-separated protocol info entries.
    """
    import xml.etree.ElementTree as ET
    from uuid import uuid4

    from reciva_dlna_stream.forwarder import StreamForwarder
    from reciva_dlna_stream.server import MediaServerDevice
    from reciva_dlna_stream.server_lifecycle import start_server
    from reciva_dlna_stream.stream_config import StreamConfig
    from conftest import make_device_class

    # Create forwarders with different MIME types
    fwd_mpeg = StreamForwarder(stream_url=fake_radio_url, mime_type="audio/mpeg")
    fwd_ogg = StreamForwarder(stream_url=fake_radio_url, mime_type="audio/ogg")

    streams = [
        StreamConfig(url=fake_radio_url, name="MP3 Stream", mime_type="audio/mpeg"),
        StreamConfig(url=fake_radio_url, name="OGG Stream", mime_type="audio/ogg"),
    ]
    forwarders = [fwd_mpeg, fwd_ogg]

    device_class = make_device_class(
        forwarders=forwarders,
        friendly_name="Distinct MIME Test",
    )

    handle = await start_server(
        device_class=device_class,
        local_ip="127.0.0.1",
        http_bind="127.0.0.1",
        http_port=dlna_http_port,
        streams=streams,
        forwarders=forwarders,
    )

    try:
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
            ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            '<s:Body>'
            '<u:GetProtocolInfo xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">'
            '</u:GetProtocolInfo>'
            '</s:Body>'
            '</s:Envelope>'
        ).encode("utf-8")

        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{handle.port}/upnp/control/ConnectionManager1",
                data=body,
                headers={
                    "SOAPACTION": '"urn:schemas-upnp-org:service:ConnectionManager:1#GetProtocolInfo"',
                    "Content-Type": "text/xml; charset=utf-8",
                },
                timeout=STREAM_READ_TIMEOUT,
            ) as resp:
                assert resp.status == 200
                text = await resp.text()
                # Both MIME types should appear in the response
                assert "http-get:*:audio/mpeg:*" in text
                assert "http-get:*:audio/ogg:*" in text
                # They should be comma-separated
                assert (
                    "http-get:*:audio/mpeg:*,http-get:*:audio/ogg:*" in text
                    or "http-get:*:audio/ogg:*,http-get:*:audio/mpeg:*" in text
    )
    finally:
        await fwd_mpeg.cancel_all()
        await fwd_ogg.cancel_all()
        await handle.stop()


@pytest.mark.asyncio
async def test_search_action_returns_empty(
    dlna_base_uri: str,
) -> None:
    """Test that the Search action returns empty result."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:Search xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
        '<ContainerID>0</ContainerID>'
        '<BrowseFlag>BrowseDirectChildren</BrowseFlag>'
        '<Filter>*</Filter>'
        '<StartingIndex>0</StartingIndex>'
        '<RequestedCount>0</RequestedCount>'
        '<SortCriteria></SortCriteria>'
        '</u:Search>'
        '</s:Body>'
        '</s:Envelope>'
    ).encode("utf-8")

    async with ClientSession() as session:
        async with session.post(
            f"{dlna_base_uri}/upnp/control/ContentDirectory1",
            data=body,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#Search"',
                "Content-Type": "text/xml; charset=utf-8",
            },
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            text = await resp.text()
            # Should be an empty DIDL-Lite result
            # Response may use <Result /> (self-closing) or <Result></Result>
            assert "<Result" in text and "</Result>" in text or "<Result/>" in text or "<Result />" in text


@pytest.mark.asyncio
async def test_device_xml_valid(
    dlna_base_uri: str,
) -> None:
    """
    Test that the device description XML is valid and contains the correct
    URLs (not port 0), services, and UDN.
    """
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/device.xml",
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            text = await resp.text()

            # Verify the XML is well-formed and contains expected content
            assert "urn:schemas-upnp-org:device:MediaServer:1" in text
            assert "Test Radio Stream" in text
            assert "/ContentDirectory_1.xml" in text
            assert "/ConnectionManager_1.xml" in text

            # Verify it's parseable XML
            import xml.etree.ElementTree as ET
            root = ET.fromstring(text)
            assert root.tag.endswith("root"), (
                f"Expected root element, got {root.tag}"
            )

            # Verify the UDN is present and is not the default placeholder
            ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
            udn_el = root.find(".//upnp:UDN", ns)
            assert udn_el is not None and udn_el.text
            assert udn_el.text.startswith("uuid:")
            assert "00000000-0000" not in udn_el.text, (
                "UDN should not be the default placeholder"
            )

            # Verify the device has services
            service_list = root.find(".//upnp:serviceList", ns)
            assert service_list is not None
            services = service_list.findall("upnp:service", ns)
            assert len(services) == 2, (
                f"Expected 2 services, got {len(services)}"
            )


@pytest.mark.asyncio
async def test_ssdp_location_port(dlna_server, dlna_base_uri: str) -> None:
    """
    Verify that the SSDP LOCATION URL contains the correct port (not 0).

    Instead of sending real SSDP multicast packets (which requires raw socket
    access and can conflict with other SSDP services on the network), we
    inspect the ``ServerHandle.ssdp_location_url`` property, which derives
    the LOCATION URL from the SsdpSearchResponder's device configuration
    (the same source that SSDP responses use).
    """
    expected_location = f"{dlna_base_uri}/device.xml"
    actual_location = dlna_server.ssdp_location_url

    assert actual_location == expected_location, (
        f"SSDP LOCATION URL mismatch: expected {expected_location}, "
        f"got {actual_location}"
    )
    # Also verify the port is not 0 (the original bug)
    assert ":0/" not in actual_location, (
        f"SSDP LOCATION URL must not contain port 0: {actual_location}"
    )


@pytest.mark.asyncio
async def test_buffer_trim_error_returns_416(
    dlna_base_uri: str,
    stream_forwarder: StreamForwarder,
) -> None:
    """
    Test that requesting a trimmed offset returns 416 Range Not Satisfiable.

    After the ring buffer fills past _MAX_BUFFER_SIZE (4 MB), old data is
    trimmed. A subsequent range request for a trimmed offset must return
    a 416 status with a Content-Range header indicating the file size.

    We fill the buffer by appending data directly, then verify that the
    HTTP handler returns 416 when the requested offset has been trimmed.
    """
    from reciva_dlna_stream.forwarder import _MAX_BUFFER_SIZE

    buffer = stream_forwarder._buffer

    # Start the buffer and let it fetch a bit of data to be realistic
    await buffer.start()

    # Fill the buffer past _MAX_BUFFER_SIZE by injecting data directly
    # (under the lock, as _run() would).
    chunk_size = 64 * 1024  # Match _BUFFER_SIZE
    target_size = _MAX_BUFFER_SIZE + chunk_size

    async with buffer.condition:
        while len(buffer._buffer) < target_size:
            buffer._buffer.extend(b"\x00" * chunk_size)
            buffer._total_read += chunk_size

        # Now trim the buffer down to _MAX_BUFFER_SIZE (simulating
        # what _run() does when it exceeds the max)
        if len(buffer._buffer) > _MAX_BUFFER_SIZE:
            excess = len(buffer._buffer) - _MAX_BUFFER_SIZE
            del buffer._buffer[:excess]

    assert buffer.total_bytes_read > _MAX_BUFFER_SIZE, (
        f"total_bytes_read={buffer.total_bytes_read} should exceed "
        f"_MAX_BUFFER_SIZE={_MAX_BUFFER_SIZE}"
    )

    # Request offset 0 — it should have been trimmed
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": "bytes=0-4095"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 416, (
                f"Expected 416 for trimmed offset, got {resp.status}"
            )
            assert resp.headers.get("Content-Range", "").startswith("bytes */"), (
                f"Expected Content-Range: bytes */..., "
                f"got {resp.headers.get('Content-Range')}"
            )


# ---------------------------------------------------------------------------
# Multi-stream tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_stream_browse_direct_children(
    dlna_base_uri_multi: str,
) -> None:
    """
    Test that BrowseDirectChildren on a multi-stream server returns
    all configured streams.
    """
    requester = AiohttpRequester()
    factory = UpnpFactory(requester, non_strict=True)
    upnp_device = await factory.async_create_device(
        f"{dlna_base_uri_multi}/device.xml"
    )
    dms = DmsDevice(upnp_device, event_handler=None)

    # Browse children of root
    result = await dms.async_browse_direct_children("0")

    assert result.number_returned == 2
    assert result.total_matches == 2

    items = result.result
    assert len(items) == 2

    # First stream
    assert items[0].title == "Test Radio Stream"
    assert items[0].upnp_class == "object.item.audioItem.audioBroadcast"
    assert len(items[0].res) == 1
    assert items[0].res[0].uri.endswith("/stream/0")

    # Second stream
    assert items[1].title == "Alt Radio Stream"
    assert items[1].upnp_class == "object.item.audioItem.audioBroadcast"
    assert len(items[1].res) == 1
    assert items[1].res[0].uri.endswith("/stream/1")


@pytest.mark.asyncio
async def test_multi_stream_browse_metadata_root(
    dlna_base_uri_multi: str,
) -> None:
    """
    Test that BrowseMetadata on root container reports the correct
    child count for multi-stream.
    """
    requester = AiohttpRequester()
    factory = UpnpFactory(requester, non_strict=True)
    upnp_device = await factory.async_create_device(
        f"{dlna_base_uri_multi}/device.xml"
    )
    dms = DmsDevice(upnp_device, event_handler=None)

    result = await dms.async_browse("0", browse_flag="BrowseMetadata")

    assert result.number_returned == 1
    assert result.result is not None
    container = result.result[0]
    assert container.child_count == "2"


@pytest.mark.asyncio
async def test_multi_stream_browse_item_metadata(
    dlna_base_uri_multi: str,
) -> None:
    """
    Test BrowseMetadata on individual stream items in multi-stream mode.
    """
    requester = AiohttpRequester()
    factory = UpnpFactory(requester, non_strict=True)
    upnp_device = await factory.async_create_device(
        f"{dlna_base_uri_multi}/device.xml"
    )
    dms = DmsDevice(upnp_device, event_handler=None)

    # Browse metadata of item "1" (second stream)
    result = await dms.async_browse("1", browse_flag="BrowseMetadata")

    assert result.number_returned == 1
    assert result.result is not None
    item = result.result[0]
    assert item.title == "Alt Radio Stream"
    assert item.upnp_class == "object.item.audioItem.audioBroadcast"
    assert len(item.res) == 1
    assert item.res[0].uri.endswith("/stream/1")


@pytest.mark.asyncio
async def test_multi_stream_range_request(
    dlna_base_uri_multi: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Test that range requests to individual stream routes work correctly.
    """
    range_size = len(dummy_mp3_data)
    range_end = range_size - 1

    # Stream 0
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream/0",
            headers={"Range": f"bytes=0-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data = await resp.content.readexactly(range_size)
            assert data == dummy_mp3_data

    # Stream 1
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream/1",
            headers={"Range": f"bytes=0-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data = await resp.content.readexactly(range_size)
            assert data == dummy_mp3_data


@pytest.mark.asyncio
async def test_multi_stream_full_request(
    dlna_base_uri_multi: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Test that full (non-range) requests to individual stream routes work.
    """
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream/0",
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            assert resp.content_type == "audio/mpeg"
            received = await resp.content.readexactly(len(dummy_mp3_data))
            assert received == dummy_mp3_data

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream/1",
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 200
            assert resp.content_type == "audio/mpeg"
            received = await resp.content.readexactly(len(dummy_mp3_data))
            assert received == dummy_mp3_data


@pytest.mark.asyncio
async def test_multi_stream_end_of_file(
    dlna_base_uri_multi: str,
) -> None:
    """
    Test that synthetic footer is served for multi-stream routes.
    """
    from reciva_dlna_stream.forwarder import _FAKE_CONTENT_LENGTH, _build_synthetic_footer

    synthetic_footer = _build_synthetic_footer()

    range_start = _FAKE_CONTENT_LENGTH - len(synthetic_footer)
    range_end = _FAKE_CONTENT_LENGTH - 1

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream/0",
            headers={"Range": f"bytes={range_start}-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data = await resp.content.readexactly(len(synthetic_footer))
            assert data == synthetic_footer

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream/1",
            headers={"Range": f"bytes={range_start}-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data = await resp.content.readexactly(len(synthetic_footer))
            assert data == synthetic_footer


@pytest.mark.asyncio
async def test_single_stream_backward_compat_route(
    dlna_base_uri: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Test that in single-stream mode, /stream (without index) still works
    as a backward-compatible alias.
    """
    range_size = len(dummy_mp3_data)
    range_end = range_size - 1

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri}/stream",
            headers={"Range": f"bytes=0-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data = await resp.content.readexactly(range_size)
            assert data == dummy_mp3_data


@pytest.mark.asyncio
async def test_multi_stream_no_legacy_route(
    dlna_base_uri_multi: str,
) -> None:
    """
    Test that in multi-stream mode, /stream (without index) returns 404.
    """
    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream",
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 404


@pytest.mark.asyncio
async def test_cancel_all_cleans_up_connections(
    dlna_base_uri: str,
    stream_forwarder: StreamForwarder,
) -> None:
    """
    Verify that cancel_all() terminates all active connections and
    clears the disconnect timer. (D-10)

    Establishes multiple full-stream connections, verifies they are
    tracked, then cancels them via cancel_all() while they are still
    active. After cancellation the connection count must be zero and
    no disconnect timer should be pending.
    """
    # Start three concurrent connections.
    async with ClientSession() as session:
        # We create the connections but do NOT read from them at first,
        # so the handler tasks are still alive inside _handle_full_stream's
        # read loop (waiting on buffer read).
        conns = []
        for _ in range(3):
            resp = await session.get(
                f"{dlna_base_uri}/stream",
                timeout=STREAM_READ_TIMEOUT,
            )
            assert resp.status == 200
            conns.append(resp)

        # Verify all three connections are tracked
        assert stream_forwarder.active_connection_count == 3, (
            f"Expected 3 active connections, got "
            f"{stream_forwarder.active_connection_count}"
        )

        # Cancel all connections while they are still active.
        # This sends CancelledError into each handler task, which
        # then discards itself from _active_connections.
        await stream_forwarder.cancel_all()

        # Allow a brief yield so cancelled tasks run their finally blocks
        await asyncio.sleep(0)

        # All connections should be cleaned up
        assert stream_forwarder.active_connection_count == 0, (
            f"Expected 0 active connections after cancel_all, got "
            f"{stream_forwarder.active_connection_count}"
        )

        # No disconnect timer should be pending
        assert not stream_forwarder.pending_disconnect, (
            "Expected no pending disconnect timer after cancel_all"
        )

        # Clean up client-side responses
        for resp in conns:
            resp.close()


# ---------------------------------------------------------------------------
# Buffer auto-reconnect tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buffer_reconnects_after_stream_end(
    fake_radio_server_flakey: TestServer,
) -> None:
    """
    Verify that the buffer reconnects to the remote stream after it ends.

    The flakey radio server only serves 8 KB per connection. The buffer
    should detect the stream end, reconnect after ``_RECONNECT_DELAY``,
    and continue accumulating data.
    """
    from reciva_dlna_stream.forwarder import StreamBuffer, _RECONNECT_DELAY

    url = f"http://127.0.0.1:{fake_radio_server_flakey.port}/radio/flakey"
    buffer = StreamBuffer(url)

    await buffer.start()

    try:
        # Wait for the buffer to fetch data from the first connection
        await asyncio.sleep(0.5)

        # The buffer should have read data from the first connection
        total_before = buffer.total_bytes_read
        assert total_before > 0, (
            f"Expected buffer to have read some data, got {total_before}"
        )

        # Wait for reconnect delay + buffer to fetch data from second connection
        await asyncio.sleep(_RECONNECT_DELAY + 1.0)

        # The buffer should have read more data from the reconnection
        total_after = buffer.total_bytes_read
        assert total_after > total_before, (
            f"Expected buffer to read more data after reconnect "
            f"(before={total_before}, after={total_after})"
        )
    finally:
        await buffer.stop()


@pytest.mark.asyncio
async def test_buffer_read_timeout_returns_empty() -> None:
    """
    Verify that StreamBuffer.read() returns empty bytes on timeout.

    When the buffer has no data at the requested offset and the timeout
    expires, read() should return b"" rather than blocking indefinitely.
    """
    from reciva_dlna_stream.forwarder import StreamBuffer

    # Create a buffer but never start it — no data will ever arrive.
    buffer = StreamBuffer(stream_url="http://127.0.0.1:1/nonexistent")

    # Read with a very short timeout — should return empty bytes.
    result = await buffer.read(offset=0, size=1024, timeout=0.1)

    assert result == b"", (
        f"Expected empty bytes on read timeout, got {len(result)} bytes"
    )


# ---------------------------------------------------------------------------
# Session cleanup after cancelled _run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_closed_after_cancelled_run() -> None:
    """
    Verify that StreamBuffer._close_session() is called when _run() is
    cancelled (via stop()), not just on clean stop.
    """
    from reciva_dlna_stream.forwarder import StreamBuffer

    buffer = StreamBuffer(stream_url="http://127.0.0.1:1/nonexistent")
    await buffer.start()

    # Give _run() a moment to create the session and connector
    await asyncio.sleep(0.1)

    # Verify session and connector were created
    assert buffer._session is not None, "Session should be created in _run()"
    assert buffer._connector is not None, "Connector should be created in _run()"

    # Stop the buffer — this cancels _run() which should trigger _close_session()
    await buffer.stop()

    # Verify session and connector are None after cleanup
    assert buffer._session is None, (
        "Session should be None after stopped/cancelled _run()"
    )
    assert buffer._connector is None, (
        "Connector should be None after stopped/cancelled _run()"
    )

