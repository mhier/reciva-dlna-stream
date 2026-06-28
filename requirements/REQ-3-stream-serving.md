# REQ-3: Stream Serving (HTTP)

| Requirement ID | Title | Status |
|---|---|---|
| REQ-3.1 | Fake Content-Length | ✅ Implemented |
| REQ-3.2 | Persistent Ring Buffer with Grace Period | ✅ Implemented |
| REQ-3.3 | Range Request Handling | ✅ Implemented |
| REQ-3.4 | Full Stream (Non-Range) Request | ✅ Implemented |
| REQ-3.5 | Synthetic ID3v1.1 Footer (End-of-File) | ✅ Implemented |
| REQ-3.6 | Data Consistency | ✅ Implemented |
| REQ-3.7 | Active Connection Tracking | ✅ Implemented |
| REQ-3.8 | Buffer Auto-Reconnect | ✅ Implemented |
| REQ-3.9 | HTTP Response Headers | ✅ Implemented |

---

## REQ-3.1: Fake Content-Length

**Status: ✅ Implemented**

The server must advertise a `Content-Length` header on all stream responses, even though the underlying source is a live stream with no known length. This is required because Reciva radios reject streams without a declared size.

### Details
- The Content-Length must be a large, fixed value — large enough for ~24 hours of streaming at a typical bitrate (e.g. 128 kbps MP3).
- The exact value must be consistent across all responses (200 and 206).
- Content-Range headers (for 206 responses) must reflect this same total size.

---

## REQ-3.2: Persistent Ring Buffer with Grace Period

**Status: ✅ Implemented**

A background task must read the remote Icecast/Shoutcast stream into an in-memory ring buffer. The buffer must persist for a configurable grace period after all clients disconnect, maintaining its accumulated data so that reconnecting clients get consistent data. This prevents the "re-buffering → disconnect" cycle seen when the buffer stops immediately after each range request completes.

### Details
- The buffer must be a mutable byte container that supports appending new data and trimming from the front.
- A background task must continuously read from the remote stream URL in chunks and append data to the buffer.
- When the buffer exceeds a configurable maximum size (default: 4 MB), the oldest bytes must be trimmed (ring buffer behavior).
- The buffer must track: total bytes ever read, current bytes in buffer.
- Readers must be able to request data by byte offset with a configurable timeout (default: 30 s), returning the corresponding portion of accumulated data.

### Grace Period (Keep-Alive Timeout)
- **When the last client disconnects**, the buffer must NOT stop immediately.
- Instead, the buffer enters a **grace period** (default: **10 seconds**) during which:
  - The buffer continues running (remote stream keeps reading, data keeps accumulating in the ring buffer).
  - If a new client connects during the grace period, the buffer continues uninterrupted (grace period is cancelled).
  - If no client connects before the grace period expires, the buffer is stopped and all remote connection resources are freed.
- Buffer lifecycle must be governed by active connection count transitions:
  - When the first client connects: ensure the buffer is active and cancel any pending disconnect timer.
  - When the last client disconnects: start a timer with the grace period duration.
  - When the timer fires: stop the buffer.
- This ensures:
  - No gap in stream data when the client reconnects quickly (e.g. sequential range requests or re-buffering).
  - Remote connection resources are still freed after a reasonable idle period.
  - The accumulated ring buffer data is available for the reconnecting client.

### Buffer Read Behavior

When a reader requests bytes at a given offset:

- If the offset falls within the data still retained in the buffer and enough data is available: return the requested data.
- If the offset falls within retained data but only partial data is available: return what is available.
- If the offset has been trimmed (too old to be in the buffer): signal an error to the reader.
- If the offset is beyond what the remote stream has provided so far: wait for more data (with a timeout).

---

## REQ-3.3: Range Request Handling

**Status: ✅ Implemented**

The server must handle HTTP Range requests (`Range: bytes=...`). Reciva radios request byte ranges sequentially during playback and probe the file size using range requests.

