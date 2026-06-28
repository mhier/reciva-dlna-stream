"""DLNA Media Server device definition using async_upnp_client.

Defines a MediaServer:1 device with ContentDirectory:1 and ConnectionManager:1
services. The ContentDirectory exposes a single audio item representing the
internet radio stream.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Sequence, Type

from aiohttp.web import RouteDef, route

from async_upnp_client.client import UpnpRequester, UpnpStateVariable
from async_upnp_client.const import (
    STATE_VARIABLE_TYPE_MAPPING,
    DeviceInfo,
    ServiceInfo,
    StateVariableTypeInfo,
)
from async_upnp_client.server import (
    UpnpServerDevice,
    UpnpServerService,
    callable_action,
)

from .forwarder import StreamForwarder
from .stream_config import StreamConfig

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DIDL-Lite helpers for ContentDirectory browse responses
# ---------------------------------------------------------------------------

_DIDL_XMLNS = (
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"'
)

# Protocol info for DLNA: "http-get:*:audio/mpeg:DLNA.ORG_PN=MP3"
_PROTOCOL_INFO = "http-get:*:audio/mpeg:*"


def _build_didl_item(
    item_id: str,
    parent_id: str,
    title: str,
    url: str,
    mime_type: str,
    protocol_info: str | None = None,
) -> str:
    """Build a DIDL-Lite XML string for a single item (full document)."""
    if protocol_info is None:
        protocol_info = f"http-get:*:{mime_type}:*"

    return (
        f'<DIDL-Lite {_DIDL_XMLNS}>'
        f'{_build_item_xml(item_id, parent_id, title, url, mime_type, protocol_info)}'
        f'</DIDL-Lite>'
    )


def _build_item_xml(
    item_id: str,
    parent_id: str,
    title: str,
    url: str,
    mime_type: str,
    protocol_info: str | None = None,
) -> str:
    """Build the inner XML for a single item (without DIDL-Lite wrapper)."""
    if protocol_info is None:
        protocol_info = f"http-get:*:{mime_type}:*"

    return (
        f'<item id="{item_id}" parentID="{parent_id}" restricted="true">'
        f'<dc:title>{_xml_escape(title)}</dc:title>'
        f'<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>'
        f'<res protocolInfo="{_xml_escape(protocol_info)}"'
        f'>{_xml_escape(url)}</res>'
        f'</item>'
    )


def _build_didl_container(
    container_id: str,
    parent_id: str,
    title: str,
    child_count: int,
) -> str:
    """Build a DIDL-Lite XML string for a container."""
    return (
        f'<DIDL-Lite {_DIDL_XMLNS}>'
        f'<container id="{container_id}" parentID="{parent_id}"'
        f' restricted="true" childCount="{child_count}">'
        f'<dc:title>{_xml_escape(title)}</dc:title>'
        f'<upnp:class>object.container</upnp:class>'
        f'</container>'
        f'</DIDL-Lite>'
    )


def _xml_escape(text: str) -> str:
    """Escape text for XML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ---------------------------------------------------------------------------
# ContentDirectory service
# ---------------------------------------------------------------------------

# Root container constants
_CONTAINER_ID = "0"

# Stream items use IDs "0", "1", "2", ... matching their index


