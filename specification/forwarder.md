# StreamForwarder & StreamBuffer Design

## Purpose
The `StreamForwarder` handles HTTP requests to `/stream`. The `StreamBuffer` provides an on-demand ring buffer that reads from the remote Icecast stream only while clients are connected. Together they present the live stream as a seekable MP3 file to DLNA clients, specifically Reciva-based internet radios.

## Problem
The Reciva radio treats the stream as a file and requests ranges at increasing byte positions (`bytes=0-262143`, then `bytes=262144-393215`, etc.). Each request for position N must return the exact bytes the radio expects there. A live stream cannot satisfy this with per-request connections — the stream moves on between requests.

**Solution**: Buffer the stream in a background task so all readers see the same data at each byte position. The buffer runs **only while at least one client is connected**, freeing resources (ClientSession, TCP connection, bytearray memory) when idle.

## Class: `StreamBuffer`

An on-demand ``asyncio.Task`` that reads the remote stream and appends data to a ``bytearray``. Synchronization between the writer (``_run()``) and readers (``read()``) uses ``asyncio.Condition`` as the sole synchronization primitive (it wraps an ``asyncio.Lock`` + condition variable internally). There is no separate ``Lock`` — the writer acquires the ``Condition`` for both data mutation and waiter notification, avoiding the TOCTOU race inherent in separate ``Lock + Event`` usage. The task only exists while clients are connected.

### Constructor
```python
StreamBuffer(stream_url: str)
```
- `stream_url`: URL of the remote internet radio stream

### Lifecycle Methods

#### `async start()`
Creates a background ``asyncio.Task`` that runs `_run()`. Creates a new `aiohttp.ClientSession` and `TCPConnector`.

#### `async stop()`
Cancels the background task, closes the `ClientSession` and `TCPConnector`. Called when the last client disconnects or during server shutdown.

### Properties
- `buffered_bytes -> int`: Number of bytes currently in the buffer
- `total_bytes_read -> int`: Total bytes ever read from the remote stream (buffer may have been trimmed)
- `is_running -> bool`: Whether the buffer background task is currently running

### Background Reader (`_run()`)

```
1. Create aiohttp.ClientSession + TCPConnector
2. loop:
    3. GET stream_url
    4. Read in 64 KB chunks
    5. For each chunk:
       a. Acquire asyncio.Condition (sole lock)
       b. Extend bytearray buffer
       c. Increment total_bytes_read
       d. Trim buffer if > 4 MB (delete oldest bytes)
       e. Notify all waiters via asyncio.Condition.notify_all()
       f. Release asyncio.Condition (lock released automatically)
       g. asyncio.sleep(0) — only yielded periodically (every N chunks, not every chunk)
         to reduce event-loop overhead while still allowing readers to make progress
    6. On stream end: loop restarts (reconnect)
    7. On error: log, wait 5s, retry
    8. On cancel or _stopped: break out, close session/connector
```

### Read Interface

#### `async read(offset, size, timeout=30.0) -> bytes`
Reads `size` bytes starting at `offset` from the buffer.

Implemented using ``asyncio.Condition``, which wraps a lock and a condition
variable. This avoids the classic TOCTOU race of ``Lock + Event`` where data
could arrive between releasing the lock and calling ``event.wait()``.

Logic:
1. Acquire the condition's lock (mutual exclusion with the writer).
2. If ``offset < total_read``: data at this position has (or had) been read from
   the remote stream.
   a. Calculate ``local_offset = len(buffer) - (total_read - offset)``.
   b. If ``local_offset >= 0`` and enough data available → return slice immediately.
   c. If ``local_offset >= 0`` but partial → return what's available.
   d. If ``local_offset < 0``: offset has been trimmed from the buffer → raise
      ``ValueError``.
3. If ``offset >= total_read``: the requested position hasn't been reached yet;
   fall through to waiting.
4. Wait on ``asyncio.Condition.wait()`` for more data, looping until deadline.
   - ``Condition.wait()`` atomically releases the lock and suspends, avoiding
     the race between checking state and starting to wait.
   - Uses ``asyncio.wait_for(condition.wait(), timeout=...)`` with a dynamic timeout.
   - Catches ``asyncio.TimeoutError`` and falls through to the deadline check.
5. If deadline exceeded: log warning at ``WARNING`` level, return ``b""``.

## Class: `StreamForwarder`

### Constructor
```python
StreamForwarder(stream_url: str, mime_type: str, verbose_logging: bool = False)
```
- `stream_url`: URL of the remote internet radio stream
- `mime_type`: MIME type of the stream (e.g. `"audio/mpeg"`; required, no default in code)
- `verbose_logging`: If `True`, emit per-chunk progress DEBUG logs during streaming (every chunk until 2 KB sent, then every 512 KB). Default `False` to reduce log noise in normal operation.

