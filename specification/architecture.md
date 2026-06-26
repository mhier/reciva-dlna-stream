# Architecture Overview

## Purpose
`reciva-dlna-stream` is a DLNA Media Server that makes a live internet radio stream (e.g. Icecast/Shoutcast MP3) discoverable and playable by UPnP/DLNA clients.

**This project is highly tailored to serve a Reciva-based internet radio.** Every significant design decision is driven by how Reciva radios (common in older internet radios from Coby, Sangean, and others) behave when accessing a DLNA Media Server. The server may work with other DLNA clients, but it is tested and tuned specifically for Reciva.

The core challenge: Reciva radios treat streams as **files with a fixed size**. They probe the file size via HTTP Range requests before playing. A live stream has no fixed size, so the server must fake it convincingly enough for the client to start playing.

## High-Level Architecture

```
┌─────────────┐     SSDP NOTIFY      ┌──────────────────────┐
│  DLNA Client │◄────────────────────►│     reciva-dlna-stream      │
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
                                      │  │ (per stream)     │ │
                                      │  │ /stream/{id}     │ │
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
The server advertises a fake `Content-Length` (~1.4 GB = 24 hours of 128 kbps MP3). Reciva radios check `Content-Length` before accepting a stream — if absent or too small, they reject it immediately.

### 2. Persistent Ring Buffer
A background task (`StreamBuffer`) continuously reads the remote Icecast stream into a `bytearray` buffer (up to 512 MB). All HTTP requests (range or full) read from this buffer at the correct offset. This is necessary because Reciva radios request sequential byte ranges in separate TCP connections:

- `bytes=0-262143` (first 256 KB)
- `bytes=262144-393215` (next ~128 KB)
- etc.

Without the ring buffer, each range request opened a new Icecast connection — but the stream is live, so "byte 0" is different every time you connect. The radio got garbage at position 262144 and disconnected.

### 3. Hybrid Range Handling
Reciva radios probe the declared file size in **two parallel HTTP requests** before they start playing:
1. Requesting `Range: bytes=0-131071` (first 128 KB) — served from the ring buffer
2. Requesting `Range: bytes=<end-128>-<end>` (last 129 bytes) — served from synthetic ID3v1.1 tag

### 4. Synthetic ID3v1.1 Footer
The Reciva radar specifically validates the last 129 bytes of the declared file to confirm it is a real MP3. The last 129 bytes of a real MP3 file typically contain:
- 1 byte: end of the last MP3 audio frame
- 128 bytes: ID3v1.1 tag (metadata)

The server constructs a fake ID3v1.1 tag that starts with `TAG` magic bytes and contains sensible defaults (title "Internet Radio", current year, etc.). This satisfies the client's probe.

### 5. Server Lifecycle Ordering
The upstream `async_upnp_client` library creates the SSDP device before starting the HTTP server. When using port 0 (auto-assign), SSDP advertisements go out with `LOCATION: http://IP:0/device.xml` which is broken. The fix: determine the port first (via temporary socket binding if needed), create the device with the correct port, then start SSDP.

## Directories and Files

```
reciva_dlna_stream/
├── __init__.py          # Package marker, exports
├── __main__.py          # CLI entry point, arg parsing
├── forwarder.py         # StreamForwarder + StreamBuffer
├── server.py            # UPnP device + service definitions
├── server_lifecycle.py  # Server startup/shutdown orchestration
└── stream_config.py     # Config file parsing (multi-stream)

tests/
├── __init__.py
├── conftest.py          # Test fixtures (single + multi-stream)
└── test_integration.py  # Integration tests (22 tests)

specification/
├── architecture.md      # This file
├── forwarder.md         # StreamForwarder + StreamBuffer design
├── server.md            # UPnP device/service design
├── server-lifecycle.md  # Server startup design
├── testing.md           # Test design
└── radio-behavior.md    # Reciva radio behavior notes
```
