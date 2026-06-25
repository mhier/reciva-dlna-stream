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
| `--port` | HTTP server port (default: 0 = auto-assign) |
| `--mime-type` | MIME type of the stream (default: audio/mpeg) |
| `--bind-ip` | IP address to bind the HTTP server to (default: 0.0.0.0) |
| `--verbose`, `-v` | Enable verbose (debug) logging |

## How it works

1. The server announces itself on the local network via UPnP/DLNA SSDP
2. When a DLNA client browses the ContentDirectory, a single audio item is listed
3. When the client requests that item, the server fetches the remote stream and
   forwards it byte-by-byte to the client
4. When the client disconnects, the remote stream fetch is also stopped

## Scripts

Two helper scripts are provided in the repository root:

### `setup.sh` — One-time setup

Creates a Python virtual environment (`.venv`), upgrades pip, and installs the
package along with test dependencies (`pytest`, `pytest-asyncio`).

```bash
./setup.sh                  # uses python3
./setup.sh /path/to/python  # use a specific Python interpreter
```

### `dlna-stream.sh` — Launch wrapper

Starts the server using the virtual environment without requiring manual
activation. Run it from anywhere inside the repository:

```bash
./dlna-stream.sh --stream-url "https://example.com/radio.mp3" [options]
```

It auto-detects `.venv` created by `setup.sh` and forwards all arguments to
`dlna-stream`. If the virtual environment is missing, it prints a clear error
pointing you to run `./setup.sh` first.

## Running tests

```bash
.venv/bin/python -m pytest tests/
```

## Requirements

- Python 3.11+
- Linux (for network socket access)

## License

MIT