Internally creates a `StreamBuffer` instance. The buffer is **not started automatically** — it starts on first client connection that actually needs buffer data (footer-only requests skip starting the buffer). See \"Footer Early-Return Optimization\" under `handle_request` below. Also creates a `_disconnect_timer: asyncio.Task | None` for the grace period.

### Properties
- `active_connection_count -> int`: Number of currently active streaming connections.
- `fake_content_length -> int`: The fake Content-Length constant.
- `pending_disconnect -> bool`: Whether a disconnect timer is pending (grace period active).

### Lifecycle Methods

#### `async start_buffer()`
Delegates to `StreamBuffer.start()`. Used during server startup for pre-warming (if desired, but typically the buffer starts on demand).

#### `async stop_buffer()`
Delegates to `StreamBuffer.stop()`.

### On-Demand Buffer Lifecycle with Grace Period

The buffer is started/stopped based on `_active_connections` and a configurable grace period:

- **When `handle_request` is called with a buffer-range or full-stream request**: If this is the first connection, ensure the buffer is running and cancel any pending disconnect timer (grace period). Footer-only requests skip buffer lifecycle entirely.
- **When a client disconnects** (`finally` block in handler): If this was the last connection, start a **disconnect timer** with the grace period timeout (default: 10 seconds) instead of stopping the buffer immediately.
- **When the disconnect timer fires**: Stop the buffer (close remote connection, free resources).
- **When a new client connects while the timer is pending**: Cancel the timer, buffer keeps running.

This ensures:
- The ring buffer accumulates data continuously while any client is active.
- After the last client disconnects, the buffer persists for 10 seconds in case the client reconnects (common Reciva behavior during re-buffering or sequential range requests).
- Remote connection resources are eventually freed after the grace period expires.

### Public Methods

#### `handle_request(request: Request) -> StreamResponse`
Main entry point for incoming HTTP requests. Manages buffer lifecycle and routing:

```
1. Track client connection (add task to _active_connections)
2. Cancel any pending disconnect timer (grace period)
3. Parse Range header, if present:
   ├── Range targets footer (range_end >= FOOTER_START) → skip buffer start,
   │   serve from synthetic footer immediately, then exit
   └── Range targets buffer data → proceed to step 4
4. If buffer is not running: start the buffer
5. Route to appropriate handler:
   ├── Buffer range (range_end < FOOTER_START)  → _handle_buffer_range()
   └── No Range header                          → _handle_full_stream()
6. In finally: remove task from _active_connections
7. If _active_connections is now empty: start the disconnect timer
   (buffer continues running during the grace period)
```

**Footer early-return optimization**: The footer-range check happens **before** the buffer lifecycle logic. This avoids starting the ring buffer unnecessarily for synthetic footer probes. This matters because the Reciva radio sends two simultaneous parallel requests during probing:
- Request A: `Range: bytes=0-131071` (first 128 KB) — needs the buffer
- Request B: `Range: bytes=<end-128>-<end>` (last 129 bytes) — served from synthetic footer, no buffer needed

Without this optimization, request B would start the buffer unnecessarily, and since B's handler exits before A connects, the buffer would start and then immediately enter the grace period (unnecessary start/stop cycle). With the optimization, only request A starts the buffer.

Constants:
- `_FAKE_CONTENT_LENGTH` = 1,415,577,600 (~24h of 128kbps MP3)
- `_FOOTER_START` = `_FAKE_CONTENT_LENGTH - 129` = 1,415,577,471
- `_FOOTER_LENGTH` = 129 bytes

#### `active_connection_count -> int`
Returns number of currently active streaming connections.

#### `async cancel_all()`
Cancels all active stream forwarding tasks (used during shutdown). Also properly awaits the disconnect timer cancellation so no lingering tasks remain.

Because the cancelled tasks' ``finally`` blocks (via ``_maybe_stop_buffer()``) can start a new disconnect timer after the initial cancellation, ``cancel_all()``:
1. Cancels the disconnect timer first.
2. Cancels and **awaits** all connection tasks via ``asyncio.gather()`` so their cleanup runs to completion.
3. Cancels the disconnect timer again (in case a ``_maybe_stop_buffer()`` call started one).

#### `fake_content_length -> int`
Returns the fake Content-Length constant (for tests).

### Internal Methods

