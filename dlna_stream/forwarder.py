"""HTTP stream forwarder.

Fetches a remote internet radio stream on demand and forwards it to a DLNA client.
The remote connection is only active while a client is connected.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp.web import Request, StreamResponse

_LOGGER = logging.getLogger(__name__)

# Buffer size for reading from the remote stream
_BUFFER_SIZE = 64 * 1024  # 64 KB
# Timeout for establishing the remote stream connection
_CONNECT_TIMEOUT = 30
# How long to wait for data from the remote stream before checking if client is gone
_READ_TIMEOUT = 10


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

        This method is called when the client requests the audio stream.
        It creates a streaming response and forwards the remote stream data
        to the client.
        """
        _LOGGER.info(
            "Client connected: %s (range: %s)",
            request.remote,
            request.headers.get("Range", "none"),
        )

        response = StreamResponse(
            status=200,
            headers={
                "Content-Type": self._mime_type,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Transfer-Encoding": "chunked",
                "Content-Disposition": 'inline; filename="stream.mp3"',
            },
        )

        # Prepare the response so we can write to it
        await response.prepare(request)

        task = asyncio.current_task()
        assert task is not None
        self._active_connections.add(task)

        try:
            await self._forward_stream(response)
        except asyncio.CancelledError:
            _LOGGER.debug("Stream forwarding cancelled for %s", request.remote)
            raise
        except Exception:
            _LOGGER.exception("Error forwarding stream to %s", request.remote)
        finally:
            self._active_connections.discard(task)
            _LOGGER.info("Client disconnected: %s", request.remote)

        return response

    async def _forward_stream(self, response: StreamResponse) -> None:
        """Fetch remote stream and forward data to the client response."""
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=_CONNECT_TIMEOUT,
            sock_read=_READ_TIMEOUT,
        )

        connector = aiohttp.TCPConnector(limit=1)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            _LOGGER.debug("Connecting to remote stream: %s", self._stream_url)
            async with session.get(self._stream_url) as remote_resp:
                if remote_resp.status != 200:
                    _LOGGER.warning(
                        "Remote stream returned status %d",
                        remote_resp.status,
                    )
                    # Still try to read whatever we got
                _LOGGER.debug(
                    "Remote stream connected, forwarding (status=%d)",
                    remote_resp.status,
                )

                async for chunk in remote_resp.content.iter_chunked(_BUFFER_SIZE):
                    if not chunk:
                        break
                    try:
                        await response.write(chunk)
                    except (ConnectionResetError, ConnectionAbortedError):
                        _LOGGER.debug("Client disconnected during write")
                        break

                    # Give other tasks a chance to run
                    await asyncio.sleep(0)

                _LOGGER.debug("Remote stream finished")

    def cancel_all(self) -> None:
        """Cancel all active stream forwarding tasks."""
        for task in self._active_connections.copy():
            task.cancel()
