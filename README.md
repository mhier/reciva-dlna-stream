# reciva-dlna-stream

A DLNA Media Server **highly tailored to serve a Reciva-based internet radio**.

This project exists because Reciva radios (common in older internet radios from
Coby, Sangean, and other brands) treat streams as **files**. They probe the HTTP
server with Range requests, validate the file size via the last 129 bytes (ID3v1
tag), and abort if the content at a given byte position changes between requests.

The entire design — ring buffer, synthetic ID3v1 footer, fake Content-Length,
hybrid Range handling — is built specifically around Reciva radio behavior.

Unlike most DLNA radio solutions, this server does **not** serve an `.m3u` playlist file.
Instead, it presents the stream as a regular audio file served via HTTP, making it
compatible with DLNA clients that don't support playlist/radio streaming.

The remote stream is only fetched when a DLNA client requests it, minimising
internet traffic and CPU usage when no client is connected.

Note: This project has been shamelessly vibe coded. I needed a quick solution to get
my old Reciva radio working again (Sharpfin did not work for me, I somehow locked me
out permanently). I hence do not claim this is my work, and I am not proud of it. I am
also using it to try various techiques of improved vibe coding.

If you don't like this or if you are looking for a stable piece of software, stay away
from it :-)

## Usage

### Single stream (legacy CLI arguments)

```bash
reciva-dlna-stream --stream-url "https://example.com/radio-stream.mp3"
```

### Multiple streams (config file)

```bash
# See example-config.json for the format
reciva-dlna-stream --config example-config.json
```

Config file format (`example-config.json`):

```json
{
    "streams": [
        {
            "url": "https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3",
            "name": "Deutschlandfunk",
            "mime_type": "audio/mpeg"
        }
    ]
}
```

Each stream is exposed at `/stream/0`, `/stream/1`, etc. in the ContentDirectory.
In single-stream mode, the legacy alias `/stream` also works.

### Options

| Option | Description |
|--------|-------------|
| `--stream-url` | URL of the internet radio stream (required unless `--config` is given) |
| `--name` | Friendly name of the DLNA server (default: "Internet Radio Stream") |
| `--config` | Path to JSON config file listing multiple streams |
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

### `reciva-dlna-stream.sh` — Launch wrapper

Starts the server using the virtual environment without requiring manual
activation. Run it from anywhere inside the repository:

```bash
./reciva-dlna-stream.sh --stream-url "https://example.com/radio.mp3" [options]
```

It auto-detects `.venv` created by `setup.sh` and forwards all arguments to
`reciva-dlna-stream`. If the virtual environment is missing, it prints a clear error
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
