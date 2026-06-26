# UPnP Device & Service Design

## Purpose
Define the DLNA MediaServer device and its services using the `async_upnp_client` library. The device exposes the configured streams as audio items in the ContentDirectory.

## Device: `MediaServerDevice`

Inherits from `UpnpServerDevice` (from `async_upnp_client.server`).

### Device Definition
```python
DEVICE_DEFINITION = DeviceInfo(
    device_type="urn:schemas-upnp-org:device:MediaServer:1",
    friendly_name="Internet Radio Stream",          # overridable via CLI
    manufacturer="reciva-dlna-stream",
    model_name="reciva-dlna-stream v0.1",
    udn="uuid:...",                                  # overridden per instance
    url="/device.xml",
)
```

### Services
- `ContentDirectoryService` (ContentDirectory:1)
- `ConnectionManagerService` (ConnectionManager:1)

### Routes (single-stream)
- `/stream` → `StreamForwarder.handle_request` (added via `set_forwarder()` or `set_forwarders(one_element_list)`)

### Routes (multi-stream)
- `/stream/0`, `/stream/1`, ... → each forwarder's `handle_request` (added via `set_forwarders()`)

Only in single-stream mode (exactly 1 forwarder) is the legacy `/stream` route registered.

### Key Methods

#### `set_forwarders(forwarders: list[StreamForwarder])`
Stores all forwarders and creates routes `/stream/{index}` for each one.
If exactly one forwarder, also registers legacy `/stream` for backward compatibility.

#### `set_forwarder(forwarder: StreamForwarder)`
Convenience wrapper — delegates to `set_forwarders([forwarder])`.

#### `configure_services(streams: list[StreamConfig], host_url: str)`
Iterates all services and calls their `configure()` with the full stream list.

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
| "0" | BrowseMetadata | Container metadata (childCount = number of streams) |
| "0" | BrowseDirectChildren | One `<item>` per stream, each with its own URL `/stream/{id}` |
| "N" (digit) | BrowseMetadata | Item metadata for stream index N (URL `/stream/{N}`) |

The stream URL returned is `{host_url}/stream/{item_id}`.

#### `GetSearchCapabilities()`, `GetSortCapabilities()`, `GetSystemUpdateID()`
Standard ContentDirectory actions, return empty/default values.

#### `Search(ContainerID, ...)`
Not implemented. Returns empty result (`NumberReturned=0, TotalMatches=0`).

## Service: `ConnectionManagerService`

### DIDL-Lite XML Structure

Browse result for "BrowseDirectChildren" (example with 2 streams):
```xml
<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
           xmlns:dc="http://purl.org/dc/elements/1.1/"
           xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
  <item id="0" parentID="0" restricted="true">
    <dc:title>Deutschlandfunk</dc:title>
    <upnp:class>object.item.audioItem.audioBroadcast</upnp:class>
    <res protocolInfo="http-get:*:audio/mpeg:*">http://IP:PORT/stream/0</res>
  </item>
  <item id="1" parentID="0" restricted="true">
    <dc:title>Deutschlandfunk Kultur</dc:title>
    <upnp:class>object.item.audioItem.audioBroadcast</upnp:class>
    <res protocolInfo="http-get:*:audio/mpeg:*">http://IP:PORT/stream/1</res>
  </item>
</DIDL-Lite>
```

Container metadata (here with 2 streams):
```xml
<DIDL-Lite ...>
  <container id="0" parentID="-1" restricted="true" childCount="2">
    <dc:title>Deutschlandfunk</dc:title>
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
