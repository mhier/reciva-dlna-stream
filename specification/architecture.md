# Architecture Overview

## Purpose
`dlna-stream` is a DLNA Media Server that makes a live internet radio stream (e.g. Icecast/Shoutcast MP3) discoverable and playable by UPnP/DLNA clients, specifically Reciva-based internet radios.

The core challenge: DLNA clients treat streams as **files with a fixed size**. They probe the file size via HTTP Range requests before playing. A live stream has no fixed size, so the server must fake it convincingly enough for the client to start playing.

## High-Level Architecture

```
┌─────────────┐     SSDP NOTIFY      ┌──────────────────────┐
│  DLNA Client │◄────────────────────►│     dlna-stream      │
│ (Reciva Radio)│    HTTP (stream)    │                      │
└─────────────┘                      │  HTTP Server         │
                                     │  (aiohttp :port)     │
                                     │                      │
                                     │  ┌──────────────────┐ │
                                     │  │  StreamBuffer    │ │
                                     │  │  (ring buffer)   │◄┼─── Internet Radio
                                     │  │  up to 512 MB    │◄─┼─── (Icecast URL)
                                     │  └───────┬──────────┘ │
                                     │          │            │
                                     │          ▼            │
                                     │  ┌──────────────────┐ │
                                     │  │ StreamForwarder  │ │
                                     │  │ (HTTP handlers)  │ │
                                     │  └──────────────────┘ │
                                     │                      │
                                     │  ┌──────────────────┐ │
                                     │  │ ContentDirectory │ │
                                     │  │ Service          │ │
                                     │  └──────────────────┘ │
                                     │                      │
                                     │  ┌──────────────────┐ │
                                     │  │ ConnectionManager│ │
                                     │  │ Service          │ │
                                     │  └──────────────────┘ │
                                     │                      │
                                     │  ┌──────────────────┐ │
                                     │  │ SSDP Announcer + │ │
                                     │  │ Search Responder │ │
                                     │  └──────────────────┘ │
                                     └──────────────────────┘
```

## Key Design Decisions

### 1. Fake Content-Length
The server advertises a fake `Content-Length` (~1.4 GB = 24 hours of 128 kbps MP3). This satisfies clients that check Content-Length before accepting a stream.

### 2. Persistent Ring Buffer
A background task (`StreamBuffer`) continuously reads the remote Icecast stream into a `bytearray` buffer (up to 512 MB). All HTTP requests (range or full) read from this buffer at the correct offset. This ensures that:

- Byte position N always returns the **same data** regardless of when it's requested
- The radio can request `bytes=262144-393215` and get the same content it got at position 262144 from the first request
- Multiple concurrent clients see consistent data

Without the ring buffer, each range request opened a new Icecast connection — but the stream is live, so "byte 0" is different every time you connect. The radio got garbage at position 262144 and disconnected.

### 3. Hybrid Range Handling
DLNA clients (especially Reciva radios) probe the file size by:
1. Requesting `Range: bytes=0-131071` (first 128 KB) — served from the ring buffer
2. Requesting `Range: bytes=<end-128>-<end>` (last 129 bytes) — served from synthetic ID3v1.1 tag

### 4. Synthetic ID3v1.1 Footer
The last 129 bytes of a real MP3 file typically contain:
- 1 byte: end of the last MP3 audio frame
- 128 bytes: ID3v1.1 tag (metadata)

The server constructs a fake ID3v1.1 tag that starts with `TAG` magic bytes and contains sensible defaults (title "Internet Radio", current year, etc.). This satisfies the client's probe.

### 5. Server Lifecycle Ordering
The upstream `async_upnp_client` library creates the SSDP device before starting the HTTP server. When using port 0 (auto-assign), SSDP advertisements go out with `LOCATION: http://IP:0/device.xml` which is broken. The fix: determine the port first (via temporary socket binding if needed), create the device with the correct port, then start SSDP.

## Directories and Files

```
dlna_stream/
├── __init__.py          # Package marker, exports
├── __main__.py          # CLI entry point, arg parsing
├── forwarder.py         # StreamForwarder + StreamBuffer
├── server.py            # UPnP device + service definitions
└── server_lifecycle.py  # Server startup/shutdown orchestration

tests/
├── __init__.py
├── conftest.py          # Test fixtures
└── test_integration.py  # Integration tests (7 tests)

specification/
├── architecture.md      # This file
├── forwarder.md         # StreamForwarder + StreamBuffer design
├── server.md            # UPnP device/service design
├── server-lifecycle.md  # Server startup design
├── testing.md           # Test design
└── radio-behavior.md    # Reciva radio behavior notes
```