class ContentDirectoryService(UpnpServerService):
    """ContentDirectory:1 service.

    Exposes a container with one audio item per configured stream.
    """

    SERVICE_DEFINITION = ServiceInfo(
        service_id="urn:upnp-org:serviceId:ContentDirectory",
        service_type="urn:schemas-upnp-org:service:ContentDirectory:1",
        control_url="/upnp/control/ContentDirectory1",
        event_sub_url="/upnp/event/ContentDirectory1",
        scpd_url="/ContentDirectory_1.xml",
        xml=ET.Element("server_service"),
    )

    STATE_VARIABLE_DEFINITIONS = {
        "A_ARG_TYPE_ObjectID": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_Result": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_BrowseFlag": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=["BrowseMetadata", "BrowseDirectChildren"],
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_Filter": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_StartingIndex": StateVariableTypeInfo(
            data_type="ui4",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["ui4"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_RequestedCount": StateVariableTypeInfo(
            data_type="ui4",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["ui4"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_SortCriteria": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_UpdateID": StateVariableTypeInfo(
            data_type="ui4",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["ui4"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "SearchCapabilities": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value="",
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "SortCapabilities": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value="",
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "SystemUpdateID": StateVariableTypeInfo(
            data_type="ui4",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["ui4"],
            default_value="0",
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
    }

    def __init__(self, requester: UpnpRequester) -> None:
        """Initialize."""
        super().__init__(requester)
        self._streams: list[StreamConfig] = [
            StreamConfig(url="", name="Internet Radio", mime_type="audio/mpeg"),
        ]
        self._host_url: str = ""
        self._friendly_name: str = "Internet Radio"

    def configure(
        self,
        streams: list[StreamConfig],
        host_url: str,
        friendly_name: str = "",
    ) -> None:
        """Configure stream entries.

        Args:
            streams: The list of stream configurations.
            host_url: The base URL for stream item URLs.
            friendly_name: The device's friendly name, used for the
                root container title.
        """
        self._streams = streams
        self._host_url = host_url
        if friendly_name:
            self._friendly_name = friendly_name

    # pylint: disable=invalid-name,unused-argument

    @callable_action(
        name="GetSearchCapabilities",
        in_args={},
        out_args={"SearchCaps": "SearchCapabilities"},
    )
    async def get_search_capabilities(self) -> dict[str, UpnpStateVariable]:
        """Get search capabilities."""
        return {"SearchCaps": self.state_variable("SearchCapabilities")}

    @callable_action(
        name="GetSortCapabilities",
        in_args={},
        out_args={"SortCaps": "SortCapabilities"},
    )
    async def get_sort_capabilities(self) -> dict[str, UpnpStateVariable]:
        """Get sort capabilities."""
        return {"SortCaps": self.state_variable("SortCapabilities")}

    @callable_action(
        name="GetSystemUpdateID",
        in_args={},
        out_args={"Id": "SystemUpdateID"},
    )
    async def get_system_update_id(self) -> dict[str, UpnpStateVariable]:
        """Get system update ID."""
        return {"Id": self.state_variable("SystemUpdateID")}

    @callable_action(
        name="Browse",
        in_args={
            "ObjectID": "A_ARG_TYPE_ObjectID",
            "BrowseFlag": "A_ARG_TYPE_BrowseFlag",
            "Filter": "A_ARG_TYPE_Filter",
            "StartingIndex": "A_ARG_TYPE_StartingIndex",
            "RequestedCount": "A_ARG_TYPE_RequestedCount",
            "SortCriteria": "A_ARG_TYPE_SortCriteria",
        },
        out_args={
            "Result": "A_ARG_TYPE_Result",
            "NumberReturned": "A_ARG_TYPE_StartingIndex",
            "TotalMatches": "A_ARG_TYPE_RequestedCount",
            "UpdateID": "A_ARG_TYPE_UpdateID",
        },
    )
    async def browse(
        self,
        ObjectID: str,
        BrowseFlag: str,
        Filter: str,
        StartingIndex: int,
        RequestedCount: int,
        SortCriteria: str,
    ) -> dict[str, UpnpStateVariable | str | int]:
        """Browse the ContentDirectory."""
        _LOGGER.debug(
            "Browse: ObjectID=%s BrowseFlag=%s StartingIndex=%d RequestedCount=%d",
            ObjectID,
            BrowseFlag,
            StartingIndex,
            RequestedCount,
        )

        if ObjectID == "0" and BrowseFlag == "BrowseMetadata":
            # Root container metadata
            title = self._friendly_name
            child_count = len(self._streams)
            result = _build_didl_container(
                "0", "-1", title, child_count,
            )
            n_returned = 1 if result else 0

        elif ObjectID == "0" and BrowseFlag == "BrowseDirectChildren":
            # List all stream items as children of root
            items_parts: list[str] = []
            for idx, stream in enumerate(self._streams):
                stream_url = f"{self._host_url}/stream/{idx}"
                items_parts.append(
                    _build_item_xml(
                        item_id=str(idx),
                        parent_id=_CONTAINER_ID,
                        title=stream.name,
                        url=stream_url,
                        mime_type=stream.mime_type,
                    )
                )

            # Apply pagination
            total = len(items_parts)
            start = StartingIndex if StartingIndex < total else total
            end = min(start + RequestedCount, total) if RequestedCount else total
            items_xml = "".join(items_parts[start:end])
            result = f'<DIDL-Lite {_DIDL_XMLNS}>{items_xml}</DIDL-Lite>'
            n_returned = end - start

        elif ObjectID.isdigit() and BrowseFlag == "BrowseMetadata":
            # Single item metadata
            idx = int(ObjectID)
            if 0 <= idx < len(self._streams):
                stream = self._streams[idx]
                stream_url = f"{self._host_url}/stream/{idx}"
                result = _build_didl_item(
                    item_id=str(idx),
                    parent_id=_CONTAINER_ID,
                    title=stream.name,
                    url=stream_url,
                    mime_type=stream.mime_type,
                )
                n_returned = 1
            else:
                result = ""
                n_returned = 0

        else:
            result = ""
            n_returned = 0

        _LOGGER.debug(
            "Browse result for %s/%s: %d chars, %d items",
            ObjectID,
            BrowseFlag,
            len(result),
            n_returned,
        )

        return {
            "Result": result,
            "NumberReturned": n_returned,
            "TotalMatches": len(self._streams) if ObjectID == "0" else n_returned,
            "UpdateID": 0,
        }

    @callable_action(
        name="Search",
        in_args={
            "ContainerID": "A_ARG_TYPE_ObjectID",
            "BrowseFlag": "A_ARG_TYPE_BrowseFlag",
            "Filter": "A_ARG_TYPE_Filter",
            "StartingIndex": "A_ARG_TYPE_StartingIndex",
            "RequestedCount": "A_ARG_TYPE_RequestedCount",
            "SortCriteria": "A_ARG_TYPE_SortCriteria",
        },
        out_args={
            "Result": "A_ARG_TYPE_Result",
            "NumberReturned": "A_ARG_TYPE_StartingIndex",
            "TotalMatches": "A_ARG_TYPE_RequestedCount",
            "UpdateID": "A_ARG_TYPE_UpdateID",
        },
    )
    async def search(
        self,
        ContainerID: str,
        BrowseFlag: str,
        Filter: str,
        StartingIndex: int,
        RequestedCount: int,
        SortCriteria: str,
    ) -> dict[str, UpnpStateVariable | str | int]:
        """Search is not implemented. Return empty result."""
        return {
            "Result": "",
            "NumberReturned": 0,
            "TotalMatches": 0,
            "UpdateID": 0,
        }


# ---------------------------------------------------------------------------
# ConnectionManager service
# ---------------------------------------------------------------------------


class ConnectionManagerService(UpnpServerService):
    """ConnectionManager:1 service."""

    SERVICE_DEFINITION = ServiceInfo(
        service_id="urn:upnp-org:serviceId:ConnectionManager",
        service_type="urn:schemas-upnp-org:service:ConnectionManager:1",
        control_url="/upnp/control/ConnectionManager1",
        event_sub_url="/upnp/event/ConnectionManager1",
        scpd_url="/ConnectionManager_1.xml",
        xml=ET.Element("server_service"),
    )

    STATE_VARIABLE_DEFINITIONS = {
        "A_ARG_TYPE_ConnectionStatus": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=["OK", "ContentFormatMismatch", "InsufficientBandwidth",
                           "UnreliableChannel", "Unknown"],
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_ConnectionID": StateVariableTypeInfo(
            data_type="i4",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["i4"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_AVTransportID": StateVariableTypeInfo(
            data_type="i4",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["i4"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_RcsID": StateVariableTypeInfo(
            data_type="i4",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["i4"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_ProtocolInfo": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_ConnectionManager": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "A_ARG_TYPE_Direction": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value=None,
            allowed_value_range={},
            allowed_values=["Output", "Input"],
            xml=ET.Element("server_stateVariable"),
        ),
        "SourceProtocolInfo": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value="",
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "SinkProtocolInfo": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value="",
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
        "CurrentConnectionIDs": StateVariableTypeInfo(
            data_type="string",
            data_type_mapping=STATE_VARIABLE_TYPE_MAPPING["string"],
            default_value="0",
            allowed_value_range={},
            allowed_values=None,
            xml=ET.Element("server_stateVariable"),
        ),
    }

    def __init__(self, requester: UpnpRequester) -> None:
        """Initialize."""
        super().__init__(requester)
        self._mime_types: list[str] = ["audio/mpeg"]

    def configure(self, mime_types: list[str]) -> None:
        """Configure with all stream MIME types."""
        self._mime_types = list(mime_types) if mime_types else ["audio/mpeg"]

    # pylint: disable=invalid-name,unused-argument

    @callable_action(
        name="GetProtocolInfo",
        in_args={},
        out_args={"Source": "SourceProtocolInfo", "Sink": "SinkProtocolInfo"},
    )
    async def get_protocol_info(self) -> dict[str, UpnpStateVariable]:
        """Get protocol info."""
        source = self.state_variable("SourceProtocolInfo")
        # Return all MIME types as comma-separated protocol info strings
        protocol_strings = ",".join(
            f"http-get:*:{mt}:*" for mt in self._mime_types
        )
        source.upnp_value = protocol_strings
        sink = self.state_variable("SinkProtocolInfo")
        sink.upnp_value = ""
        return {
            "Source": source,
            "Sink": sink,
        }

    @callable_action(
        name="GetCurrentConnectionIDs",
        in_args={},
        out_args={"ConnectionIDs": "CurrentConnectionIDs"},
    )
    async def get_current_connection_ids(self) -> dict[str, UpnpStateVariable]:
        """Get current connection IDs."""
        return {"ConnectionIDs": self.state_variable("CurrentConnectionIDs")}

    @callable_action(
        name="GetCurrentConnectionInfo",
        in_args={"ConnectionID": "A_ARG_TYPE_ConnectionID"},
        out_args={
            "RcsID": "A_ARG_TYPE_RcsID",
            "AVTransportID": "A_ARG_TYPE_AVTransportID",
            "ProtocolInfo": "A_ARG_TYPE_ProtocolInfo",
            "PeerConnectionManager": "A_ARG_TYPE_ConnectionManager",
            "PeerConnectionID": "A_ARG_TYPE_ConnectionID",
            "Direction": "A_ARG_TYPE_Direction",
            "Status": "A_ARG_TYPE_ConnectionStatus",
        },
    )
    async def get_current_connection_info(
        self, ConnectionID: int
    ) -> dict[str, UpnpStateVariable]:
        """Get current connection info."""
        protocol_strings = ",".join(
            f"http-get:*:{mt}:*" for mt in self._mime_types
        )
        return {
            "RcsID": -1,
            "AVTransportID": -1,
            "ProtocolInfo": protocol_strings,
            "PeerConnectionManager": "",
            "PeerConnectionID": -1,
            "Direction": "Output",
            "Status": "OK",
        }


# ---------------------------------------------------------------------------
# Media Server Device
# ---------------------------------------------------------------------------


class MediaServerDevice(UpnpServerDevice):
    """DLNA MediaServer device.

    Presents itself as a MediaServer:1 device on the network and exposes
    one audio item per configured stream in its ContentDirectory.

    Services are configured externally after creation via configure_services().
    This allows UpnpServer to instantiate the device with the standard
    (requester, base_uri, boot_id, config_id) signature.
    """

    DEVICE_DEFINITION = DeviceInfo(
        device_type="urn:schemas-upnp-org:device:MediaServer:1",
        friendly_name="Internet Radio Stream",
        manufacturer="reciva-dlna-stream",
        manufacturer_url=None,
        model_name="reciva-dlna-stream v0.1",
        model_url=None,
        udn="uuid:00000000-0000-0000-0000-000000000001",
        upc=None,
        model_description="DLNA Media Server for internet radio streaming",
        model_number="0.1.0",
        serial_number="1",
        presentation_url=None,
        url="/device.xml",
        icons=[],
        xml=ET.Element("server_device"),
    )
    EMBEDDED_DEVICES: Sequence[Type[UpnpServerDevice]] = []
    SERVICES: Sequence[Type[UpnpServerService]] = [
        ContentDirectoryService,
        ConnectionManagerService,
    ]

    def __init__(
        self,
        requester: UpnpRequester,
        base_uri: str,
        boot_id: int = 1,
        config_id: int = 1,
    ) -> None:
        """Initialize."""
        super().__init__(
            requester=requester,
            base_uri=base_uri,
            boot_id=boot_id,
            config_id=config_id,
        )
        self._forwarders: list[StreamForwarder] = []
        self._routes: tuple[RouteDef, ...] = ()

    @property
    def routes(self) -> tuple[RouteDef, ...]:
        """Return the per-instance stream routes."""
        return self._routes

    def set_forwarders(self, forwarders: list[StreamForwarder]) -> None:
        """Set multiple stream forwarders and register routes.

        Each forwarder is mounted at ``/stream/<index>``.
        For single-stream backward compat, ``/stream`` also works when
        there is exactly one forwarder.
        """
        self._forwarders = list(forwarders)

        from aiohttp.web import get
        routes: list[RouteDef] = []
        for idx, fwd in enumerate(self._forwarders):
            routes.append(get(f"/stream/{idx}", fwd.handle_request))
        # Backward compat: single stream also works at /stream
        if len(self._forwarders) == 1:
            routes.append(get("/stream", self._forwarders[0].handle_request))
        self._routes = tuple(routes)

    def set_forwarder(self, forwarder: StreamForwarder) -> None:
        """Set a single stream forwarder (backward-compat)."""
        self.set_forwarders([forwarder])

    def configure_services(
        self,
        streams: list[StreamConfig],
        host_url: str,
    ) -> None:
        """Configure services with stream details after creation."""
        for service in self.all_services:
            if isinstance(service, ContentDirectoryService):
                service.configure(
                    streams=streams,
                    host_url=host_url,
                    friendly_name=self.DEVICE_DEFINITION.friendly_name,
                )
            elif isinstance(service, ConnectionManagerService):
                # Collect all MIME types for multi-stream support
                mime_types = [s.mime_type for s in streams] if streams else ["audio/mpeg"]
                service.configure(mime_types=mime_types)


# ---------------------------------------------------------------------------
# Device class factory (shared between production and tests)
# ---------------------------------------------------------------------------


def make_device_class(
    friendly_name: str,
    forwarders: list[StreamForwarder],
    udn: str | None = None,
) -> type:
    """Create a MediaServerDevice subclass with a unique UDN and custom name.

    Args:
        friendly_name: The device's friendly name.
        forwarders: List of StreamForwarder instances.
        udn: Optional UDN string (e.g. "uuid:..."). Auto-generated if None.

    Returns:
        A MediaServerDevice subclass with the given UDN and forwarders pre-wired.
    """
    from uuid import uuid4

    if udn is None:
        udn = f"uuid:{uuid4()}"

    class _CustomDevice(MediaServerDevice):
        """MediaServerDevice with custom UDN and pre-wired forwarders."""

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
            """Initialize and attach the forwarders."""
            super().__init__(
                requester=requester,
                base_uri=base_uri,
                boot_id=boot_id,
                config_id=config_id,
            )
            self.set_forwarders(forwarders)

    return _CustomDevice
