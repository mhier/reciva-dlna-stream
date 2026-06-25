"""HTTP stream forwarder.

Fetches a remote internet radio stream on demand and forwards it to a DLNA client.
The remote connection is only active while a client is connected.
"""

from __future__ import annotations

import asyncio
import logging
import re

import aiohttp
from aiohttp.web import Request, StreamResponse

_LOGGER = logging.getLogger(__name__)

# Buffer size for reading from the remote stream
_BUFFER_SIZE = 64 * 1024  # 64 KB
# Timeout for establishing the remote stream connection
_CONNECT_TIMEOUT = 30
# How long to wait for data from the remote stream before checking if client is gone
_READ_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Fake content length for the live stream
#
# Many DLNA clients (e.g. Reciva radios) treat streams as files.  If the
# server does not advertise a Content-Length the client may reject it.
# We lie about the length — ~24 hours of 128 kbps MP3 — to satisfy the
# client's file-oriented expectations.
# ---------------------------------------------------------------------------
# 24 h * 3600 s/h * 128 kbps / 8 bits/byte * 1024 bytes/kbyte
_FAKE_CONTENT_LENGTH = 24 * 3600 * 128 * 1024 // 8

_RANGE_RE = re.compile(r"^bytes=(?P<start>\d+)-(?P<end>\d*)$")

# ---------------------------------------------------------------------------
# DLNA transfer mode header values
# ---------------------------------------------------------------------------
_DLNA_TRANSFER_MODE = "Streaming"


class StreamForwarder:
    """Manages forwarding a remote stream to a single HTTP client."""

    def __init__(self, stream_url: str, mime_type: str) -> None:
        self._stream_url = stream_url
        self._mime_type = mime_type

        self._active_connections: set[asyncio.Task[None]] = set()

    @property
    def active_connection_count(self) -> int:
        """Return number of active connections."""
        return len(self._active_connections)

    async def handle_request(self, request: Request) -> StreamResponse:
        """Handle an incoming HTTP request from a DLNA client.

        The stream is presented as a very large (24 h) seekable file via
        a fake ``Content-Length``, but **all** requests (including Range
        requests) are answered with ``200 OK`` and stream data is sent
        indefinitely until the client disconnects.

        The Reciva radio probes the file by requesting ``bytes=0-131071``
        then ``bytes=<last 129 bytes>`` to verify the declared file size.
        Since this is a live stream there is no "end" to serve, so we
        ignore the Range header and always stream fresh data from the
        remote source.  The fake ``Accept-Ranges: bytes`` header keeps
        the radio from rejecting the resource as unseekable.
        """
        _LOGGER.info(
            "=== Client connected: %s ===",
            request.remote,
        )

        range_header = request.headers.get("Range")
        if range_header:
            _LOGGER.info("Range: %s (ignored for live stream)", range_header)
        _LOGGER.info("Headers: %s", dict(request.headers))

        # Present as a large seekable file, but always stream live data.
        resp_headers = {
            "Content-Type": self._mime_type,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Content-Disposition": 'inline; filename="stream.mp3"',
            "Content-Length": str(_FAKE_CONTENT_LENGTH),
            "Accept-Ranges": "bytes",
            "TransferMode.DLNA.ORG": _DLNA_TRANSFER_MODE,
        }

        response = StreamResponse(status=200, headers=resp_headers)
        _LOGGER.info(
            "Responding 200 OK, Content-Length=%s, streaming %s",
            _FAKE_CONTENT_LENGTH,
            self._mime_type,
        )

        await response.prepare(request)

        task = asyncio.current_task()
        assert task is not None
        self._active_connections.add(task)

        bytes_sent = 0
        try:
            bytes_sent = await self._forward_stream(response)
        except asyncio.CancelledError:
            _LOGGER.debug("Stream forwarding cancelled for %s", request.remote)
            raise
        except Exception:
            _LOGGER.exception("Error forwarding stream to %s", request.remote)
        finally:
            self._active_connections.discard(task)
            _LOGGER.info(
                "=== Client disconnected: %s (sent %d bytes) ===",
                request.remote,
                bytes_sent,
            )

        return response

    async def _parse_range(
        self, range_header: str
    ) -> tuple[int, int] | None:
        """Parse a Range header. Returns (start, end) or None."""
        match = _RANGE_RE.match(range_header)
        if not match:
            _LOGGER.debug("Unsupported range header: %s", range_header)
            return None
        start = int(match.group("start"))
        end_str = match.group("end")
        if end_str:
            return start, int(end_str)
        return start, start  # single byte if no end specified

    async def _forward_stream(
        self,
        response: StreamResponse,
        range_spec: tuple[int, int] | None = None,
    ) -> int:
        """Fetch remote stream and forward data to the client response.

        When *range_spec* is set the method stops after fulfilling the
        requested byte window.  Without a range it streams indefinitely.
        """
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=_CONNECT_TIMEOUT,
            sock_read=_READ_TIMEOUT,
        )

        connector = aiohttp.TCPConnector(limit=1)

        bytes_sent = 0
        range_start = range_spec[0] if range_spec else 0
        range_end = range_spec[1] if range_spec else None

        # How many bytes to skip from the remote stream (for range seeking)
        bytes_to_skip = range_start

        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            _LOGGER.info("Fetching remote stream: %s", self._stream_url)
            async with session.get(self._stream_url) as remote_resp:
                _LOGGER.info(
                    "Remote stream connected: status=%d",
                    remote_resp.status,
                )

                async for chunk in remote_resp.content.iter_chunked(_BUFFER_SIZE):
                    if not chunk:
                        _LOGGER.debug("Empty chunk received, stream ended")
                        break

                    # Skip bytes before the requested range start
                    if bytes_to_skip > 0:
                        if len(chunk) <= bytes_to_skip:
                            bytes_to_skip -= len(chunk)
                            continue
                        chunk = chunk[bytes_to_skip:]
                        bytes_to_skip = 0

                    # Log first forwarded chunk details
                    if bytes_sent == 0:
                        _LOGGER.info(
                            "First chunk: %d bytes, first 32 hex: %s",
                            len(chunk),
                            chunk[:32].hex(),
                        )

                    # Enforce range end bound if set
                    if range_end is not None:
                        remaining = range_end - range_start - bytes_sent + 1
                        if remaining <= 0:
                            _LOGGER.debug(
                                "Reached range end at byte %d", range_end
                            )
                            break
                        if len(chunk) > remaining:
                            chunk = chunk[:remaining]

                    try:
                        await response.write(chunk)
                    except (ConnectionResetError, ConnectionAbortedError) as exc:
                        _LOGGER.warning(
                            "Client disconnected during write: %s", exc
                        )
                        break

                    bytes_sent += len(chunk)

                    if (
                        bytes_sent < 1024 * 2
                        or bytes_sent % (512 * 1024) < _BUFFER_SIZE
                    ):
                        _LOGGER.debug("Forwarded %d bytes so far", bytes_sent)

                    # Give other tasks a chance to run
                    await asyncio.sleep(0)

                _LOGGER.info(
                    "Remote stream %s, forwarded %d bytes total",
                    "ended" if range_end is None else "finished (range fulfilled)",
                    bytes_sent,
                )

        return bytes_sent

    def cancel_all(self) -> None:
        """Cancel all active stream forwarding tasks."""
        for task in self._active_connections.copy():
            task.cancel()
