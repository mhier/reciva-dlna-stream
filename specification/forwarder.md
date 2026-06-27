# StreamForwarder & StreamBuffer Design

## Purpose
The `StreamForwarder` handles HTTP requests to `/stream`. The `StreamBuffer` provides a persistent ring buffer that continuously reads from the remote Icecast stream. Together they present the live stream as a seekable MP3 file to DLNA clients, specifically Reciva-based internet radios.

## Problem
The Reciva radio treats the stream as a file and requests ranges at increasing byte positions (`bytes=0-262143`, then `bytes=262144-393215`, etc.). Each request for position N must return the exact bytes the radio expects there. A live stream cannot satisfy this with per-request connections — the stream moves on between requests.

**Solution**: Buffer the stream in a background task so all readers see the same data at each byte position.

## Class: `StreamBuffer`

A persistent background ``asyncio.Task`` reads the remote stream and appends data to a ``bytearray`` protected by an ``asyncio.Lock``.

### Constructor
```python
StreamBuffer(stream_url: str)
```
- `stream_url`: URL of the remote internet radio stream

### Lifecycle Methods

#### `async start()`
Creates a background ``asyncio.Task`` that runs `_run()`.

#### `async stop()`
Cancels the background task. Called during server shutdown.

### Properties
- `buffered_bytes -> int`: Number of bytes currently in the buffer
- `total_bytes_read -> int`: Total bytes ever read from the remote stream (buffer may have been trimmed)

### Background Reader (`_run()`)

```
loop:
  1. Open aiohttp ClientSession + GET stream_url
  2. Read in 64 KB chunks in a for loop
  3. For each chunk:
     a. Acquire lock
     b. Extend bytearray buffer
     c. Increment total_bytes_read
      d. Trim buffer if > 64 MB (delete oldest bytes)
     e. Release lock
     f. Set + clear asyncio.Event (wake up waiters)
     g. asyncio.sleep(0)
  4. On stream end: loop restarts (reconnect)
  5. On error: log, wait 5s, retry
  6. On cancel: return
```

### Read Interface

#### `async read(offset, size, timeout=30.0) -> bytes`
Reads `size` bytes starting at `offset` from the buffer.

Logic:
1. Calculate `local_offset` = where this offset sits in the current buffer:
   `local_offset = len(buffer) - (total_read - offset)`
2. If `local_offset >= 0` and enough data available → return slice immediately
3. If `local_offset >= 0` but partial → return what's available
4. If `local_offset < 0`: offset has been trimmed from the buffer → raise `ValueError`
5. Otherwise: wait on `asyncio.Event` for more data, retry until timeout

## Class: `StreamForwarder`

### Constructor
```python
StreamForwarder(stream_url: str, mime_type: str)      # mime_type is required, no default
```
- `stream_url`: URL of the remote internet radio stream
- `mime_type`: MIME type of the stream (e.g. `"audio/mpeg"`; required, no default in code)

Internally creates a `StreamBuffer` instance.

### Lifecycle Methods

#### `async start_buffer()`
Delegates to `StreamBuffer.start()`.

#### `async stop_buffer()`
Delegates to `StreamBuffer.stop()`.

### Public Methods

#### `handle_request(request: Request) -> StreamResponse`
Main entry point for incoming HTTP requests. The routing decision is:

```
request with Range header?
├── YES, range is parseable?
│   ├── range_end >= FOOTER_START?   → _handle_footer_range() (206 + synthetic)
│   └── range_end < FOOTER_START?    → _handle_buffer_range() (206 + from buffer)
└── NO                               → _handle_full_stream()  (200 + from buffer)
```

Constants:
- `_FAKE_CONTENT_LENGTH` = 1,415,577,600 (~24h of 128kbps MP3)
- `_FOOTER_START` = `_FAKE_CONTENT_LENGTH - 129` = 1,415,577,471
- `_FOOTER_LENGTH` = 129 bytes

#### `active_connection_count -> int`
Returns number of currently active streaming connections.

#### `cancel_all()`
Cancels all active stream forwarding tasks (used during shutdown).

#### `fake_content_length -> int`
Returns the fake Content-Length constant (for tests).

### Internal Methods

#### `_handle_full_stream(request) -> StreamResponse`
- Status: `200 OK`
- Headers: `Content-Type`, `Content-Length` (fake), `Accept-Ranges: bytes`, `TransferMode.DLNA.ORG: Streaming`, `Cache-Control: no-cache`, `Content-Disposition`
- Body: Reads sequentially from the ring buffer (`_buffer.read(bytes_sent, _BUFFER_SIZE)`), sending data indefinitely until client disconnects

#### `_handle_buffer_range(request, range_start, range_end) -> StreamResponse`
- Status: `206 Partial Content`
- Headers: Same as above plus `Content-Range: bytes start-end/total`
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
| `_MAX_BUFFER_SIZE` | 64 MB | Maximum ring buffer size before trimming old data |
| `_FAKE_CONTENT_LENGTH` | 1,415,577,600 | 24h of 128kbps MP3 |
| `_FOOTER_LENGTH` | 129 bytes | 1 byte (frame end) + 128 bytes (ID3v1) |
| `_FOOTER_START` | 1,415,577,471 | Byte offset where footer begins |

## Synthetic Footer Design

The ID3v1.1 tag format (128 bytes at EOF):

```
Offset  Length  Content
0       3       "TAG" (magic identifier)
3       30      Title (null-padded) → "Internet Radio"
33      30      Artist (null-padded) → empty
63      30      Album (null-padded) → empty
93      4       Year → "2026"
97      28      Comment (null-padded)
125     1       Null separator (0x00 = ID3v1.1)
126     1       Track number → 1
127     1       Genre code → 255 (Unknown)
```

The full 129-byte footer is `\x00` + 128-byte ID3v1 tag.

## Active Connection Tracking

Each streaming task is tracked in `_active_connections: set[asyncio.Task]`. This allows:
- Querying active connection count
- Cancelling all connections on shutdown via `cancel_all()`
- Cleanup in `finally` blocks when connections drop

## Error Handling

- Client disconnects mid-write: caught `ConnectionResetError`/`ConnectionAbortedError`, task exits cleanly
- Buffer read timeout: returns empty bytes, caller sends what it has so far
- Buffer offset trimmed: raises `ValueError`, caught and logged
- Remote stream connection failure in buffer: caught in `_run()` loop, retries after 5s
- All forwarding tasks are `discard`ed from the set when they complete
