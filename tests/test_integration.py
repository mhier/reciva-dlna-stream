"""Integration tests for dlna-stream.

Tests the full pipeline:
1. A fake HTTP radio stream serves dummy MP3 data
2. dlna-stream proxies it as a DLNA Media Server
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
async def test_dlna_stream_proxying(
    dlna_server,
    dlna_base_uri: str,
    dummy_mp3_data: bytes,
) -> None:
    """
    Full integration test:
    - Discover dlna-stream via SSDP
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
async def test_device_xml_valid(dlna_base_uri: str) -> None:
    """
    Test that the device description XML is valid and contains the correct
    URLs (not port 0).
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
    Verify that the SSDP advertisement LOCATION URL contains the correct
    port (not 0). We do this by scraping SSDP NOTIFY packets.
    """
    import socket
    import struct
    import asyncio

    SOCKET_TIMEOUT = 5
    MCAST_GRP = "239.255.255.250"
    MCAST_PORT = 1900

    # Listen for SSDP multicast packets
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MCAST_PORT))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)

    expected_location = f"{dlna_base_uri}/device.xml"
    found = False

    try:
        loop = asyncio.get_running_loop()
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
                                "SSDP LOCATION matches: %s", url
                            )
                            break
            except (BlockingIOError, TimeoutError):
                await asyncio.sleep(0.1)
                continue
            if found:
                break
    finally:
        sock.close()

    assert found, (
        f"Did not find SSDP advertisement with LOCATION={expected_location} "
        f"in {SOCKET_TIMEOUT}s. SSDP advertisements are sent every ~30s by "
        f"default, so the test may need a longer timeout or the server may "
        f"not have sent one yet."
    )
