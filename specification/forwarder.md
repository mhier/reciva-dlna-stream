# StreamForwarder Design

## Purpose
The `StreamForwarder` is the core component that handles HTTP requests to `/stream`. It fetches an internet radio stream from a remote URL and forwards it to DLNA clients, while presenting it as a large seekable MP3 file.

## Class: `StreamForwarder`

### Constructor
```python
StreamForwarder(stream_url: str, mime_type: str)
```
- `stream_url`: URL of the remote internet radio stream (e.g. Icecast/Shoutcast)
- `mime_type`: MIME type of the stream (default: `audio/mpeg`)

### Public Methods

#### `handle_request(request: Request) -> StreamResponse`
Main entry point for incoming HTTP requests. The routing decision is:

```
request with Range header?
├── YES, range is parseable?
│   ├── range_end >= FOOTER_START?        → _handle_footer_range()  (206 + synthetic)
│   └── range_end < FOOTER_START?         → _handle_stream_range()  (206 + live data)
└── NO                                    → _handle_full_stream()   (200 + live data)
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
- Body: Streams live data from the remote source indefinitely until client disconnects

#### `_handle_stream_range(request, range_start, range_end) -> StreamResponse`
- Status: `206 Partial Content`
- Headers: Same as above plus `Content-Range: bytes start-end/total`
- Body: Streams live data from the remote source, skipping the first `range_start` bytes, stopping after delivering `range_end - range_start + 1` bytes

#### `_handle_footer_range(request, range_start, range_end) -> StreamResponse`
- Status: `206 Partial Content`
- Headers: Same as stream range
- Body: Synthetic data from `_SYNTHETIC_FOOTER` sliced to the requested range

#### `_forward_stream(response, range_spec) -> int`
Core streaming method:
1. Opens a connection to the remote stream URL (via `aiohttp`)
2. Reads in 64 KB chunks
3. Skips bytes before `range_start` (for range requests)
4. Writes chunks to the response, stopping at `range_end` if specified
5. Returns total bytes sent

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_BUFFER_SIZE` | 64 KB | Chunk size for reading remote stream |
| `_CONNECT_TIMEOUT` | 30s | Timeout for remote stream connection |
| `_READ_TIMEOUT` | 10s | Timeout between data reads from remote |
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
- Remote stream connection failure: caught in `handle_request` caller
- All forwarding tasks are `discard`ed from the set when they complete
