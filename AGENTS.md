# Agent Instructions / Development Guidelines

This file documents how the `dlna-stream` project has been developed in this session, for continuity across future sessions or agent handoffs.

## Development Workflow

### Session Flow
1. **Understand the problem**: Start by reading the existing codebase, project memory, and any user-provided logs
2. **Save key context to memory**: Non-obvious findings, radio behavior, architecture decisions → saved to `memory/project.md`
3. **Implement iteratively**: Make changes, run tests, fix issues
4. **Update specifications**: After each significant change, update the `specification/*.md` files to reflect the current design
5. **Save to memory before session end**: Important project context must not be lost

### Spec-First for Reimplementation
- The `specification/` directory is the source of truth for how the server works
- After any implementation change, the corresponding spec files must be updated
- Specs should contain enough detail to reimplement the server from scratch

### User's Communication Style
- Concise, direct. No pleasantries needed.
- User provides logs from the actual device for debugging.
- The goal is always: make the Reciva radio play the stream reliably.

## Coding Standards

### Language & Runtime
- **Python 3.11+** (uses `|` union types, `str | None` syntax)
- **asyncio** throughout (no synchronous blocking I/O)

### Style Conventions
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `SCREAMING_SNAKE` for module-level constants
- **Imports**: stdlib first, then third-party, then local (separated by blank lines)
- **Type annotations**: Required on all function signatures. Use `from __future__ import annotations` for PEP 604 syntax.
- **Logging**: Use the `_LOGGER` module-level logger. Levels:
  - `INFO`: Client connections/disconnections, major state changes
  - `DEBUG`: Bytes forwarded, SSDP packet details, buffer state
  - `WARNING`: Client disconnects mid-write, buffer timeouts
  - `ERROR`: Unexpected exceptions (with `_LOGGER.exception()`)
- **Docstrings**: Required on all public methods. Brief, descriptive.
- **Constants**: Module-level, with descriptive comments explaining the value.

### Error Handling
- Use `try/except/finally` for connection tracking (ensure tasks are removed from `_active_connections`)
- Catch specific exceptions (`ConnectionResetError`, `ConnectionAbortedError`, `asyncio.CancelledError`)
- Use `_LOGGER.exception()` in `except Exception` blocks
- Don't let exceptions propagate to aiohttp's error handler — catch and log

### Concurrency
- Use `asyncio.Event` for signaling between producer and consumer (buffer → readers)
- Use `asyncio.Lock` for shared state (buffer mutations)
- Track all streaming tasks in a `set[asyncio.Task]` for lifecycle management
- Use `asyncio.sleep(0)` in tight loops to yield control

## Testing Requirements

### Every feature must have a test
- No code is merged without at least one test covering it
- Tests must be integration-level where possible (test the full HTTP → buffer → stream pipeline)

### Test Structure
- Tests live in `tests/test_integration.py`
- Fixtures in `tests/conftest.py`
- Uses `pytest` with `pytest-asyncio` (`asyncio_mode = auto`)

### Test Patterns
- Each test gets a fresh server instance (new port, new device UDN, new buffer)
- Use the same `start_server()` as production code (not a mock)
- The `fake_radio` fixture provides a controlled stream source
- Test HTTP responses check: status code, all relevant headers, body content
- For range tests, verify exact byte matching against known data

### Flaky Tests
- Avoid timing-dependent tests (don't wait for periodic events)
- Use active queries (M-SEARCH) instead of passive waiting (NOTIFY)
- If a test must wait, use short timeouts with retries

## Project Conventions

### Package Structure
```
dlna_stream/
├── __init__.py          # Package marker, re-exports
├── __main__.py          # CLI entry point
├── forwarder.py         # StreamForwarder + StreamBuffer
├── server.py            # UPnP device/service definitions
└── server_lifecycle.py  # Server startup/shutdown

tests/
├── __init__.py
├── conftest.py          # Fixtures
└── test_integration.py  # Tests

specification/
├── architecture.md      # Overall design
├── forwarder.md         # StreamBuffer + StreamForwarder
├── server.md            # UPnP device/services
├── server-lifecycle.md  # Startup/shutdown
├── testing.md           # Test design
└── radio-behavior.md    # Reciva radio notes
```

### External Dependencies
- `async-upnp-client>=0.47.0` — UPnP device framework, SSDP
- `aiohttp>=3.9.0` — HTTP server and client
- No other runtime dependencies

### SSDP
- TTL is monkey-patched from 2 → 4 to comply with UPnP Device Architecture v2.0
- The `async_upnp_client.SsdpAdvertisementAnnouncer` sends NOTIFY every 30s
- `SsdpSearchResponder` responds to M-SEARCH immediately
- The `async_upnp_client.SsdpSearchResponder` option `ssdp_search_responder_always_rootdevice` is set to `True`
