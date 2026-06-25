# dlna-stream

A DLNA Media Server that forwards an internet radio stream to DLNA clients.

Unlike most DLNA radio solutions, this server does **not** serve an `.m3u` playlist file.
Instead, it presents the stream as a regular audio file served via HTTP, making it
compatible with DLNA clients that don't support playlist/radio streaming.

The remote stream is only fetched when a DLNA client requests it, minimising
internet traffic and CPU usage when no client is connected.

## Usage

```bash
dlna-stream --stream-url "https://example.com/radio-stream.mp3"
```

### Options

| Option | Description |
|--------|-------------|
| `--stream-url` | URL of the internet radio stream (required) |
| `--name` | Friendly name of the DLNA server (default: "Internet Radio Stream") |
| `--port` | HTTP server port (default: 0 = auto) |
| `--mime-type` | MIME type of the stream (default: audio/mpeg) |

## How it works

1. The server announces itself on the local network via UPnP/DLNA SSDP
2. When a DLNA client browses the ContentDirectory, a single audio item is listed
3. When the client requests that item, the server fetches the remote stream and
   forwards it byte-by-byte to the client
4. When the client disconnects, the remote stream fetch is also stopped

## Requirements

- Python 3.11+
- Linux (for network socket access)

## License

MIT
