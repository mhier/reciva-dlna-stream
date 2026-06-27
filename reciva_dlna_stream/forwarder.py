"""HTTP stream forwarder.

Fetches a remote internet radio stream on demand and forwards it to a DLNA client.
The remote connection is only active while a client is connected.
"""

from __future__ import annotations

import asyncio
import datetime
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
# Maximum buffer size for cached stream data (~1 minute of audio at 320 kbps)
_MAX_BUFFER_SIZE = 4 * 1024 * 1024
# Delay between reconnection attempts when the remote stream fails
_RECONNECT_DELAY = 5
# Grace period after last client disconnects before stopping the buffer (seconds)
_DISCONNECT_TIMEOUT = 10

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

# ---------------------------------------------------------------------------
# Synthetic MP3 footer (ID3v1.1 tag)
#
# Reciva radios probe the declared file size by requesting the last 129 bytes
# of the "file" to validate the Content-Length.  Since this is a live stream
# with no real end, we serve a synthetic ID3v1.1 tag for any range that
# intersects the last 128 bytes of our fake file size.
# ---------------------------------------------------------------------------
_CURRENT_YEAR = str(datetime.datetime.now().year).encode("ascii")

_ID3V1_TAG = (
    b"TAG"
    + b"Internet Radio\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    + b"\x00" * 30  # Artist
    + b"\x00" * 30  # Album
    + _CURRENT_YEAR  # Year
    + b"\x00" * 28  # Comment (null-padded)
    + b"\x00"       # v1.1 separator
    + b"\x01"       # Track 1
    + b"\xff"       # Genre 255 (Unknown)
)

assert len(_ID3V1_TAG) == 128, f"ID3v1 tag must be 128 bytes, got {len(_ID3V1_TAG)}"

_ID3_LAST_FRAME_BYTE = b"\x00"

_SYNTHETIC_FOOTER = _ID3_LAST_FRAME_BYTE + _ID3V1_TAG
_FOOTER_LENGTH = len(_SYNTHETIC_FOOTER)  # 129

_FOOTER_START = _FAKE_CONTENT_LENGTH - _FOOTER_LENGTH


