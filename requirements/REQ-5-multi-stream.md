# REQ-5: Multi-Stream Support

| Requirement ID | Title | Status |
|---|---|---|
| REQ-5.1 | JSON Configuration File | ✅ Implemented |
| REQ-5.2 | Indexed Routes | ✅ Implemented |
| REQ-5.3 | Stream-Specific Metadata | ✅ Implemented |
| REQ-5.4 | Route Disambiguation | ✅ Implemented |
| REQ-5.5 | Container Child Count | ✅ Implemented |
| REQ-5.6 | Independent Stream Buffers | ✅ Implemented |

---

## REQ-5.1: JSON Configuration File

**Status: ✅ Implemented**

The server must support a JSON configuration file that defines multiple streams.

### Details
- CLI argument: `--config PATH` (mutually exclusive with `--stream-url`).
- The JSON file must contain a list of stream objects, each with:
  - `url` (required): URL of the internet radio stream.
  - `name` (required): Display name for the ContentDirectory item.
  - `mime_type` (optional, default: `"audio/mpeg"`): MIME type of the stream.
- Example:
  ```json
  {
    "streams": [
      {
        "url": "http://example.com/stream1",
        "name": "Station One",
        "mime_type": "audio/mpeg"
      },
      {
        "url": "http://example.com/stream2",
        "name": "Station Two"
      }
    ]
  }
  ```

---

## REQ-5.2: Indexed Routes

**Status: ✅ Implemented**

In multi-stream mode, each stream must be accessible via an indexed HTTP route.

### Details
- Streams are accessible at `/stream/0`, `/stream/1`, etc. (zero-indexed by their order in the config).
- Each route delegates to the corresponding `StreamForwarder.handle_request()`.

---

## REQ-5.3: Stream-Specific Metadata

**Status: ✅ Implemented**

Each stream in the ContentDirectory must have its own name (title), MIME type, and URL.

### Details
- `BrowseDirectChildren` must return one `<item>` per stream, each with its own:
  - `dc:title`: The stream's configured name.
  - `res`: The stream's indexed URL (`/stream/{id}`).
  - `protocolInfo`: Based on the stream's configured MIME type.
- `BrowseMetadata` for a specific item ID must return the same metadata as the corresponding item in the children list.

---

## REQ-5.4: Route Disambiguation

**Status: ✅ Implemented**

In multi-stream mode, the legacy `/stream` route must NOT be registered, to avoid ambiguity.

### Details
- If there is exactly 1 stream, both `/stream` (legacy) and `/stream/0` are registered.
- If there are 2 or more streams, only `/stream/{index}` routes are registered.
- A request to `/stream` in multi-stream mode must return 404.

---

## REQ-5.5: Container Child Count

**Status: ✅ Implemented**

The root container metadata must correctly report the number of streams.

### Details
- `Browse("0", "BrowseMetadata")` must return `childCount` equal to the number of configured streams.
- The container title is the server's friendly name (from the device definition, not configurable per individual stream).

---

## REQ-5.6: Independent Stream Buffers

**Status: ✅ Implemented**

Each stream must have its own independent ring buffer and background reader.

### Details
- Each `StreamForwarder` creates its own `StreamBuffer` instance.
- Buffers run independently (one BufferError in one stream does not affect others).
- On shutdown, all stream buffers are stopped.
- Active connection counting is per-forwarder (not global).
