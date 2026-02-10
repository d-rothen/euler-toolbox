"""TrackedPath type and origin-resolution helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TrackedPath:
    """A filesystem path that remembers where the data originally came from.

    *working* is the fast/local path used for actual I/O (e.g. ``$TMPDIR``).
    *origin* is the canonical path used for logging and metadata.
    """

    working: Path
    origin: Path

    def __fspath__(self) -> str:
        return str(self.working)

    def __str__(self) -> str:
        return str(self.working)


def resolve_origin(
    working: Path,
    *,
    explicit_origin: Path | None = None,
    origin_map: dict[str, str] | None = None,
) -> Path:
    """Determine the origin path for *working*.

    Priority:
    1. *explicit_origin* (from ``--<param>-origin``)
    2. First matching prefix in *origin_map* (from ``--origin-map``)
    3. Fallback: *working* itself
    """
    if explicit_origin is not None:
        return explicit_origin
    if origin_map:
        working_str = str(working)
        for local_prefix, real_prefix in origin_map.items():
            if working_str.startswith(local_prefix):
                return Path(real_prefix + working_str[len(local_prefix) :])
    return working


def parse_origin_map(raw: str) -> dict[str, str]:
    """Parse ``"local=real,local2=real2"`` into a dict.  Environment
    variables are expanded on both sides."""
    pairs: dict[str, str] = {}
    for segment in raw.split(","):
        local, _, remote = segment.partition("=")
        if not remote:
            continue
        pairs[os.path.expandvars(local.strip())] = os.path.expandvars(
            remote.strip()
        )
    return pairs


def render_placeholder(placeholder: str, style: str = "mustache") -> str:
    """Wrap a raw placeholder name according to *style*.

    Styles:
    - ``mustache``: ``{{dataset_path:1}}``
    - ``shell``:    ``${dataset_path:1}``
    - ``plain``:    ``dataset_path:1``
    """
    if style == "mustache":
        return "{{" + placeholder + "}}"
    if style == "shell":
        return "${" + placeholder + "}"
    return placeholder
