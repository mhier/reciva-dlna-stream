# Reciva Radio Behavior

## Background
Reciva-based internet radios (e.g. models from Coby, Sangean, some older Logitech devices) access internet radio streams through a DLNA Media Server when the radio is put into "Music Player" / "Media Server" mode. The radio scans the local network for MediaServer devices via SSDP, then browses their ContentDirectory to find audio items.

## ContentDirectory Interaction
The radio discovers the server via SSDP, then:
1. Retrieves `/device.xml` to get the device description
2. Calls `Browse("0", "BrowseMetadata")` to get root container info
3. Calls `Browse("0", "BrowseDirectChildren")` to list available items
4. Extracts the `<res>` URL and `protocolInfo` from the item

## Stream Probing Sequence (KEY)
Once the radio has the stream URL, it probes the file in **two parallel HTTP requests**:

### Request 1: Beginning of file
```
GET /stream HTTP/1.1
Range: bytes=0-131071
```
This requests the first 128 KB of the file. The radio starts receiving data and begins buffering.

### Request 2: End of file (~4 seconds later)
```
GET /stream HTTP/1.1
Range: bytes=1415577471-1415577599
```
This requests the **last 129 bytes** of the declared file size. The radio does this to validate that the Content-Length is real — it expects to get actual MP3 data back containing an ID3v1 tag.

If the end-of-file request fails or hangs, the radio gives up, reports the stream as unavailable, and retries the entire cycle.

## Stream Playback Sequence
Once probing succeeds, the radio plays the stream by requesting sequential ranges:

```
GET /stream HTTP/1.1
Range: bytes=0-262143           (first 256 KB)

GET /stream HTTP/1.1
Range: bytes=262144-393215      (next ~128 KB)

GET /stream HTTP/1.1
Range: bytes=262144-393215      (same range again, if first attempt failed)
```

Each range request at position N must return exactly the data the radio expects at that position. If the data doesn't match (e.g. because a new connection to the Icecast source returns different data at position 0), the radio disconnects and retries.

### What the Radio Expects
The radio expects a proper MP3 file with:
- Valid MPEG audio frames at each byte position (consistent across requests)
- An ID3v1.1 tag (128 bytes) at position `Content-Length - 128`
- The ID3 tag starts with the magic bytes `TAG` (0x54 0x41 0x47)
- Valid `Content-Range` headers on 206 responses
- `Accept-Ranges: bytes` header indicating the resource is seekable

### What the Radio Does NOT Support
- Pure streams with no Content-Length
- HTTP redirects to external URLs
- Non-seekable resources (no Accept-Ranges or Accept-Ranges: none)
- Inconsistent data at the same byte position across different connections

## Implementation Requirements
To satisfy a Reciva radio, the server must:
1. Advertise a **Content-Length** (fake is fine, must be consistent)
2. Advertise **Accept-Ranges: bytes**
3. **Respond 206 Partial Content** to Range requests
4. Serve valid bytes for the **last 129 bytes** of the declared file (a valid ID3v1.1 tag)
5. Serve valid MP3 data for the **first 128 KB** of the declared file
6. Serve **consistent data** at byte position N regardless of when it's requested (requires buffering the live stream)
7. Respond to other requests with the expected HTTP status codes and headers

## Retry Behavior
If any part of the probing or playback fails:
1. The radio waits ~4-5 seconds
2. Logs `contentDidPlay(0)` with a dismiss message
3. Retries the entire discovery + probe + play cycle
4. This continues indefinitely (every ~10 seconds)
