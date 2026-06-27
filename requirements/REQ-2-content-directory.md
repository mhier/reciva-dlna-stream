# REQ-2: Content Directory (UPnP ContentDirectory Service)

| Requirement ID | Title | Status |
|---|---|---|
| REQ-2.1 | ContentDirectory Service | ✅ Implemented |
| REQ-2.2 | Browse Root Metadata | ✅ Implemented |
| REQ-2.3 | Browse Direct Children | ✅ Implemented |
| REQ-2.4 | Browse Item Metadata | ✅ Implemented |
| REQ-2.5 | Search Action (Empty) | ✅ Implemented |
| REQ-2.6 | Standard ContentDirectory Actions | ✅ Implemented |
| REQ-2.7 | ConnectionManager Service | ✅ Implemented |

---

## REQ-2.1: ContentDirectory Service

**Status: ✅ Implemented**

The server must provide a UPnP ContentDirectory:1 service that exposes audio items to clients.

### Details
- Service type: `urn:schemas-upnp-org:service:ContentDirectory:1`.
- Must implement the `Browse` action.
- Must implement the standard query actions: `GetSearchCapabilities`, `GetSortCapabilities`, `GetSystemUpdateID`.
- The `Search` action must not fail but may return empty results.
- DIDL-Lite XML must use standard UPnP namespaces and follow the ContentDirectory:1 schema.

---

## REQ-2.2: Browse Root Metadata

**Status: ✅ Implemented**

A `Browse("0", "BrowseMetadata")` call must return metadata about the root container.

### Details
- The root container must have:
  - `id="0"`, `parentID="-1"`, `restricted="true"`.
  - `childCount` set to the number of configured streams.
  - `dc:title` set to the server's friendly name.
  - `upnp:class` set to `object.container`.

---

## REQ-2.3: Browse Direct Children

**Status: ✅ Implemented**

A `Browse("0", "BrowseDirectChildren")` call must return a DIDL-Lite XML document containing one `<item>` per configured stream.

### Details
- Each item must include:
  - `id` and `parentID` attributes.
  - `dc:title` element with the stream name (user-configurable).
  - `upnp:class` element set to `object.item.audioItem.audioBroadcast`.
  - `res` element with:
    - The full URL to the stream (`http://IP:PORT/stream/{id}`).
    - `protocolInfo` attribute: `http-get:*:audio/mpeg:*` (or the configured MIME type).
- `StartingIndex` and `RequestedCount` parameters must be respected for pagination.

---

## REQ-2.4: Browse Item Metadata

**Status: ✅ Implemented**

A `Browse("N", "BrowseMetadata")` call (where N is a numeric stream index) must return metadata for a single stream item.

### Details
- Must return the same `<item>` element that would appear for this stream in `BrowseDirectChildren`.
- If `ObjectID` is a non-numeric string (other than "0"), the behavior is implementation-defined but must not crash.

---

## REQ-2.5: Search Action (Empty)

**Status: ✅ Implemented**

The `Search` action must not crash the server.

### Details
- Since this server does not have a search index, `Search` must return `NumberReturned=0`, `TotalMatches=0`, and an empty `Result` string.
- The action must accept the same parameters as `Browse` and process them gracefully.

---

## REQ-2.6: Standard ContentDirectory Actions

**Status: ✅ Implemented**

The standard simple actions must return appropriate values.

### Details
- `GetSearchCapabilities`: Returns empty string (no search is supported).
- `GetSortCapabilities`: Returns empty string (no sort is supported).
- `GetSystemUpdateID`: Returns `0` (no incremental updates are tracked).

---

## REQ-2.7: ConnectionManager Service

**Status: ✅ Implemented**

The server must provide a UPnP ConnectionManager:1 service.

### Details
- Service type: `urn:schemas-upnp-org:service:ConnectionManager:1`.
- Must implement:
  - `GetProtocolInfo`: Returns `Source: http-get:*:audio/mpeg:*` and `Sink: ""`.
  - `GetCurrentConnectionIDs`: Returns `ConnectionIDs: "0"`.
  - `GetCurrentConnectionInfo(ConnectionID)`: Returns dummy info with Status="OK", Direction="Output", ProtocolInfo string.
