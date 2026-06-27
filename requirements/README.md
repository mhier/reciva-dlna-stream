# reciva-dlna-stream — Requirements Overview

This directory contains the **requirements documents** for the `reciva-dlna-stream` project. These describe *what* the server must do, at a level of detail that is sufficient to derive the specification documents from scratch.

## Relationship to Specifications

```
Requirements  ──(LLM derives)──→  Specifications  ──(LLM implements)──→  Source Code
(what)                                (how)                               (code)
```

- **Requirements** (`requirements/REQ-*.md`): High-level behavior. Edited by the human to define or change features.
- **Specifications** (`specification/*.md`): Technical design detail. Derived from requirements by an LLM. Sufficient to reimplement.
- **Implementation** (`reciva_dlna_stream/`): Python code. Generated from specifications by a separate LLM instance.

## Requirements Documents

| Document | Covers | # Requirements |
|---|---|---|
| [REQ-0: Non-Functional](REQ-0-non-functional.md) | Platform, dependencies, testing, error handling, logging, concurrency | 7 |
| [REQ-1: Discovery & Presence](REQ-1-discovery.md) | SSDP, M-SEARCH, NOTIFY, device.xml, LOCATION URL, TTL | 6 |
| [REQ-2: Content Directory](REQ-2-content-directory.md) | ContentDirectory service, Browse, Search, ConnectionManager | 7 |
| [REQ-3: Stream Serving](REQ-3-stream-serving.md) | Fake Content-Length, ring buffer, range requests, synthetic footer, data consistency | 9 |
| [REQ-4: Server Lifecycle & Configuration](REQ-4-lifecycle.md) | Startup order, CLI, port assignment, shutdown, single-stream, IP detection | 6 |
| [REQ-5: Multi-Stream Support](REQ-5-multi-stream.md) | JSON config, indexed routes, independent buffers | 6 |
| [REQ-6: Systemd Service Deployment](REQ-6-deployment.md) | Systemd unit, EnvironmentFile, restart policy, journald, install script, auto-start | 10 |

**Total: 51 requirements**

## Status Legend

Each requirement table uses these status markers:

| Marker | Meaning |
|---|---|
| ✅ Implemented | The requirement is fully implemented in the current source code |
| 🚧 In Progress | The requirement is partially implemented or being worked on |
| 🔄 Changed | The requirement has been edited and needs specification & code updates |
| ⬜ Not Started | The requirement has not been implemented yet |
| ❌ Not applicable | Process requirement, not a feature |