#### `_handle_full_stream(request) -> StreamResponse`
- Status: `200 OK`
- Headers: `Content-Type`, `Content-Length` (fake), `Accept-Ranges: bytes`, `TransferMode.DLNA.ORG: Streaming`, `Cache-Control: no-cache`, `Content-Disposition`
- Body: Reads sequentially from the ring buffer (`_buffer.read(bytes_sent, _BUFFER_SIZE)`), sending data indefinitely until client disconnects or the buffer stops (disconnect timer expires).
- When `read()` returns empty bytes (timeout): checks `_buffer._stopped` and breaks if the buffer was stopped; otherwise yields control via `asyncio.sleep(0)` and retries.
- `bytes_sent` is always defined in the ``try`` block before the ``finally`` handler (``_write_chunks_to_response`` always runs), so the ``finally`` can reference it directly without runtime introspection.

#### `_handle_buffer_range(request, range_start, range_end) -> StreamResponse`
- Status: `206 Partial Content` (or `416 Range Not Satisfiable` if offset trimmed)
- If the requested byte offset has been trimmed from the ring buffer (because the buffer overflowed 4 MB and old data was discarded), returns `416` with `Content-Range: bytes */{fake_content_length}`.
- Headers for 206: Same as above plus `Content-Range: bytes start-end/total`
- Body: Reads from the ring buffer in chunks at the requested offset
  ```
  offset = range_start
  remaining = content_length
  while remaining > 0:
    chunk = await _buffer.read(offset, min(remaining, 64KB))
    if empty: break  # timeout
    response.write(chunk)
    offset += len(chunk)
    remaining -= len(chunk)
  ```
- The first buffer read is attempted **before** preparing the response so that a `ValueError` (trimmed offset) can be caught and a 416 response returned instead of sending a 206 with no body.

#### `_handle_footer_range(request, range_start, range_end) -> StreamResponse`
- Status: `206 Partial Content`
- Headers: Same as stream range but with synthetic Content-Length/Content-Range
- Body: Synthetic data from `_SYNTHETIC_FOOTER` sliced to the requested range (computed from memory, no network I/O)

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_BUFFER_SIZE` | 64 KB | Chunk size for reading remote stream |
| `_CONNECT_TIMEOUT` | 30s | Timeout for remote stream connection |
| `_READ_TIMEOUT` | 10s | Timeout between data reads from remote |
| `_MAX_BUFFER_SIZE` | 4 MB | Maximum ring buffer size before trimming old data (~1 min of 320 kbps) |
| `_FAKE_CONTENT_LENGTH` | 1,415,577,600 | 24h of 128kbps MP3 |
| `_FOOTER_LENGTH` | 129 bytes | 1 byte (frame end) + 128 bytes (ID3v1) |
| `_FOOTER_START` | 1,415,577,471 | Byte offset where footer begins |
| `_DISCONNECT_TIMEOUT` | 10s | Grace period after last client disconnects |

## Synthetic Footer Design

The ID3v1.1 tag format (128 bytes at EOF):

```
Offset  Length  Content
0       3       "TAG" (magic identifier)
3       30      Title (null-padded) → "Internet Radio"
33      30      Artist (null-padded) → empty
63      30      Album (null-padded) → empty

93      4       Year → current year (computed at call time via ``datetime.datetime.now().year``)
97      28      Comment (null-padded)
125     1       Null separator (0x00 = ID3v1.1)
126     1       Track number → 1
127     1       Genre code → 255 (Unknown)
```

The full 129-byte footer is `\x00` + 128-byte ID3v1 tag.

## Active Connection Tracking

Each streaming task is tracked in `_active_connections: set[asyncio.Task]`. This allows:
- Querying active connection count
- Starting the buffer when the first client connects
- Stopping the buffer when the last client disconnects
- Cancelling all connections on shutdown via `cancel_all()`
- Cleanup in `finally` blocks when connections drop

## Error Handling

- Client disconnects mid-write: caught `ConnectionResetError`/`ConnectionAbortedError`, task exits cleanly
- Buffer read timeout: returns empty bytes, caller sends what it has so far
- Buffer offset trimmed: raises `ValueError`, caught and a `416 Range Not Satisfiable` response is returned with `Content-Range: bytes */{total}`
- Remote stream connection failure in buffer: caught in `_run()` loop, retries after 5s
- All forwarding tasks are `discard`ed from the set when they complete

## Implementation Status

**Status: CHANGED** — Specification has been updated with multiple refinements
that are not yet reflected in the code.

| Change | Status |
|--------|--------|
| `StreamBuffer` uses `asyncio.Condition` as sole lock (no separate `Lock`) | **Spec changed, code not updated** |
| Writer yields via `asyncio.sleep(0)` periodically (every N chunks) instead of every chunk | **Spec changed, code not updated** |
| Footer-range check before buffer lifecycle (footer early-return optimization) | **Spec changed, code not updated** |
| `bytes_sent` in `_handle_full_stream` uses direct variable reference (no `dir()` introspection) | **Spec changed, code not updated** |
| All other behavior (ring buffer, synthetic footer, grace period, connection tracking) | Implemented |
