"""Server lifecycle management for dlna-stream.

Provides ``_start_server()`` and ``_ServerHandle`` for starting the
HTTP and SSDP components in the correct order, ensuring the SSDP
LOCATION URL contains the actual port (not 0).

See ``__main__.py`` for CLI entry point usage.
"""

from __future__ import annotations

import logging
import socket
from functools import partial
from typing import cast

from aiohttp.web import Application, AppRunner, TCPSite

from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.server import (
    SsdpAdvertisementAnnouncer,
    SsdpSearchResponder,
    UpnpServerService,
    _LOGGER_TRAFFIC_UPNP,
    action_handler,
    subscribe_handler,
    to_xml,
    unsubscribe_handler,
)

_LOGGER = logging.getLogger(__name__)


class ServerHandle:
    """Holds references to all running server components for clean shutdown."""

    def __init__(
        self,
        port: int,
        search_responder: SsdpSearchResponder,
        advertisement_announcer: SsdpAdvertisementAnnouncer,
        runner: AppRunner,
        forwarder: object,
    ) -> None:
        self.port = port
        self._search_responder = search_responder
        self._advertisement_announcer = advertisement_announcer
        self._runner = runner
        self._forwarder = forwarder

    async def stop(self) -> None:
        """Stop stream buffer, SSDP, then HTTP."""
        if hasattr(self._forwarder, 'stop_buffer'):
            await self._forwarder.stop_buffer()
        await self._advertisement_announcer.async_stop()
        await self._search_responder.async_stop()
        await self._runner.cleanup()
        _LOGGER.info("Server stopped")


async def start_server(
    device_class: type,
    local_ip: str,
    http_bind: str,
    http_port: int,
    stream_url: str,
    stream_title: str,
    stream_mime_type: str,
    forwarder: object,
) -> ServerHandle:
    """Start HTTP + SSDP, ensuring SSDP LOCATION has the correct port.

    The upstream ``UpnpServer.async_start()`` calls ``_create_device()`` before
    ``_async_start_http_server()``, so when port is 0 (auto-assign) the SSDP
    advertisements go out with ``LOCATION: http://IP:0/device.xml``, which
    breaks discovery.

    This function fixes the ordering:
      1. Start HTTP server first (or determine port).
      2. Get the actual port.
      3. Create device + register routes with the correct ``base_uri``.
      4. Start SSDP search responder and advertisement announcer.
    """

    # ------------------------------------------------------------------
    # Step 1: Determine the actual port to use.
    #
    # If http_port is 0 (auto-assign), bind a temporary TCP socket to
    # find a free port, then release it.
    # ------------------------------------------------------------------

    actual_port = http_port
    if actual_port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((http_bind, 0))
            actual_port = s.getsockname()[1]
        _LOGGER.debug("Auto-assigned port %d on %s", actual_port, http_bind)

    # ------------------------------------------------------------------
    # Step 2: Create device and build the full aiohttp app
    # ------------------------------------------------------------------
    base_uri = f"http://{local_ip}:{actual_port}"
    requester = AiohttpRequester()
    device = device_class(requester, base_uri)

    # Configure services with stream details
    device.configure_services(
        stream_url=stream_url,
        stream_title=stream_title,
        stream_mime_type=stream_mime_type,
        host_url=base_uri,
    )

    # Build the aiohttp app with all UPnP routes
    app = Application()
    app.router.add_get(device.device_url, partial(to_xml, device))

    for service in device.all_services:
        service = cast(UpnpServerService, service)
        app.router.add_get(
            service.SERVICE_DEFINITION.scpd_url,
            partial(to_xml, service),
        )
        app.router.add_post(
            service.SERVICE_DEFINITION.control_url,
            partial(action_handler, service),
        )
        app.router.add_route(
            "SUBSCRIBE",
            service.SERVICE_DEFINITION.event_sub_url,
            partial(subscribe_handler, service),
        )
        app.router.add_route(
            "UNSUBSCRIBE",
            service.SERVICE_DEFINITION.event_sub_url,
            partial(unsubscribe_handler, service),
        )

    if device.ROUTES:
        app.router.add_routes(device.ROUTES)

    # ------------------------------------------------------------------
    # Step 3: Start the real HTTP server on the known port
    # ------------------------------------------------------------------
    runner = AppRunner(app, access_log=_LOGGER_TRAFFIC_UPNP)
    await runner.setup()
    site = TCPSite(runner, http_bind, actual_port, reuse_address=True)
    await site.start()

    # ------------------------------------------------------------------
    # Step 4: Start SSDP handlers
    # ------------------------------------------------------------------
    source = (local_ip, 0)
    search_responder = SsdpSearchResponder(
        device,
        source=source,
        options={"ssdp_search_responder_always_rootdevice": True},
    )
    advertisement_announcer = SsdpAdvertisementAnnouncer(
        device,
        source=source,
    )
    await search_responder.async_start()
    await advertisement_announcer.async_start()

    # Step 5: Start the buffer background reader
    if hasattr(forwarder, 'start_buffer'):
        await forwarder.start_buffer()

    return ServerHandle(
        port=actual_port,
        search_responder=search_responder,
        advertisement_announcer=advertisement_announcer,
        runner=runner,
        forwarder=forwarder,
    )
