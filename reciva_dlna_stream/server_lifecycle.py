"""Server lifecycle management for reciva-dlna-stream.

Provides ``_start_server()`` and ``_ServerHandle`` for starting the
HTTP and SSDP components in the correct order, ensuring the SSDP
LOCATION URL contains the actual port (not 0).

See ``__main__.py`` for CLI entry point usage.
"""

from __future__ import annotations

import logging
import socket
from datetime import timedelta
from functools import partial
from typing import Sequence, cast

from aiohttp.web import Application, AppRunner, TCPSite

from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.server import (
    SsdpAdvertisementAnnouncer,
    SsdpSearchResponder,
    UpnpServerService,
    _LOGGER_TRAFFIC_UPNP,
    _build_advertisements,
    action_handler,
    subscribe_handler,
    to_xml,
    unsubscribe_handler,
)
from async_upnp_client.ssdp import SsdpProtocol, build_ssdp_packet

from .forwarder import StreamForwarder
from .stream_config import StreamConfig

_LOGGER = logging.getLogger(__name__)


class FastSsdpAdvertisementAnnouncer(SsdpAdvertisementAnnouncer):
    """SSDP announcer that sends all NOTIFY entries each interval.

    The upstream library cycles through one NT/USN pair per interval,
    delivering only 1 of ~5 entries per beacon. Reciva radios seem to
    only respond to specific NT/USN entries (e.g. ``upnp:rootdevice``),
    causing multi-minute delays before discovery succeeds.

    This subclass sends ALL advertisement entries at every 5-second
    interval, so the radio sees every NT/USN combination on every beacon.
    """

    ANNOUNCE_INTERVAL = timedelta(seconds=5)

    def __init__(self, *args, **kwargs):
        """Initialize and store the full advertisement list."""
        super().__init__(*args, **kwargs)
        # The parent __init__ set self._advertisements = cycle(entries).
        # Replace with a plain list so we can iterate all at once.
        self._advertisements = _build_advertisements(self.target, self.device)

    def _announce_next(self) -> None:
        """Send ALL advertisement entries, then reschedule."""
        _LOGGER.debug("Announcing all %d advertisements", len(self._advertisements))
        assert self._transport

        protocol = cast(SsdpProtocol, self._transport.get_protocol())
        start_line = "NOTIFY * HTTP/1.1"

        for headers in self._advertisements:
            packet = build_ssdp_packet(start_line, headers)
            _LOGGER.debug(
                "Sending SSDP NOTIFY: NTS=%s NT=%s USN=%s",
                headers["NTS"],
                headers["NT"],
                headers["USN"],
            )
            protocol.send_ssdp_packet(packet, self.target)

        # Reschedule self.
        self._cancel_announce = self._loop.call_later(
            self.ANNOUNCE_INTERVAL.total_seconds(),
            self._announce_next,
        )


class ServerHandle:
    """Holds references to all running server components for clean shutdown."""

    def __init__(
        self,
        port: int,
        search_responder: SsdpSearchResponder,
        advertisement_announcer: SsdpAdvertisementAnnouncer,
        runner: AppRunner,
        forwarders: Sequence[StreamForwarder],
    ) -> None:
        self.port = port
        self._search_responder = search_responder
        self._advertisement_announcer = advertisement_announcer
        self._runner = runner
        self._forwarders = list(forwarders)

    @property
    def ssdp_location_url(self) -> str:
        """Return the SSDP LOCATION URL that the server advertises.

        This is the URL that SSDP NOTIFY and M-SEARCH responses include
        in their ``LOCATION`` header. It is derived from the device's
        ``base_uri`` and ``device_url``, both of which are set during
        ``start_server()`` to ensure the port is correct (not 0).
        """
        return (
            f"{self._search_responder.device.base_uri}"
            f"{self._search_responder.device.device_url}"
        )

    async def stop(self) -> None:
        """Stop all stream buffers, SSDP, then HTTP.

        Shutdown sequence:
        1. Cancel all active streaming connections (for each forwarder)
        2. Stop all stream buffers (for each forwarder)
        3. Stop advertisement announcer (stop sending NOTIFY)
        4. Stop search responder (stop responding to M-SEARCH)
        5. Cleanup aiohttp AppRunner
        """
        for fwd in self._forwarders:
            await fwd.cancel_all()
            await fwd.stop_buffer()
        await self._advertisement_announcer.async_stop()
        await self._search_responder.async_stop()
        await self._runner.cleanup()
        _LOGGER.info("Server stopped")


async def start_server(
    device_class: type,
    local_ip: str,
    http_bind: str,
    http_port: int,
    streams: list[StreamConfig],
    forwarders: Sequence[StreamForwarder],
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
    device.configure_services(streams=streams, host_url=base_uri)

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

    if device.routes:
        app.router.add_routes(device.routes)

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
    advertisement_announcer = FastSsdpAdvertisementAnnouncer(
        device,
        source=source,
    )
    await search_responder.async_start()
    await advertisement_announcer.async_start()

    return ServerHandle(
        port=actual_port,
        search_responder=search_responder,
        advertisement_announcer=advertisement_announcer,
        runner=runner,
        forwarders=forwarders,
    )
