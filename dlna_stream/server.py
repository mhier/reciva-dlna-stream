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
    """Build a DIDL-Lite XML string for a single item."""
    if protocol_info is None:
        protocol_info = f"http-get:*:{mime_type}:*"

    return (
        f'<DIDL-Lite {_DIDL_XMLNS}>'
        f'<item id="{item_id}" parentID="{parent_id}" restricted="true">'
        f'<dc:title>{_xml_escape(title)}</dc:title>'
        f'<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>'
        f'<res protocolInfo="{_xml_escape(protocol_info)}"'
        f'>{_xml_escape(url)}</res>'
        f'</item>'
        f'</DIDL-Lite>'
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

# Item ID for our radio stream
_ITEM_ID = "0"
_CONTAINER_ID = "0"


class ContentDirectoryService(UpnpServerService):
    """ContentDirectory:1 service.

    Exposes a single audio item representing the internet radio stream.
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
        self._stream_url: str = ""
        self._stream_title: str = "Internet Radio"
        self._stream_mime_type: str = "audio/mpeg"
        self._host_url: str = ""

    def configure(
        self,
        stream_url: str,
        stream_title: str,
        stream_mime_type: str,
        host_url: str,
    ) -> None:
        """Configure the stream details."""
        self._stream_url = stream_url
        self._stream_title = stream_title
        self._stream_mime_type = stream_mime_type
        self._host_url = host_url

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

        # Build the stream URL that the client will use to request audio data
        stream_url = f"{self._host_url}/stream"

        if ObjectID == "0" and BrowseFlag == "BrowseMetadata":
            # Root container metadata
            result = _build_didl_container(
                "0", "-1", self._stream_title, 1,
            )

        elif ObjectID == "0" and BrowseFlag == "BrowseDirectChildren":
            # List children of root: our single radio item
            result = _build_didl_item(
                item_id=_ITEM_ID,
                parent_id=_CONTAINER_ID,
                title=self._stream_title,
                url=stream_url,
                mime_type=self._stream_mime_type,
            )

        elif ObjectID == _ITEM_ID and BrowseFlag == "BrowseMetadata":
            # Item metadata
            result = _build_didl_item(
                item_id=_ITEM_ID,
                parent_id=_CONTAINER_ID,
                title=self._stream_title,
                url=stream_url,
                mime_type=self._stream_mime_type,
            )

        else:
            result = ""

        _LOGGER.debug(
            "Browse result for %s/%s: %d chars, url=%s",
            ObjectID,
            BrowseFlag,
            len(result),
            stream_url,
        )

        return {
            "Result": result,
            "NumberReturned": 1 if result else 0,
            "TotalMatches": 1 if result else 0,
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
        self._mime_type: str = "audio/mpeg"

    def configure(self, mime_type: str) -> None:
        """Configure."""
        self._mime_type = mime_type

    # pylint: disable=invalid-name,unused-argument

    @callable_action(
        name="GetProtocolInfo",
        in_args={},
        out_args={"Source": "SourceProtocolInfo", "Sink": "SinkProtocolInfo"},
    )
    async def get_protocol_info(self) -> dict[str, UpnpStateVariable]:
        """Get protocol info."""
        source = self.state_variable("SourceProtocolInfo")
        source.upnp_value = f"http-get:*:{self._mime_type}:*"
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
        return {
            "RcsID": -1,
            "AVTransportID": -1,
            "ProtocolInfo": f"http-get:*:{self._mime_type}:*",
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
    a single audio item for the internet radio stream.

    Services are configured externally after creation via configure_services().
    This allows UpnpServer to instantiate the device with the standard
    (requester, base_uri, boot_id, config_id) signature.
    """

    DEVICE_DEFINITION = DeviceInfo(
        device_type="urn:schemas-upnp-org:device:MediaServer:1",
        friendly_name="Internet Radio Stream",
        manufacturer="dlna-stream",
        manufacturer_url=None,
        model_name="dlna-stream v0.1",
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
    ROUTES: Sequence[RouteDef] | None = None

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
        self._stream_forwarder: StreamForwarder | None = None

    def set_forwarder(self, forwarder: StreamForwarder) -> None:
        """Set the stream forwarder and add its route."""
        self._stream_forwarder = forwarder

        # Create a route for /stream that the forwarder handles
        from aiohttp.web import get
        self.ROUTES = (get("/stream", forwarder.handle_request),)

    def configure_services(
        self,
        stream_url: str,
        stream_title: str,
        stream_mime_type: str,
        host_url: str,
    ) -> None:
        """Configure services with stream details after creation."""
        for service in self.all_services:
            if isinstance(service, ContentDirectoryService):
                service.configure(
                    stream_url=stream_url,
                    stream_title=stream_title,
                    stream_mime_type=stream_mime_type,
                    host_url=host_url,
                )
            elif isinstance(service, ConnectionManagerService):
                service.configure(mime_type=stream_mime_type)
