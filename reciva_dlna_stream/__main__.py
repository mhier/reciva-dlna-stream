"""Main entry point for reciva-dlna-stream.

Usage:
    reciva-dlna-stream --stream-url "https://example.com/radio.mp3"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
import sys
from functools import partial
from typing import cast
from uuid import uuid4

from .forwarder import StreamForwarder
from .server import MediaServerDevice
from .server_lifecycle import ServerHandle, start_server
from .stream_config import ServerConfig, StreamConfig, load_config

# ---------------------------------------------------------------------------
# Monkey-patch: increase SSDP multicast TTL from 2 to 4
#
# The UPnP Device Architecture v2.0 (section 1.2.2) mandates a TTL of 4 for
# SSDP multicast messages. The library hard-codes 2.
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

_LOGGER = logging.getLogger("reciva_dlna_stream")


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build argument parser."""
    parser = argparse.ArgumentParser(
        description="DLNA Media Server for internet radio streaming",
    )
    parser.add_argument(
        "--stream-url",
        help="URL of the internet radio stream to forward",
    )
    parser.add_argument(
        "--name",
        default="Internet Radio Stream",
        help="Friendly name for the DLNA server (default: %(default)s)",
    )
    parser.add_argument(
        "--config",
        help="Path to JSON config file with stream definitions",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="HTTP server port (0 = auto-assign, default: %(default)s)",
    )
    parser.add_argument(
        "--mime-type",
        default="audio/mpeg",
        help="MIME type of the stream (default: %(default)s)",
    )
    parser.add_argument(
        "--bind-ip",
        default="0.0.0.0",
        help="IP address to bind HTTP server to (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )
    return parser


async def async_main(args: argparse.Namespace) -> None:
    """Main async entry point."""
    local_ip = _get_local_ip()
    if not local_ip:
        _LOGGER.error(
            "Could not determine local IP address. "
            "Check network connectivity."
        )
        sys.exit(1)

    _LOGGER.info("=" * 50)
    _LOGGER.info("reciva-dlna-stream starting up")
    _LOGGER.info("Local IP (detected): %s", local_ip)
    _LOGGER.info("Bind IP (from args): %s", args.bind_ip)

    # If --bind-ip is 0.0.0.0, we still need a concrete IP for SSDP source
    # and device XML URLs. Keep using the detected local_ip for those,
    # but use bind_ip for the HTTP server socket.
    http_bind = args.bind_ip or "0.0.0.0"
    _LOGGER.info("HTTP server binding to: %s", http_bind)
    _LOGGER.info("SSDP source IP: %s", local_ip)

    # ------------------------------------------------------------------
    # Resolve streams: either from --config or from --stream-url + --name
    # ------------------------------------------------------------------

    if args.config:
        server_config = load_config(args.config)
    elif args.stream_url:
        server_config = ServerConfig(streams=[
            StreamConfig(url=args.stream_url, name=args.name, mime_type=args.mime_type),
        ])
    else:
        _LOGGER.error(
            "Either --config or --stream-url must be provided"
        )
        sys.exit(1)

    streams = list(server_config.streams)
    _LOGGER.info("Configured %d stream(s)", len(streams))

    # Create a StreamForwarder for each configured stream
    forwarders: list[StreamForwarder] = []
    for stream in streams:
        fwd = StreamForwarder(stream_url=stream.url, mime_type=stream.mime_type)
        forwarders.append(fwd)

    # Build server device class with a unique UDN and custom name
    # Use the first stream's name as the device friendly name
    device_name = streams[0].name if len(streams) == 1 else "reciva-dlna-stream"
    device_class = _make_device_class(device_name, forwarders)

    http_port = args.port or 0

    # Start everything with correct port handling
    stopper = await start_server(
        device_class=device_class,
        local_ip=local_ip,
        http_bind=http_bind,
        http_port=http_port,
        streams=streams,
        forwarders=forwarders,
    )
    base_uri = f"http://{local_ip}:{stopper.port}"

    _LOGGER.info("=" * 50)
    _LOGGER.info(
        "DLNA server started: '%s' at %s",
        device_name,
        base_uri,
    )
    _LOGGER.info("Device XML: %s/device.xml", base_uri)
    for idx, stream in enumerate(streams):
        _LOGGER.info("Stream %d: %s <- %s", idx, stream.name, stream.url)
    _LOGGER.info("SSDP advertisements being sent every ~30s")
    _LOGGER.info("Waiting for DLNA clients on the network...")
    _LOGGER.info("=" * 50)
    _LOGGER.info("Press Ctrl+C to stop")

    # Wait for shutdown
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        _LOGGER.info("Shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    await stop_event.wait()

    # Cleanup: cancel active streaming tasks first, then stop buffers + SSDP
    for fwd in forwarders:
        fwd.cancel_all()
    await stopper.stop()


# ---------------------------------------------------------------------------
# Local IP detection
# ---------------------------------------------------------------------------


def _get_local_ip() -> str | None:
    """Get the local IP address of the machine."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Device class factory
# ---------------------------------------------------------------------------


def _make_device_class(friendly_name: str, forwarders: list[StreamForwarder]) -> type:
    """Create a MediaServerDevice subclass with a unique UDN and custom name."""
    udn = f"uuid:{uuid4()}"

    class CustomMediaServerDevice(MediaServerDevice):
        """MediaServer with custom UDN and name."""

        DEVICE_DEFINITION = MediaServerDevice.DEVICE_DEFINITION._replace(
            udn=udn,
            friendly_name=friendly_name,
        )

        def __init__(
            self,
            requester: object,
            base_uri: str,
            boot_id: int = 1,
            config_id: int = 1,
        ) -> None:
            """Initialize and set forwarders."""
            super().__init__(
                requester=requester,
                base_uri=base_uri,
                boot_id=boot_id,
                config_id=config_id,
            )
            self.set_forwarders(forwarders)

    return CustomMediaServerDevice


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Main entry point."""
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Reduce noise from libraries, but enable SSDP/UPnP traffic logging in verbose mode
    if args.verbose:
        logging.getLogger("async_upnp_client").setLevel(logging.DEBUG)
    else:
        logging.getLogger("async_upnp_client").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
