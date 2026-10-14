"""Host-facing read adapters and a narrow native continuity hook command.

The functions provide stable read-only GUI/TUI entry points while keeping repository
identity, validation, status calculation, and diagnostics in ``load_context``.
The opt-in command handles native lifecycle metadata for one selected writer;
it neither installs hooks nor performs semantic sync.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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


def main(argv: list[str] | None = None) -> int:
    """Native hook command; consume metadata, never open a transcript locator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--mei-root", required=True, type=Path)
    parser.add_argument("--host", required=True, choices=sorted(SUPPORTED_HOSTS))
    parser.add_argument("--writer-thread-id")
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args(argv)
    actual_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    if args.codex_home and actual_home.resolve() != args.codex_home.resolve():
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0  # A repository hook must not opt the GUI/default home into this pilot.
    event = None
    automatic = False
    try:
        raw = sys.stdin.buffer.read(65_537)
        if len(raw) > 65_536:
            raise ContinuityError("hook envelope exceeds its hard cap")
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            raise ContinuityError("hook envelope must be an object")
        event = envelope["hook_event_name"]
        automatic = event == "PreCompact" and envelope.get("trigger") != "manual"
        if Path(envelope["cwd"]).resolve() != args.repo_root.resolve():
            raise ContinuityError("hook workspace differs from selected repository")
        if event not in {"SessionStart", "UserPromptSubmit", "PreCompact", "PostCompact"}:
            raise ContinuityError("unsupported host event")
        if event in {"SessionStart", "PostCompact"}:
            state = read_context(args.repo_root, args.context_id, host=args.host, mei_root=args.mei_root)
            context = {key: state[key] for key in ("repo_id", "context_id", "status", "project", "project_sha256", "index_sha256", "diagnostics")}
            context["generation"] = state["checkpointer"]["generation"]
            context["needs_reconcile"] = state["needs_reconcile"]
            context["authority"] = "Transient task context only; current user instructions provide authorization."
            content = json.dumps(context, ensure_ascii=False)
            if event == "SessionStart":
                wire = {"continue": True, "hookSpecificOutput": {"hookEventName": event, "additionalContext": content}}
            else:
                wire = {"continue": True, "systemMessage": "Mei continuity after compact: " + content}
        else:
            session = envelope.get("session_id")
            if not args.writer_thread_id or session != args.writer_thread_id:
                if event == "UserPromptSubmit":
                    print(json.dumps({"continue": True, "suppressOutput": True}))
                    return 0  # A readback witness must not change bookkeeping.
                raise ContinuityError("lifecycle writer differs from the selected native thread")
            if __package__:
                # Existing writer modules use direct-script imports.
                sys.path.insert(0, str(Path(__file__).resolve().parent))
            from sync_continuity import handle_lifecycle_event

            lifecycle = "user_prompt_submit" if event == "UserPromptSubmit" else {
                "manual": "precompact_manual", "auto": "precompact_auto",
            }.get(envelope.get("trigger"), "precompact_unknown")
            result = handle_lifecycle_event(
                args.repo_root, args.context_id, lifecycle, mei_root=args.mei_root,
                writer_session_id=f"{args.host}-{session}",
            )
            wire = {"continue": result["allow"], "systemMessage": "Mei continuity: " + json.dumps(result, ensure_ascii=False)}
            if not result["allow"]:
                wire["stopReason"] = "Explicitly sync the selected Mei context before manual compact."
    except (ContinuityError, OSError, ValueError, KeyError, TypeError):
        # Do not echo unvalidated input or record contents into host diagnostics.
        print(json.dumps({"continue": automatic, "systemMessage": "Mei continuity unavailable; inspect the selected pair."}))
        # Keep the response parseable by the native host. Automatic failures
        # remain fail-open; manual failures stop through continue:false.
        return 0
    print(json.dumps(wire, ensure_ascii=False))
    # A valid hook response is controlled by its JSON envelope. In particular,
    # PreCompact uses continue:false to stop compaction; a non-zero exit causes
    # the native host to discard that control response and report hook failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
