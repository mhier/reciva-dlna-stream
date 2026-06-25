"""Fixtures for dlna-stream integration tests."""

from __future__ import annotations

import asyncio
import socket
from typing import AsyncIterator
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port

from dlna_stream.forwarder import StreamForwarder
from dlna_stream.server import MediaServerDevice
from dlna_stream.server_lifecycle import ServerHandle, start_server

# ---------------------------------------------------------------------------
# Apply the SSDP TTL monkey-patch (TTL 4 per UPnP spec, library uses 2)
# ---------------------------------------------------------------------------
import async_upnp_client.ssdp as _ssdp_module

_orig_get_ssdp_socket = _ssdp_module.get_ssdp_socket


def _patched_get_ssdp_socket(*args, **kwargs):
    sock, src, tgt = _orig_get_ssdp_socket(*args, **kwargs)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    except OSError:
        pass
    return sock, src, tgt


_ssdp_module.get_ssdp_socket = _patched_get_ssdp_socket

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
    """Return a fixed port for the dlna-stream HTTP server."""
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
def dlna_device_class(stream_forwarder: StreamForwarder) -> type:
    """
    Create a MediaServerDevice subclass that sets up the forwarder
    on construction.
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
) -> AsyncIterator[ServerHandle]:
    """
    Start a fully-configured dlna-stream server using the same startup
    logic as ``__main__.py`` (``start_server`` from ``server_lifecycle``),
    yield the ``ServerHandle``, then shut everything down.
    """
    handle = await start_server(
        device_class=dlna_device_class,
        local_ip="127.0.0.1",
        http_bind="127.0.0.1",
        http_port=dlna_http_port,
        stream_url=fake_radio_url,
        stream_title=DUMMY_STREAM_TITLE,
        stream_mime_type=DUMMY_MIME,
        forwarder=stream_forwarder,
    )

    try:
        yield handle
    finally:
        stream_forwarder.cancel_all()
        await handle.stop()


@pytest.fixture()
def dlna_base_uri(dlna_server: ServerHandle, dlna_http_port: int) -> str:
    """Return the base URI of the dlna-stream server."""
    return f"http://127.0.0.1:{dlna_server.port}"
