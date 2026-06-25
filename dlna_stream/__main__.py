"""Main entry point for dlna-stream.

Usage:
    dlna-stream --stream-url "https://example.com/radio.mp3"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from uuid import uuid4

from async_upnp_client.server import UpnpServer

from .forwarder import StreamForwarder
from .server import MediaServerDevice

_LOGGER = logging.getLogger("dlna_stream")


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build argument parser."""
    parser = argparse.ArgumentParser(
        description="DLNA Media Server for internet radio streaming",
    )
    parser.add_argument(
        "--stream-url",
        required=True,
        help="URL of the internet radio stream to forward",
    )
    parser.add_argument(
        "--name",
        default="Internet Radio Stream",
        help="Friendly name for the DLNA server (default: %(default)s)",
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

    _LOGGER.info("Local IP: %s", local_ip)

    # Create stream forwarder
    forwarder = StreamForwarder(
        stream_url=args.stream_url,
        mime_type=args.mime_type,
    )

    # Build server device class with a unique UDN and custom name
    device_class = _make_device_class(args.name, forwarder)

    # Determine port
    http_port = args.port or 0

    # Create and start UPnP server (includes HTTP server, SSDP, etc.)
    source = (local_ip, 0)
    server = UpnpServer(
        device_class,
        source=source,
        http_port=http_port,
        options={
            "ssdp_search_responder_options": {
                "ssdp_search_responder_always_rootdevice": True,
            },
        },
    )

    await server.async_start()

    # Get actual port (in case of auto-assign)
    assert server._site is not None
    actual_port = server._site._server.sockets[0].getsockname()[1]
    base_uri = f"http://{local_ip}:{actual_port}"

    # Configure services with stream details
    device = server._device
    assert device is not None
    device.configure_services(
        stream_url=args.stream_url,
        stream_title=args.name,
        stream_mime_type=args.mime_type,
        host_url=base_uri,
    )

    _LOGGER.info(
        "DLNA server started: '%s' at %s",
        args.name,
        base_uri,
    )
    _LOGGER.info("Stream URL: %s", args.stream_url)
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

    # Cleanup
    forwarder.cancel_all()
    await server.async_stop()
    _LOGGER.info("Server stopped")


def _get_local_ip() -> str | None:
    """Get the local IP address of the machine."""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        pass

    try:
        import socket
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except OSError:
        return None


def _make_device_class(friendly_name: str, forwarder: StreamForwarder) -> type:
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
            """Initialize and set forwarder."""
            super().__init__(
                requester=requester,
                base_uri=base_uri,
                boot_id=boot_id,
                config_id=config_id,
            )
            self.set_forwarder(forwarder)

    return CustomMediaServerDevice


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

    # Reduce noise from libraries
    logging.getLogger("async_upnp_client").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
