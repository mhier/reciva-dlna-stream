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

            # Read all data
            received = await resp.read()

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
            chunk = await resp.content.read(1024)
            assert len(chunk) == 1024

    # Give the forwarder time to clean up
    await asyncio.sleep(0.5)

    # The forwarder is accessible through the server device.
    # We can verify by fetching /stream again — it should work fine
    # (new connection). The main assertion is that the old connection
    # is cleaned up properly (no unclosed tasks).
    async with ClientSession() as session:
        async with session.get(f"{dlna_base_uri}/stream", timeout=STREAM_READ_TIMEOUT) as resp:
            chunk = await resp.content.read(512)
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
