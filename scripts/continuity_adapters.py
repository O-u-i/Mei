"""Read-only host-facing adapters for the shared continuity loader.

The adapters provide stable GUI/TUI entry points while keeping repository
identity, validation, status calculation, and diagnostics in ``load_context``.
They do not install hooks or perform writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

if __package__:  # Support package imports from the repository root.
    from .check_continuity import ContinuityError, load_context
else:  # Support direct imports when ``scripts`` is on ``sys.path``.
    from check_continuity import ContinuityError, load_context


SUPPORTED_HOSTS = frozenset(("gui", "tui"))


def read_context(
    repo_root: str | Path,
    context_id: str,
    *,
    host: str,
    mei_root: str | Path | None = None,
    remote: str | None = None,
) -> dict[str, Any]:
    """Read one shared context for a supported host without mutating state."""
    if not isinstance(host, str) or host not in SUPPORTED_HOSTS:
        supported = ", ".join(sorted(SUPPORTED_HOSTS))
        raise ContinuityError(f"host must be one of: {supported}")
    return load_context(
        repo_root,
        context_id,
        mei_root=mei_root,
        remote=remote,
        host=host,
    )


def read_gui_context(
    repo_root: str | Path,
    context_id: str,
    *,
    mei_root: str | Path | None = None,
    remote: str | None = None,
) -> dict[str, Any]:
    """Read one context for a GUI caller."""
    return read_context(
        repo_root,
        context_id,
        host="gui",
        mei_root=mei_root,
        remote=remote,
    )


def read_tui_context(
    repo_root: str | Path,
    context_id: str,
    *,
    mei_root: str | Path | None = None,
    remote: str | None = None,
) -> dict[str, Any]:
    """Read one context for a TUI caller."""
    return read_context(
        repo_root,
        context_id,
        host="tui",
        mei_root=mei_root,
        remote=remote,
    )


__all__ = ["SUPPORTED_HOSTS", "read_context", "read_gui_context", "read_tui_context"]