class StreamBuffer:
    """On-demand ring buffer that reads from the remote stream only while needed.

    A background ``asyncio.Task`` reads data from the remote Icecast stream
    and appends it to a ``bytearray`` buffer.  The task only runs while at
    least one HTTP client is connected — when idle, no remote connection,
    no ``ClientSession``, and no ``TCPConnector`` exist.

    The buffer grows up to ``_MAX_BUFFER_SIZE`` (4 MB).  Once full, the
    oldest data is discarded.
    """

    def __init__(self, stream_url: str) -> None:
        self._stream_url = stream_url
        self._buffer = bytearray()
        self._total_read: int = 0
        self._task: asyncio.Task[None] | None = None
        self._event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._stopped = False
        # Created in _run(), closed in _close_session()
        self._connector: aiohttp.TCPConnector | None = None
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background buffer task."""
        self._stopped = False
        self._buffer = bytearray()
        self._total_read = 0
        self._event = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the background buffer task."""
        self._stopped = True
        self._event.set()  # wake any waiting readers so they can exit
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._close_session()

    async def _close_session(self) -> None:
        """Close the long-lived aiohttp session and connector."""
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._connector is not None:
            await self._connector.close()
            self._connector = None

    @property
    def buffered_bytes(self) -> int:
        """Return the number of bytes currently in the buffer."""
        return len(self._buffer)

    @property
    def total_bytes_read(self) -> int:
        """Total bytes ever read from the remote stream (may have been trimmed)."""
        return self._total_read

    @property
    def is_running(self) -> bool:
        """Whether the buffer background task is currently running."""
        return self._task is not None

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Fetch the remote stream and fill the buffer.

        Creates a fresh ``aiohttp.ClientSession`` and ``TCPConnector``
        on each start, and closes them on exit.  This method is called
        from ``start()`` and exits when ``stop()`` is called.
        """
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=_CONNECT_TIMEOUT,
            sock_read=_READ_TIMEOUT,
        )
        self._connector = aiohttp.TCPConnector(limit=1)
        self._session = aiohttp.ClientSession(
            connector=self._connector, timeout=timeout
        )

        try:
            while not self._stopped:
                try:
                    _LOGGER.info(
                        "Buffer: fetching remote stream: %s",
                        self._stream_url,
                    )
                    async with self._session.get(
                        self._stream_url
                    ) as remote_resp:
                        _LOGGER.info(
                            "Buffer: remote stream connected: status=%d",
                            remote_resp.status,
                        )
                        async for chunk in remote_resp.content.iter_chunked(
                            _BUFFER_SIZE
                        ):
                            if self._stopped:
                                return
                            if not chunk:
                                continue

                            async with self._lock:
                                self._buffer.extend(chunk)
                                self._total_read += len(chunk)

                                # Trim if buffer exceeds max size
                                if len(self._buffer) > _MAX_BUFFER_SIZE:
                                    excess = len(self._buffer) - _MAX_BUFFER_SIZE
                                    del self._buffer[:excess]
                                    _LOGGER.debug(
                                        "Buffer: trimmed %d bytes", excess
                                    )

                            self._event.set()

                            await asyncio.sleep(0)

                        # Remote stream ended cleanly (not an error, just retry)
                        _LOGGER.info(
                            "Buffer: remote stream ended, reconnecting"
                        )

                except asyncio.CancelledError:
                    return
                except Exception:
                    _LOGGER.exception(
                        "Buffer: error reading stream, reconnecting"
                    )

                if not self._stopped:
                    await asyncio.sleep(_RECONNECT_DELAY)
        finally:
            await self._close_session()

    # ------------------------------------------------------------------
    # Read interface for HTTP handlers
    # ------------------------------------------------------------------

    async def read(
        self,
        offset: int,
        size: int,
        timeout: float = 30.0,
    ) -> bytes:
        """Read *size* bytes starting at *offset* from the buffer.

        Blocks until the requested data is available or *timeout* expires.
        Returns the requested bytes (may be shorter than *size* if the
        stream ends or timeout occurs).
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            async with self._lock:
                if offset < self._total_read:
                    # offset is within what we've read from remote
                    local_offset = len(self._buffer) - (
                        self._total_read - offset
                    )
                    if local_offset >= 0 and local_offset + size <= len(self._buffer):
                        return bytes(
                            self._buffer[local_offset : local_offset + size]
                        )
                    elif local_offset >= 0:
                        # partial data available
                        return bytes(self._buffer[local_offset:])

                    # offset is before what we have in the trimmed buffer
                    if local_offset < 0:
                        raise ValueError(
                            f"Offset {offset} has already been trimmed"
                            f" (buffer starts at "
                            f"{self._total_read - len(self._buffer)})"
                        )

            if asyncio.get_running_loop().time() >= deadline:
                _LOGGER.warning(
                    "Buffer read timeout: offset=%d size=%d "
                    "buffered=%d total_read=%d",
                    offset,
                    size,
                    len(self._buffer),
                    self._total_read,
                )
                return b""

            await asyncio.wait_for(
                self._event.wait(), timeout=max(0.1, deadline - asyncio.get_running_loop().time())
            )
            # Clear the event so we don't spin — if new data arrives,
            # _run() will set() it again and wake us.
            self._event.clear()


