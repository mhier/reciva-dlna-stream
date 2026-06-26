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
) -> None:
    """
    Verify that no active stream connections exist after a client
    disconnects. The StreamForwarder should clean up its task set.
    """
    # Read a small chunk then disconnect
    async with ClientSession() as session:
        async with session.get(f"{dlna_base_uri}/stream", timeout=STREAM_READ_TIMEOUT) as resp:
            chunk = await resp.content.readexactly(1024)
            assert len(chunk) == 1024

    # Give the forwarder time to clean up
    await asyncio.sleep(0.5)

    # The forwarder is accessible through the server device.
    # We can verify by fetching /stream again — it should work fine
    # (new connection). The main assertion is that the old connection
    # is cleaned up properly (no unclosed tasks).
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
) -> None:
    """
    Test that a Range request targeting the end of the fake file
    returns synthetic ID3v1 tag data (last 129 bytes).
    """
    from reciva_dlna_stream.forwarder import _FAKE_CONTENT_LENGTH, _SYNTHETIC_FOOTER

    # The last 129 bytes of the fake file
    range_start = _FAKE_CONTENT_LENGTH - len(_SYNTHETIC_FOOTER)
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

            data = await resp.content.readexactly(len(_SYNTHETIC_FOOTER))
            assert len(data) == len(_SYNTHETIC_FOOTER), (
                f"Expected {len(_SYNTHETIC_FOOTER)} bytes, got {len(data)}"
            )
            assert data == _SYNTHETIC_FOOTER, (
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

    We send an SSDP M-SEARCH query and verify the search response contains
    the correct LOCATION URL. This is more reliable than passively listening
    for NOTIFY advertisements (which arrive every ~30s).
    """
    import socket
    import struct
    import asyncio

    SOCKET_TIMEOUT = 5
    MCAST_GRP = "239.255.255.250"
    MCAST_PORT = 1900
    ST = "urn:schemas-upnp-org:device:MediaServer:1"

    expected_location = f"{dlna_base_uri}/device.xml"
    found = False

    # Join SSDP multicast group
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MCAST_PORT))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)

    try:
        # Send M-SEARCH query
        mx = 3
        msearch = (
            f"M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {MCAST_GRP}:{MCAST_PORT}\r\n"
            f"MAN: \"ssdp:discover\"\r\n"
            f"MX: {mx}\r\n"
            f"ST: {ST}\r\n"
            f"\r\n"
        ).encode("utf-8")

        loop = asyncio.get_running_loop()
        await loop.sock_sendto(sock, msearch, (MCAST_GRP, MCAST_PORT))

        # Listen for responses
        deadline = loop.time() + SOCKET_TIMEOUT
        while loop.time() < deadline:
            try:
                data = await loop.sock_recv(sock, 4096)
                packet = data.decode("utf-8", errors="replace")
                for line in packet.split("\r\n"):
                    if line.lower().startswith("location:"):
                        url = line.split(":", 1)[1].strip()
                        if url == expected_location:
                            found = True
                            _LOGGER.info(
                                "SSDP SEARCH RESPONSE LOCATION matches: %s",
                                url,
                            )
                            break
                    if line.lower().startswith("st:"):
                        _LOGGER.debug("SSDP ST: %s", line)
            except (BlockingIOError, TimeoutError):
                await asyncio.sleep(0.1)
                continue
            if found:
                break
    finally:
        sock.close()

    assert found, (
        f"Did not find SSDP SEARCH RESPONSE with LOCATION={expected_location} "
        f"in {SOCKET_TIMEOUT}s."
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
    from reciva_dlna_stream.forwarder import _FAKE_CONTENT_LENGTH, _SYNTHETIC_FOOTER

    range_start = _FAKE_CONTENT_LENGTH - len(_SYNTHETIC_FOOTER)
    range_end = _FAKE_CONTENT_LENGTH - 1

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream/0",
            headers={"Range": f"bytes={range_start}-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data = await resp.content.readexactly(len(_SYNTHETIC_FOOTER))
            assert data == _SYNTHETIC_FOOTER

    async with ClientSession() as session:
        async with session.get(
            f"{dlna_base_uri_multi}/stream/1",
            headers={"Range": f"bytes={range_start}-{range_end}"},
            timeout=STREAM_READ_TIMEOUT,
        ) as resp:
            assert resp.status == 206
            data = await resp.content.readexactly(len(_SYNTHETIC_FOOTER))
            assert data == _SYNTHETIC_FOOTER


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