### Details
- The server must parse the `Range` header into `(start, end)` byte range.
- Requests that overlap with the synthetic footer region must return the appropriate slice of synthetic footer data, without reading from the buffer.
- Requests entirely within the buffered stream data must serve data from the ring buffer at the requested offset.
- All range responses must be `206 Partial Content` with a valid `Content-Range` header.
- The `Content-Range` must be in the format: `bytes start-end/total` (where total is the fake Content-Length).

---

## REQ-3.4: Full Stream (Non-Range) Request

**Status: ✅ Implemented**

When no `Range` header is present, the server must serve the stream as a full-length response.

### Details
- Status: `200 OK`.
- Headers: `Content-Type`, `Content-Length` (fake), `Accept-Ranges: bytes`, `TransferMode.DLNA.ORG: Streaming`, `Cache-Control: no-cache`, `Content-Disposition`.
- Body: Stream the ring buffer data sequentially (start at byte 0, read chunks as they arrive).
- The response must continue indefinitely until the client disconnects.
- The client may disconnect at any time; this must be handled gracefully (catch connection reset).

---

## REQ-3.5: Synthetic ID3v1.1 Footer (End-of-File)

**Status: ✅ Implemented**

The server must fabricate a valid MPEG audio file footer (with an ID3v1.1 tag) so that Reciva radios can validate the declared Content-Length.

### Details
- The footer must be placed at byte position `FAKE_CONTENT_LENGTH - 129` (the very end of the virtual file).
- The first byte must simulate the end of an MP3 frame.
- The remaining 128 bytes must form a valid ID3v1.1 tag with:
  - A recognizable title (e.g. "Internet Radio").
  - The current year.
  - A track number.
  - All other standard ID3v1.1 fields populated with sensible defaults.
- When a range request covers the footer region, the server must return the intersecting portion of the synthetic footer.
- Serving the footer must not require any network I/O.

---

## REQ-3.6: Data Consistency

**Status: ✅ Implemented**

Every request for the same byte position N must return the exact same bytes, regardless of when the request is made (within the ring buffer window).

### Details
- This is the fundamental reason for the ring buffer: without it, two requests for byte 0 at different times would get different data from the live stream.
  - The ring buffer must be long enough (e.g. 4 MB) to cover the Reciva radio's probing and playback pattern: an initial range request of a few hundred KB, followed by smaller sequential ranges, plus an end-of-file probe.
- As long as the reader stays within the buffered window, all reads are consistent.

---

## REQ-3.7: Active Connection Tracking

**Status: ✅ Implemented**

The server must track all active streaming connections (HTTP response tasks) for resource management and clean shutdown.

### Details
- Each streaming response must be tracked as a cancellable unit of work.
- The server must expose a count of active connections.
- On shutdown, all active connections must be cancelled.
- Tracking must be cleaned up reliably when a streaming response ends, even during shutdown.

---

## REQ-3.8: Buffer Auto-Reconnect

**Status: ✅ Implemented**

If the connection to the remote internet radio stream is lost, the buffer background task must automatically reconnect and resume reading.

### Details
- On remote stream end (server closed or stream ended): reconnect and resume reading immediately.
- On remote stream error (DNS failure, connection refused, timeout): log the error, wait before retrying.
- The buffer must never crash or terminate permanently due to a remote stream failure.
- All readers should continue to be served from whatever data remains in the buffer while the connection is down.

---

## REQ-3.9: HTTP Response Headers

**Status: ✅ Implemented**

All stream responses must include the correct HTTP headers for DLNA compliance and Reciva radio compatibility.

### Details

Headers required on all stream responses:
- `Content-Type`: The MIME type of the stream (default: `audio/mpeg`).
- `Content-Length`: The fake Content-Length value.
- `Accept-Ranges: bytes` (signals that range requests are supported).
- `Cache-Control: no-cache` (live content must not be cached).
- `Content-Disposition: attachment; filename="stream.mp3"` (or similar — optional, for filename hint).

Headers required on 206 (range) responses (in addition to the above):
- `Content-Range: bytes start-end/total` (valid RFC 7233 format).

Header for DLNA streaming:
- `TransferMode.DLNA.ORG: Streaming` (required by DLNA for streaming media).
