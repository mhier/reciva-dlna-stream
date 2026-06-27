# REQ-3: Stream Serving (HTTP)

| Requirement ID | Title | Status |
|---|---|---|
| REQ-3.1 | Fake Content-Length | ✅ Implemented |
| REQ-3.2 | Persistent Ring Buffer | 🔄 Changed |
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
- The Content-Length must be a large, fixed value (~1.4 GB = 24 hours of 128 kbps MP3).
- The exact value must be consistent across all responses (200 and 206).
- Content-Range headers (for 206 responses) must reflect this same total size.

---

## REQ-3.2: On-Demand Ring Buffer

**Status: ✅ Implemented**

A background task must read the remote Icecast/Shoutcast stream into an in-memory buffer **only while at least one client is connected**. When no clients are connected, the buffer must be stopped and all remote connection resources freed. This avoids unnecessary network traffic and memory usage for idle streams.

### Details
- The buffer is a `bytearray` (or similar mutable bytes container) protected by a lock.
- A background `asyncio.Task` reads from the remote stream URL in chunks (64 KB).
- Data is appended to the buffer as it arrives.
- When the buffer exceeds **64 MB**, the oldest bytes are trimmed (ring buffer behavior).
- The buffer tracks: total bytes ever read, current bytes in buffer.
- Support `async read(offset, size, timeout=30s)` that returns data from the buffer corresponding to the requested byte position in the "virtual file".
- **The buffer reader must only run while at least one client is connected.** When the last client disconnects, the buffer must stop reading and close the remote connection. When a new client connects, the buffer must start reading again.
- Buffer lifecycle is managed by the `StreamForwarder` based on `_active_connections` count: when count goes from 0→1, start the buffer; when count goes from 1→0, stop the buffer.

### Buffer Read Logic

When a reader requests bytes at `offset` with a given `size`:

1. Calculate where this offset falls in the ring buffer: `local_offset = len(buffer) - (total_read - offset)`.
2. If `local_offset >= 0` and enough data is available: return the data immediately.
3. If `local_offset >= 0` but only partial data available: return what's available.
4. If `local_offset < 0`: the requested offset was trimmed from the buffer — raise an error.
5. Otherwise (offset beyond what has been read so far): wait on an `asyncio.Event` for the buffer to advance, retry until timeout.

---

## REQ-3.3: Range Request Handling

**Status: ✅ Implemented**

The server must handle HTTP Range requests (`Range: bytes=...`). Reciva radios request byte ranges sequentially during playback and probe the file size using range requests.

### Details
- The server must parse the `Range` header into `(start, end)` byte range.
- Two types of range requests must be handled:
  1. **Buffer ranges** (`range_end < FOOTER_START`): Serve data from the ring buffer at the requested offset.
  2. **Footer ranges** (`range_end >= FOOTER_START`): Serve synthetic data (the ID3v1.1 footer) — no buffer I/O needed.
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

The server must fabricate a valid 129-byte MPEG audio file footer (1 byte of frame end + 128-byte ID3v1.1 tag) so that Reciva radios can validate the declared Content-Length.

### Details
- The fake "end of file" is at byte position `FAKE_CONTENT_LENGTH - 129` (i.e., `_FOOTER_START`).
- The first byte is `0x00` (simulating the last byte of an MP3 frame).
- The remaining 128 bytes form a valid ID3v1.1 tag:
  - `TAG` magic bytes at offset 0.
  - Title (30 bytes): "Internet Radio" (null-padded).
  - Artist (30 bytes): empty (null-padded).
  - Album (30 bytes): empty (null-padded).
  - Year (4 bytes): current year (e.g. "2026"), null-padded.
  - Comment (28 bytes): empty (null-padded).
  - Null separator (1 byte): `0x00` (indicates ID3v1.1).
  - Track number (1 byte): `0x01`.
  - Genre code (1 byte): `0xFF` (unknown).
- When a range request fully or partially covers the footer range, the server must compute the intersection and return the corresponding slice of the synthetic footer.
- No network I/O or buffer access is needed for footer ranges.

---

## REQ-3.6: Data Consistency

**Status: ✅ Implemented**

Every request for the same byte position N must return the exact same bytes, regardless of when the request is made (within the ring buffer window).

### Details
- This is the fundamental reason for the ring buffer: without it, two requests for byte 0 at different times would get different data from the live stream.
- The ring buffer must be long enough (64 MB) to cover the Reciva radio's probing and playback pattern:
  - First ~256 KB range request (bytes 0-262143)
  - Followed by ~128 KB ranges (bytes 262144-393215, etc.)
  - Plus the end-of-file probe (last 129 bytes)
- As long as the reader stays within the buffered window, all reads are consistent.

---

## REQ-3.7: Active Connection Tracking

**Status: ✅ Implemented**

The server must track all active streaming connections (HTTP response tasks) for resource management and clean shutdown.

### Details
- Each HTTP response that sends stream data must be tracked as an `asyncio.Task` in a set.
- The server must expose a count of active connections.
- On shutdown, all active connections must be cancelled.
- Tasks must be removed from the set in `finally` blocks (task done callback is insufficient if the set is iterated during shutdown).

---

## REQ-3.8: Buffer Auto-Reconnect

**Status: ✅ Implemented**

If the connection to the remote internet radio stream is lost, the buffer background task must automatically reconnect and resume reading.

### Details
- On remote stream end (server closed or stream ended): re-open the connection immediately and resume.
- On remote stream error (DNS failure, connection refused, timeout): log the error, wait 5 seconds, retry.
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
