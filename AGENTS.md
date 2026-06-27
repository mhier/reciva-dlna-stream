# Agent Instructions / Development Guidelines

This file documents how the `reciva-dlna-stream` project has been developed in this session, for continuity across future sessions or agent handoffs.

## Development Workflow

### Running Commands
- **Always use the project's virtual environment**: `/home/mhier/compile/dlna-stream/.venv/bin/python`
  - Never install packages globally (no `pip install --break-system-packages` or system pip)
  - To run tests: `.venv/bin/python -m pytest tests/ -v`
  - To run the server: `.venv/bin/python -m reciva_dlna_stream --stream-url ...`
  - The `.venv` already has all dependencies installed (`aiohttp`, `async_upnp_client`, `pytest`)

### Session Flow
1. **Understand the problem**: Start by reading the existing codebase, project memory, and any user-provided logs
2. **Save key context to memory**: Non-obvious findings, radio behavior, architecture decisions → saved to `memory/project.md`
3. **Update requirements if needed**: If the user has changed requirements (edited `requirements/REQ-*.md`), derive corresponding changes in the specification files
4. **Update specifications**: Before each significant change, update the `specification/*.md` files to reflect the new design
5. **Implement iteratively**: Write tests according to updated specifications, make changes to the implementation, run tests, fix issues
6. **Verify requirements coverage**: After implementation, check that the `requirements/REQ-*.md` status markers match actual implementation state
7. **Save to memory before session end**: Important project context must not be lost

### Three-Layer Document Architecture

```
Requirements  ──(LLM derives)──→  Specifications  ──(LLM + tests)──→  Source Code
(what)                              (how)                               (code)
```

#### Layer 1: Requirements (`requirements/REQ-*.md`)
- **What** the server must do, not how.
- Written at a level where a software architect could derive a technical design.
- Each requirement has an ID (e.g. REQ-3.1) and a status marker:

| Marker | Meaning |
|---|---|
| ✅ Implemented | Fully implemented in code |
| 🚧 In Progress | Partially implemented or being worked on |
| 🔄 Changed | Requirement has been edited; specs and code need updating |
| ⬜ Not Started | Not implemented yet |
| ❌ Not applicable | Process requirement, not a feature |

- **Edited by the human** to define or change features.
- The single source of truth for *intended behavior*.

#### Layer 2: Specifications (`specification/*.md`)
- **How** the server works — technical design detail.
- Derived from requirements by an LLM. Sufficient to reimplement from scratch.
- Describe classes, methods, HTTP responses, XML formats, constants, etc.
- The source of truth for *implementation design*.

#### Layer 3: Implementation (`reciva_dlna_stream/`)
- The actual Python code.
- Generated from specifications by a separate LLM instance, refined through tests.

### Requirements-Driven Development Workflow

When the human wants a feature change:

1. **Human edits `requirements/REQ-*.md`** — adds new requirements, changes existing ones, updates status markers to ⬜ Not Started for new work.
2. **LLM (you) derives specification changes** — reads the updated requirements, determines which spec files need changes, updates them to reflect the new design.
3. **Separate LLM instance implements** — reads the updated specs, implements the code, updates requirement status markers to ✅ Implemented.

### Keeping Requirements in Sync

The status markers in `requirements/REQ-*.md` must always reflect reality:

- After implementation: Check each requirement. If fully implemented, mark ✅.
- If a requirement is partially done: mark 🚧 In Progress.
- If not started: mark ⬜ Not Started.

### Commands for Sync

To sync requirement status markers with the current implementation (run after any code change):

1. Read all requirement files and grep the codebase for evidence of each requirement.
2. Update the status in each requirement's table to match.

To derive specification changes from requirements changes:

1. Read the changed requirement files.
2. For each new/changed requirement, determine which spec files are affected.
3. Update the affected spec files with the new design detail.
4. Ensure the spec is still detailed enough to reimplement from.

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
reciva_dlna_stream/
├── __init__.py          # Package marker, re-exports
├── __main__.py          # CLI entry point
├── forwarder.py         # StreamForwarder + StreamBuffer
├── server.py            # UPnP device/service definitions (multi-stream)
├── server_lifecycle.py  # Server startup/shutdown
└── stream_config.py     # JSON config parsing for multi-stream

tests/
├── __init__.py
├── conftest.py          # Fixtures (single + multi-stream)
└── test_integration.py  # Tests (22 tests)

specification/
├── architecture.md      # Overall design
├── forwarder.md         # StreamBuffer + StreamForwarder
├── server.md            # UPnP device/services
├── server-lifecycle.md  # Startup/shutdown
├── testing.md           # Test design
└── radio-behavior.md    # Reciva radio notes

requirements/
├── README.md            # Requirements overview
├── REQ-0-non-functional.md
├── REQ-1-discovery.md
├── REQ-2-content-directory.md
├── REQ-3-stream-serving.md
├── REQ-4-lifecycle.md
└── REQ-5-multi-stream.md
```

### External Dependencies
- `async-upnp-client>=0.47.0` — UPnP device framework, SSDP
- `aiohttp>=3.9.0` — HTTP server and client
- No other runtime dependencies

### SSDP
- The `async_upnp_client.SsdpAdvertisementAnnouncer` sends NOTIFY every 30s
- `SsdpSearchResponder` responds to M-SEARCH immediately
- The `async_upnp_client.SsdpSearchResponder` option `ssdp_search_responder_always_rootdevice` is set to `True`
