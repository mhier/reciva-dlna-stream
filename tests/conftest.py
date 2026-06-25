"""Fixtures for dlna-stream integration tests."""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port

from async_upnp_client.server import UpnpServer

from dlna_stream.forwarder import StreamForwarder
from dlna_stream.server import MediaServerDevice

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 16 KB of dummy audio data
DUMMY_DATA_SIZE = 16 * 1024

# A minimal valid MP3 frame header (sync word + basic config)
MP3_FRAME = bytes.fromhex(
    "fff3e06400000000000000000000000000000000"
    "0000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000"
)

# Build dummy data
DUMMY_MP3_DATA = (MP3_FRAME * 8)[:DUMMY_DATA_SIZE]
if len(DUMMY_MP3_DATA) < DUMMY_DATA_SIZE:
    DUMMY_MP3_DATA += b"\x00" * (DUMMY_DATA_SIZE - len(DUMMY_MP3_DATA))

DUMMY_MIME = "audio/mpeg"
DUMMY_STREAM_TITLE = "Test Radio Stream"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dummy_mp3_data() -> bytes:
    """Return the expected dummy MP3 data."""
    return DUMMY_MP3_DATA


@pytest.fixture()
def dlna_http_port() -> int:
    """Return a fixed port for the dlna-stream HTTP server.

    Using a known port avoids the 'port=0' problem where the device
    description URL in SSDP advertisements contains port 0 during
    device construction.
    """
    return unused_port()


@pytest.fixture()
async def fake_radio_server(dummy_mp3_data: bytes) -> AsyncIterator[TestServer]:
    """Start a tiny HTTP server that serves the dummy MP3 data."""

    async def handle_stream(request: web.Request) -> web.StreamResponse:
        """Serve the dummy MP3 as a streaming response."""
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": DUMMY_MIME,
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request)

        chunk_size = 4096
        offset = 0
        while offset < len(dummy_mp3_data):
            chunk = dummy_mp3_data[offset : offset + chunk_size]
            await response.write(chunk)
            offset += chunk_size
            await asyncio.sleep(0.001)

        return response

    app = web.Application()
    app.router.add_get("/radio", handle_stream)

    server = TestServer(app)
    await server.start_server()
    try:
        yield server
    finally:
        await server.close()


@pytest.fixture()
def fake_radio_url(fake_radio_server: TestServer) -> str:
    """Return the URL of the fake radio stream."""
    return f"http://127.0.0.1:{fake_radio_server.port}/radio"


@pytest.fixture()
def stream_forwarder(fake_radio_url: str) -> StreamForwarder:
    """Create a StreamForwarder pointed at the fake radio."""
    return StreamForwarder(stream_url=fake_radio_url, mime_type=DUMMY_MIME)


@pytest.fixture()
def dlna_device_class(
    stream_forwarder: StreamForwarder,
    dlna_http_port: int,
) -> type:
    """
    Create a MediaServerDevice subclass that sets up the forwarder
    on construction. This is needed because UpnpServer creates the device
    instance internally with a fixed constructor signature.
    """
    udn = f"uuid:{uuid4()}"

    class TestMediaServerDevice(MediaServerDevice):
        """A MediaServerDevice with a unique UDN and pre-wired forwarder."""

        DEVICE_DEFINITION = MediaServerDevice.DEVICE_DEFINITION._replace(
            udn=udn,
            friendly_name=DUMMY_STREAM_TITLE,
        )

        def __init__(
            self,
            requester: object,
            base_uri: str,
            boot_id: int = 1,
            config_id: int = 1,
        ) -> None:
            """Initialize and attach the forwarder."""
            super().__init__(
                requester=requester,
                base_uri=base_uri,
                boot_id=boot_id,
                config_id=config_id,
            )
            self.set_forwarder(stream_forwarder)

    return TestMediaServerDevice


@pytest.fixture()
async def dlna_server(
    dlna_device_class: type,
    fake_radio_url: str,
    stream_forwarder: StreamForwarder,
    dlna_http_port: int,
) -> AsyncIterator[UpnpServer]:
    """
    Start a fully-configured dlna-stream server, yield it, then
    shut it down and cancel any active stream connections.
    """
    server = UpnpServer(
        dlna_device_class,
        source=("127.0.0.1", 0),
        http_port=dlna_http_port,
        options={
            "ssdp_search_responder_options": {
                "ssdp_search_responder_always_rootdevice": True,
            },
        },
    )

    await server.async_start()

    try:
        # The actual port should match dlna_http_port since we specified it
        sockname = server._site._server.sockets[0].getsockname()  # type: ignore
        actual_port = sockname[1]
        base_uri = f"http://127.0.0.1:{actual_port}"

        # Configure services with stream info
        device = server._device
        assert device is not None
        device.configure_services(
            stream_url=fake_radio_url,
            stream_title=DUMMY_STREAM_TITLE,
            stream_mime_type=DUMMY_MIME,
            host_url=base_uri,
        )

        yield server
    finally:
        stream_forwarder.cancel_all()
        await server.async_stop()


@pytest.fixture()
def dlna_base_uri(dlna_server: UpnpServer, dlna_http_port: int) -> str:
    """Return the base URI of the dlna-stream server."""
    return f"http://127.0.0.1:{dlna_http_port}"
