# UPnP Device & Service Design

## Purpose
Define the DLNA MediaServer device and its services using the `async_upnp_client` library. The device exposes a single audio item representing the internet radio stream.

## Device: `MediaServerDevice`

Inherits from `UpnpServerDevice` (from `async_upnp_client.server`).

### Device Definition
```python
DEVICE_DEFINITION = DeviceInfo(
    device_type="urn:schemas-upnp-org:device:MediaServer:1",
    friendly_name="Internet Radio Stream",          # overridable via CLI
    manufacturer="dlna-stream",
    model_name="dlna-stream v0.1",
    udn="uuid:...",                                  # overridden per instance
    url="/device.xml",
)
```

### Services
- `ContentDirectoryService` (ContentDirectory:1)
- `ConnectionManagerService` (ConnectionManager:1)

### Routes
- `/stream` → `StreamForwarder.handle_request` (added dynamically via `set_forwarder()`)

### Key Methods

#### `set_forwarder(forwarder: StreamForwarder)`
Stores the forwarder and creates the `/stream` route pointing to `forwarder.handle_request`.

#### `configure_services(stream_url, stream_title, stream_mime_type, host_url)`
Iterates all services and calls their `configure()` with appropriate parameters.

## Service: `ContentDirectoryService`

Inherits from `UpnpServerService`.

### UPnP Service Definition
- Service type: `urn:schemas-upnp-org:service:ContentDirectory:1`
- Control URL: `/upnp/control/ContentDirectory1`
- Event sub URL: `/upnp/event/ContentDirectory1`
- SCPD URL: `/ContentDirectory_1.xml`

### State Variables
Standard ContentDirectory variables:
- `A_ARG_TYPE_ObjectID` (string)
- `A_ARG_TYPE_Result` (string)
- `A_ARG_TYPE_BrowseFlag` (string, allowed: BrowseMetadata, BrowseDirectChildren)
- `A_ARG_TYPE_Filter` (string)
- `A_ARG_TYPE_StartingIndex` (ui4)
- `A_ARG_TYPE_RequestedCount` (ui4)
- `A_ARG_TYPE_SortCriteria` (string)
- `A_ARG_TYPE_UpdateID` (ui4)
- `SearchCapabilities` (string, default: "")
- `SortCapabilities` (string, default: "")
- `SystemUpdateID` (ui4, default: "0")

### Actions

#### `Browse(ObjectID, BrowseFlag, Filter, StartingIndex, RequestedCount, SortCriteria)`
Returns DIDL-Lite XML. Handles these cases:

| ObjectID | BrowseFlag | Result |
|----------|-----------|--------|
| "0" | BrowseMetadata | Container metadata (childCount=1) |
| "0" | BrowseDirectChildren | Single audio item (title, URL=`/stream`) |
| _ITEM_ID (same as "0") | BrowseMetadata | Item metadata (same URL) |

The stream URL returned is always `{host_url}/stream`.

#### `GetSearchCapabilities()`, `GetSortCapabilities()`, `GetSystemUpdateID()`
Standard ContentDirectory actions, return empty/default values.

#### `Search(ContainerID, ...)`
Not implemented. Returns empty result.

### DIDL-Lite XML Structure

Browse result for "BrowseDirectChildren":
```xml
<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
           xmlns:dc="http://purl.org/dc/elements/1.1/"
           xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
  <item id="0" parentID="0" restricted="true">
    <dc:title>Stream Name</dc:title>
    <upnp:class>object.item.audioItem.audioBroadcast</upnp:class>
    <res protocolInfo="http-get:*:audio/mpeg:*">http://IP:PORT/stream</res>
  </item>
</DIDL-Lite>
```

Container metadata:
```xml
<DIDL-Lite ...>
  <container id="0" parentID="-1" restricted="true" childCount="1">
    <dc:title>Stream Name</dc:title>
    <upnp:class>object.container</upnp:class>
  </container>
</DIDL-Lite>
```

## Service: `ConnectionManagerService`

### UPnP Service Definition
- Service type: `urn:schemas-upnp-org:service:ConnectionManager:1`
- Control URL: `/upnp/control/ConnectionManager1`
- Event sub URL: `/upnp/event/ConnectionManager1`
- SCPD URL: `/ConnectionManager_1.xml`

### Actions

#### `GetProtocolInfo()`
Returns `Source: http-get:*:audio/mpeg:*` and `Sink: ""`.

#### `GetCurrentConnectionIDs()`
Returns `ConnectionIDs: "0"`.

#### `GetCurrentConnectionInfo(ConnectionID)`
Returns dummy info with Status="OK", Direction="Output", ProtocolInfo="http-get:*:audio/mpeg:*".

## UDN Generation

The UDN is generated per-instance using `uuid.uuid4()`. A custom device class is created via `_make_device_class()` in `__main__.py` that overrides `DEVICE_DEFINITION` with a fresh UDN and the user-specified friendly name.
