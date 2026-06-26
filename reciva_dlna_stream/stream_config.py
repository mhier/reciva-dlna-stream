"""Configuration module for multi-stream setup.

Reads a JSON config file listing streams with URL and friendly name.
Also supports the legacy single-stream mode via ``--stream-url`` + ``--name``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamConfig:
    """Configuration for a single stream."""

    url: str
    name: str
    mime_type: str = "audio/mpeg"


@dataclass(frozen=True)
class ServerConfig:
    """Complete server configuration."""

    streams: Sequence[StreamConfig] = field(default_factory=list)


def load_config(path_str: str) -> ServerConfig:
    """Load a JSON config file and return a ServerConfig.

    Expected JSON format::

        {
            "streams": [
                {"url": "https://...", "name": "My Stream"},
                {"url": "https://...", "name": "Another Stream"}
            ]
        }

    Each entry may optionally include ``mime_type`` (default: ``"audio/mpeg"``).
    """
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Config path is not a file: {path}")

    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    if not isinstance(data, dict):
        raise ValueError("Config must be a JSON object (dict)")

    streams_raw = data.get("streams")
    if not isinstance(streams_raw, list) or len(streams_raw) == 0:
        raise ValueError("Config must contain a non-empty 'streams' list")

    streams: list[StreamConfig] = []
    for i, entry in enumerate(streams_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Stream {i}: expected a JSON object, got {type(entry).__name__}")
        url = entry.get("url")
        name = entry.get("name")
        if not url or not name:
            raise ValueError(f"Stream {i}: missing 'url' or 'name'")
        if not isinstance(url, str):
            raise ValueError(f"Stream {i}: 'url' must be a string")
        if not isinstance(name, str):
            raise ValueError(f"Stream {i}: 'name' must be a string")
        mime_type = entry.get("mime_type", "audio/mpeg")
        if not isinstance(mime_type, str):
            raise ValueError(f"Stream {i}: 'mime_type' must be a string")
        streams.append(StreamConfig(url=url, name=name, mime_type=mime_type))

    _LOGGER.info("Loaded %d stream(s) from config: %s", len(streams), path)
    for s in streams:
        _LOGGER.info("  Stream: %s <- %s", s.name, s.url)

    return ServerConfig(streams=tuple(streams))