class StreamForwarder:
    """Manages forwarding a remote stream to HTTP clients on demand.

    The underlying ``StreamBuffer`` runs while at least one client
    is connected, plus a grace period (``_DISCONNECT_TIMEOUT``)
    after the last client disconnects. This allows quick reconnections
    (e.g. sequential range requests or re-buffering) without losing
    the accumulated buffer data.
    """

    def __init__(self, stream_url: str, mime_type: str, verbose_logging: bool = False) -> None:
        self._stream_url = stream_url
        self._mime_type = mime_type
        self._buffer = StreamBuffer(stream_url)
        self._verbose_logging = verbose_logging

        self._active_connections: set[asyncio.Task[None]] = set()
        self._disconnect_timer: asyncio.Task[None] | None = None

    @property
    def active_connection_count(self) -> int:
        """Return number of active connections."""
        return len(self._active_connections)

    @property
    def pending_disconnect(self) -> bool:
        """Whether a disconnect timer is pending (grace period active)."""
        return self._disconnect_timer is not None and not self._disconnect_timer.done()

    async def _ensure_buffer_running(self) -> None:
        """Start the buffer if it is not already running.

        Also cancels any pending disconnect timer so the buffer
        continues serving data uninterrupted.
        """
        await self._cancel_disconnect_timer()
        if not self._buffer.is_running:
            _LOGGER.info("Starting buffer on first client connection")
            await self._buffer.start()

    async def _cancel_disconnect_timer(self) -> None:
        """Cancel the pending disconnect timer, if any."""
        if self._disconnect_timer is not None and not self._disconnect_timer.done():
            self._disconnect_timer.cancel()
            try:
                await self._disconnect_timer
            except asyncio.CancelledError:
                pass
            self._disconnect_timer = None

    async def _maybe_stop_buffer(self) -> None:
        """Start a disconnect timer if no clients remain.

        Instead of stopping the buffer immediately, this starts
        a grace period timer. The buffer keeps running and
        accumulating data. If a new client connects before the
        timer fires, the timer is cancelled. If the timer fires,
        the buffer is stopped.
        """
        if self._active_connections or self._disconnect_timer is not None:
            return
        _LOGGER.info(
            "No active clients, starting disconnect timer (%ds)",
            _DISCONNECT_TIMEOUT,
        )
        self._disconnect_timer = asyncio.create_task(
            self._disconnect_timer_task()
        )

    async def _disconnect_timer_task(self) -> None:
        """Wait for the grace period, then stop the buffer."""
        try:
            await asyncio.sleep(_DISCONNECT_TIMEOUT)
            if not self._active_connections:
                _LOGGER.info(
                    "Disconnect timer expired: stopping buffer"
                )
                await self._buffer.stop()
        except asyncio.CancelledError:
            pass
        finally:
            self._disconnect_timer = None

    async def start_buffer(self) -> None:
        """Start the background buffer task (legacy/public API)."""
        await self._buffer.start()

    async def stop_buffer(self) -> None:
        """Stop the background buffer task (legacy/public API)."""
        await self._buffer.stop()

    # ------------------------------------------------------------------
    # Public request handler
    # ------------------------------------------------------------------

    async def handle_request(self, request: Request) -> StreamResponse:
        """Handle an incoming HTTP request from a DLNA client.

        The stream is presented as a very large (24 h) seekable file via
        a fake ``Content-Length``.

        The buffer is started on the first client connection and stopped
        when the last client disconnects.

        The Reciva radio probes the file by requesting:
          1. ``bytes=0-131071`` (first 128 KB) — served from ring buffer
          2. ``bytes=<end-128>-<end>`` (last 129 bytes) — served from a
             synthetic ID3v1.1 tag to validate the declared file size.
        """
        _LOGGER.info(
            "=== Client connected: %s ===",
            request.remote,
        )

        # Track this connection before any response to ensure proper
        # lifecycle management
        task = asyncio.current_task()
        assert task is not None
        self._active_connections.add(task)

        # Footer ranges don't need the buffer — serve immediately
        range_header = request.headers.get("Range")
        if range_header:
            range_spec = self._parse_range(range_header)
            if range_spec is not None:
                range_start, range_end = range_spec
                _LOGGER.info(
                    "Range: bytes=%d-%d (file size=%d)",
                    range_start,
                    range_end,
                    _FAKE_CONTENT_LENGTH,
                )
                if range_end >= _FOOTER_START:
                    try:
                        return await self._handle_footer_range(
                            request, range_start, range_end
                        )
                    finally:
                        self._active_connections.discard(task)
                        await self._maybe_stop_buffer()

        # Buffer data needed — ensure buffer is running
        await self._ensure_buffer_running()

        try:
            if range_header and range_spec is not None:
                range_start, range_end = range_spec
                return await self._handle_buffer_range(
                    request, range_start, range_end
                )

            if range_header:
                _LOGGER.info(
                    "Range: %s (unparseable, treating as full request)",
                    range_header,
                )
            _LOGGER.info("Headers: %s", dict(request.headers))

            return await self._handle_full_stream(request)
        finally:
            self._active_connections.discard(task)
            await self._maybe_stop_buffer()

    @property
    def fake_content_length(self) -> int:
        """Return the fake content length."""
        return _FAKE_CONTENT_LENGTH

    # ------------------------------------------------------------------
    # Synthetic footer handling
    # ------------------------------------------------------------------

    async def _handle_footer_range(
        self,
        request: Request,
        range_start: int,
        range_end: int,
    ) -> StreamResponse:
        """Serve a 206 response for a range that overlaps the synthetic footer."""
        _LOGGER.info(
            "Serving synthetic footer for range bytes=%d-%d (file end)",
            range_start,
            range_end,
        )

        footer_offset_start = max(range_start - _FOOTER_START, 0)
        footer_offset_end = min(range_end - _FOOTER_START + 1, _FOOTER_LENGTH)

        data = _SYNTHETIC_FOOTER[footer_offset_start:footer_offset_end]
        content_length = len(data)

        resp_headers = {
            "Content-Type": self._mime_type,
            "Content-Length": str(content_length),
            "Content-Range": (
                f"bytes {range_start}-{range_start + content_length - 1}"
                f"/{_FAKE_CONTENT_LENGTH}"
            ),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "TransferMode.DLNA.ORG": _DLNA_TRANSFER_MODE,
        }

        response = StreamResponse(status=206, headers=resp_headers)
        await response.prepare(request)
        await response.write(data)
        _LOGGER.info(
            "Served %d bytes of synthetic footer (range bytes=%d-%d)",
            content_length,
            range_start,
            range_start + content_length - 1,
        )
        return response

    # ------------------------------------------------------------------
    # Buffer range handling
    # ------------------------------------------------------------------

    async def _handle_buffer_range(
        self,
        request: Request,
        range_start: int,
        range_end: int,
    ) -> StreamResponse:
        """Serve a 206 response from the ring buffer.

        If the requested offset has been trimmed from the ring buffer,
        returns 416 Range Not Satisfiable instead.
        """
        _LOGGER.info(
            "Serving buffer range bytes=%d-%d",
            range_start,
            range_end,
        )

        content_length = range_end - range_start + 1

        # Try the first read before preparing the response, so we can
        # return 416 if the offset has been trimmed from the buffer.
        try:
            first_chunk = await self._buffer.read(range_start, _BUFFER_SIZE)
        except ValueError as exc:
            _LOGGER.warning(
                "Buffer range error (offset trimmed): %s", exc
            )
            resp_headers = {
                "Content-Type": self._mime_type,
                "Content-Range": (
                    f"bytes */{_FAKE_CONTENT_LENGTH}"
                ),
                "Accept-Ranges": "bytes",
            }
            response = StreamResponse(status=416, headers=resp_headers)
            await response.prepare(request)
            return response

        resp_headers = {
            "Content-Type": self._mime_type,
            "Content-Length": str(content_length),
            "Content-Range": (
                f"bytes {range_start}-{range_end}/{_FAKE_CONTENT_LENGTH}"
            ),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "TransferMode.DLNA.ORG": _DLNA_TRANSFER_MODE,
        }

        response = StreamResponse(status=206, headers=resp_headers)
        await response.prepare(request)

        try:
            # Write the first chunk (already read)
            try:
                await response.write(first_chunk)
            except (ConnectionResetError, ConnectionAbortedError) as exc:
                _LOGGER.warning(
                    "Client disconnected during write: %s", exc
                )
                return response

            bytes_sent = len(first_chunk)
            offset = range_start + bytes_sent
            remaining = content_length - bytes_sent

            # Read from the ring buffer in chunks
            while remaining > 0:
                chunk_size = min(remaining, _BUFFER_SIZE)
                chunk = await self._buffer.read(offset, chunk_size)
                if not chunk:
                    _LOGGER.warning(
                        "Buffer returned empty data at offset %d, "
                        "sent %d/%d bytes before timeout",
                        offset,
                        bytes_sent,
                        content_length,
                    )
                    break

                try:
                    await response.write(chunk)
                except (ConnectionResetError, ConnectionAbortedError) as exc:
                    _LOGGER.warning(
                        "Client disconnected during write: %s", exc
                    )
                    break

                bytes_sent += len(chunk)
                offset += len(chunk)
                remaining -= len(chunk)

                # Log every chunk until 2 KB sent, then every 512 KB thereafter
                LOG_INTERVAL = 512 * 1024
                if bytes_sent < 2048 or (bytes_sent % LOG_INTERVAL) == 0:
                    _LOGGER.debug(
                        "Buffer range: forwarded %d/%d bytes",
                        bytes_sent,
                        content_length,
                    )

            _LOGGER.info(
                "Buffer range fulfilled: bytes %d-%d, sent %d bytes",
                range_start,
                range_end,
                bytes_sent,
            )
        except Exception:
            _LOGGER.exception(
                "Error serving buffer range %d-%d", range_start, range_end
            )
        return response

    # ------------------------------------------------------------------
    # Full stream (no Range header) — also from buffer
    # ------------------------------------------------------------------

    async def _handle_full_stream(self, request: Request) -> StreamResponse:
        """Serve 200 OK and stream from the ring buffer indefinitely."""
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

        bytes_sent = 0
        try:
            while not self._buffer._stopped:
                chunk = await self._buffer.read(bytes_sent, _BUFFER_SIZE)
                if not chunk:
                    # Buffer timed out — check if the buffer was stopped
                    # (disconnect timer fired) while we were waiting.
                    if self._buffer._stopped:
                        break
                    await asyncio.sleep(0)
                    continue

                try:
                    await response.write(chunk)
                except (ConnectionResetError, ConnectionAbortedError) as exc:
                    _LOGGER.warning(
                        "Client disconnected during write: %s", exc
                    )
                    break

                bytes_sent += len(chunk)

                # Log every chunk until 2 KB sent, then every 512 KB thereafter
                if self._verbose_logging:
                    LOG_INTERVAL = 512 * 1024
                    if bytes_sent < 2048 or (bytes_sent % LOG_INTERVAL) == 0:
                        _LOGGER.debug("Forwarded %d bytes so far", bytes_sent)

                await asyncio.sleep(0)
        except asyncio.CancelledError:
            _LOGGER.debug("Stream forwarding cancelled")
            raise
        except Exception:
            _LOGGER.exception("Error forwarding stream")
        finally:
            _LOGGER.info(
                "=== Client disconnected (sent %d bytes) ===",
                bytes_sent,
            )

        return response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_range(
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
        return start, start

    async def cancel_all(self) -> None:
        """Cancel all active stream forwarding tasks and the disconnect timer.

        Properly awaits the disconnect timer cancellation so no lingering
        tasks remain during shutdown. Also awaits the cancelled connection
        tasks so their ``finally`` blocks (including ``_maybe_stop_buffer()``)
        complete before cleanup finishes.
        """
        await self._cancel_disconnect_timer()
        tasks = self._active_connections.copy()
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                _LOGGER.exception("Unexpected error in cancelled task", exc_info=result)
        # The tasks' finally blocks (via _maybe_stop_buffer()) may have
        # started a new disconnect timer. Cancel it again for clean state.
        await self._cancel_disconnect_timer()
