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
Passes `friendly_name` from `DEVICE_DEFINITION.friendly_name` to `ContentDirectoryService.configure()`.
Passes all stream MIME types (as a `list[str]`) to `ConnectionManagerService.configure()`, enabling multi-stream MIME type reporting.

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

#### `configure(streams, host_url, friendly_name)`

Called during device setup to wire stream data into the service.

- `streams: list[StreamConfig]` — the configured streams, each with name, URL, MIME type.
- `host_url: str` — base URL for building stream item URLs (`{host_url}/stream/{id}`).
- `friendly_name: str` — the device's friendly name, used as the root container title.

## Service: `ConnectionManagerService` (ConnectionManager:1)

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
    <dc:title>Internet Radio Stream</dc:title>
    <upnp:class>object.container</upnp:class>
  </container>
</DIDL-Lite>
```

The container title is always the device's `friendly_name`, never a stream name.

## Service: `ConnectionManagerService`

### UPnP Service Definition
- Service type: `urn:schemas-upnp-org:service:ConnectionManager:1`
- Control URL: `/upnp/control/ConnectionManager1`
- Event sub URL: `/upnp/event/ConnectionManager1`
- SCPD URL: `/ConnectionManager_1.xml`

### Internal State

- `_mime_types: list[str]` — all stream MIME types, default `["audio/mpeg"]`.

### configure(mime_types: list[str])

Stores all MIME types. Called once during device setup via `configure_services()`.

### Actions

#### `GetProtocolInfo()`
Returns `SourceProtocolInfo` with all MIME types as comma-separated protocol info strings.

For example, with two streams (`audio/mpeg` and `audio/ogg`):
```
Source: http-get:*:audio/mpeg:*,http-get:*:audio/ogg:*
Sink: ""
```

With a single stream (single `audio/mpeg`):
```
Source: http-get:*:audio/mpeg:*
Sink: ""
```

#### `GetCurrentConnectionIDs()`
Returns `ConnectionIDs: "0"`.

#### `GetCurrentConnectionInfo(ConnectionID)`
Returns dummy info with Status="OK", Direction="Output".
`ProtocolInfo` contains all MIME types as comma-separated protocol info strings (same format as `GetProtocolInfo`).

## UDN Generation

The UDN is generated per-instance using `uuid.uuid4()`. A custom device class is created via `_make_device_class()` in `__main__.py` that overrides `DEVICE_DEFINITION` with a fresh UDN and the user-specified friendly name.
