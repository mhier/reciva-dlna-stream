"""Fixtures for reciva-dlna-stream integration tests."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port

from reciva_dlna_stream.forwarder import StreamForwarder
from reciva_dlna_stream.server import make_device_class as _make_device_class
from reciva_dlna_stream.server import MediaServerDevice
from reciva_dlna_stream.server_lifecycle import ServerHandle, start_server
from reciva_dlna_stream.stream_config import StreamConfig

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
    """Return a fixed port for the reciva-dlna-stream HTTP server."""
    return unused_port()


@pytest.fixture()
async def fake_radio_server(dummy_mp3_data: bytes) -> AsyncIterator[TestServer]:
    """Start a tiny HTTP server that serves dummy MP3 data continuously.

    The server loops the dummy data indefinitely, so tests can read
    more than 16 KB without the connection closing.
    """

    async def handle_stream(request: web.Request) -> web.StreamResponse:
        """Serve the dummy MP3 as an infinite streaming response."""
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": DUMMY_MIME,
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request)

        chunk_size = 4096
        while True:
            for offset in range(0, len(dummy_mp3_data), chunk_size):
                chunk = dummy_mp3_data[offset : offset + chunk_size]
                try:
                    await response.write(chunk)
                except (ConnectionResetError, ConnectionAbortedError):
                    return response
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
async def fake_radio_server_flakey(dummy_mp3_data: bytes) -> AsyncIterator[TestServer]:
    """Start a fake radio server that fails after serving a limited amount of data.

    This simulates a remote stream that drops unexpectedly, allowing tests
    to exercise the buffer's auto-reconnect logic.

    The handler serves up to 8 KB of data on each connection attempt and
    then returns (client sees stream end). This triggers an EOF from the
    server's perspective, which causes the buffer's ``_run()`` loop to
    reattempt the connection.
    """

    async def handle_stream(request: web.Request) -> web.StreamResponse:
        """Serve a limited amount of data then end the stream."""
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": DUMMY_MIME,
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request)

        # Serve only a small amount of data then end cleanly
        served = 0
        chunk_size = 4096
        while served < 8192:
            chunk = dummy_mp3_data[served : served + chunk_size]
            try:
                await response.write(chunk)
            except (ConnectionResetError, ConnectionAbortedError):
                break
            served += len(chunk)
            await asyncio.sleep(0.001)

        return response

    app = web.Application()
    app.router.add_get("/radio/flakey", handle_stream)

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


# ---------------------------------------------------------------------------
# Shared device class factory
# ---------------------------------------------------------------------------


def make_device_class(
    forwarders: list[StreamForwarder],
    friendly_name: str = DUMMY_STREAM_TITLE,
    udn: str | None = None,
) -> type:
    """Create a MediaServerDevice subclass with the given forwarders and UDN.

    Delegates to ``server.make_device_class()`` which is now the canonical
    implementation shared between production and tests.
    """
    return _make_device_class(
        friendly_name=friendly_name,
        forwarders=forwarders,
        udn=udn,
    )


@pytest.fixture()
def dlna_device_class(stream_forwarder: StreamForwarder) -> type:
    """
    Create a MediaServerDevice subclass that sets up the forwarders
    on construction.
    """
    return make_device_class(
        forwarders=[stream_forwarder],
        friendly_name=DUMMY_STREAM_TITLE,
    )


@pytest.fixture()
async def dlna_server(
    dlna_device_class: type,
    fake_radio_url: str,
    stream_forwarder: StreamForwarder,
    dlna_http_port: int,
) -> AsyncIterator[ServerHandle]:
    """
    Start a fully-configured reciva-dlna-stream server using the same startup
    logic as ``__main__.py`` (``start_server`` from ``server_lifecycle``),
    yield the ``ServerHandle``, then shut everything down.
    """
    streams = [StreamConfig(url=fake_radio_url, name=DUMMY_STREAM_TITLE, mime_type=DUMMY_MIME)]
    forwarders = [stream_forwarder]

    handle = await start_server(
        device_class=dlna_device_class,
        local_ip="127.0.0.1",
        http_bind="127.0.0.1",
        http_port=dlna_http_port,
        streams=streams,
        forwarders=forwarders,
    )

    try:
        yield handle
    finally:
        await stream_forwarder.cancel_all()
        await handle.stop()


@pytest.fixture()
def dlna_base_uri(dlna_server: ServerHandle, dlna_http_port: int) -> str:
    """Return the base URI of the reciva-dlna-stream server."""
    return f"http://127.0.0.1:{dlna_server.port}"


# ---------------------------------------------------------------------------
# Multi-stream fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dlna_device_class_multi(
    stream_forwarder: StreamForwarder,
    stream_forwarder_alt: StreamForwarder,
) -> type:
    """
    Create a MediaServerDevice subclass with two forwarders for multi-stream tests.
    """
    return make_device_class(
        forwarders=[stream_forwarder, stream_forwarder_alt],
        friendly_name="Multi-Stream Test",
    )


@pytest.fixture()
def stream_forwarder_alt(fake_radio_url: str) -> StreamForwarder:
    """Create a second StreamForwarder for multi-stream tests."""
    return StreamForwarder(stream_url=fake_radio_url, mime_type=DUMMY_MIME)


@pytest.fixture()
async def dlna_server_multi(
    dlna_device_class_multi: type,
    fake_radio_url: str,
    stream_forwarder: StreamForwarder,
    stream_forwarder_alt: StreamForwarder,
    dlna_http_port: int,
) -> AsyncIterator[ServerHandle]:
    """
    Start a multi-stream reciva-dlna-stream server with two streams.
    """
    streams = [
        StreamConfig(url=fake_radio_url, name=DUMMY_STREAM_TITLE, mime_type=DUMMY_MIME),
        StreamConfig(url=fake_radio_url, name="Alt Radio Stream", mime_type=DUMMY_MIME),
    ]
    forwarders = [stream_forwarder, stream_forwarder_alt]

    handle = await start_server(
        device_class=dlna_device_class_multi,
        local_ip="127.0.0.1",
        http_bind="127.0.0.1",
        http_port=dlna_http_port,
        streams=streams,
        forwarders=forwarders,
    )

    try:
        yield handle
    finally:
        await stream_forwarder.cancel_all()
        await stream_forwarder_alt.cancel_all()
        await handle.stop()


@pytest.fixture()
def dlna_base_uri_multi(dlna_server_multi: ServerHandle) -> str:
    """Return the base URI of the multi-stream server."""
    return f"http://127.0.0.1:{dlna_server_multi.port}"
