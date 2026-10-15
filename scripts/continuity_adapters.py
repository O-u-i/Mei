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
READ_EVENTS = frozenset(("SessionStart", "PostCompact"))
WRITE_EVENTS = frozenset(("UserPromptSubmit", "PreCompact"))


def select_context(repo_root: str | Path, *, mei_root: str | Path, remote: str | None = None) -> dict[str, Any]:
    """Pick the one open context for a repository; never create or merge contexts."""
    if __package__:
        from .check_continuity import _read_yaml, repo_id_for
    else:
        from check_continuity import _read_yaml, repo_id_for
    repo_id = repo_id_for(repo_root, remote)
    index_path = Path(mei_root) / "registry" / "continuity.yaml"
    if not index_path.is_file():
        return {"status": "context_missing", "repo_id": repo_id, "candidates": []}
    index, _ = _read_yaml(index_path)
    candidates = []
    for entry in index.get("entries", []):
        if entry.get("repo_id") != repo_id or entry.get("status") == "completed":
            continue
        try:
            state = load_context(repo_root, entry["context_id"], mei_root=mei_root, remote=remote)
        except ContinuityError:
            candidates.append(entry["context_id"])  # An unreadable context still needs an explicit choice.
            continue
        if state["project"]["status"] != "completed":
            candidates.append(entry["context_id"])
    if not candidates:
        return {"status": "context_missing", "repo_id": repo_id, "candidates": [], "required_action": "create_or_select"}
    if len(candidates) > 1:
        return {"status": "context_ambiguous", "repo_id": repo_id, "candidates": sorted(candidates), "required_action": "select"}
    return {"status": "selected", "repo_id": repo_id, "context_id": candidates[0]}


def _memory_digest(mei_root: Path) -> dict[str, Any]:
    try:
        if __package__:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        from memory_markdown import memory_digest

        return memory_digest(mei_root / "memory" / "markdown")
    except Exception as exc:  # Startup memory is advisory and must fail open.
        return {"status": "unavailable", "error": type(exc).__name__}


def _local_config(mei_root: Path) -> dict[str, Any]:
    import tomllib

    try:
        with (Path(mei_root) / "local" / "paths.toml").open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, ValueError):
        return {}


def _host_for_home(mei_root: Path, actual_home: Path) -> str:
    """Label the surface from the configured homes; the label never grants writes."""
    paths = _local_config(mei_root).get("paths", {})
    tui_home = paths.get("codex_home_tui")
    try:
        if tui_home and Path(tui_home).resolve() == actual_home.resolve():
            return "tui"
    except OSError:
        pass
    return "gui"


def _injection_enabled(mei_root: Path) -> bool:
    """Rollback switch: [host] memory_injection = false disables MEMORY.md injection."""
    value = _local_config(mei_root).get("host", {}).get("memory_injection", True)
    return value is not False


def _refresh_projection(mei_root: Path, generation: Any) -> dict[str, Any]:
    """Refresh the Obsidian view after compact; staleness never blocks continuity."""
    try:
        if __package__:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        from sync_obsidian import resolve_vault, sync_obsidian

        vault, _, _ = resolve_vault(config_path=mei_root / "local" / "paths.toml")
        result = sync_obsidian(vault, f"continuity-{generation}", memory_root=mei_root / "memory" / "markdown")
    except Exception as exc:
        return {"status": "obsidian_projection_stale", "reason": type(exc).__name__}
    if result.get("status") != "synced":
        return {"status": "obsidian_projection_stale", "reason": result.get("status")}
    return {"status": "synced", "added": result["added"], "updated": result["updated"]}


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
    parser.add_argument("--context-id", help="Explicit context; otherwise the one open context is selected.")
    parser.add_argument("--mei-root", required=True, type=Path)
    parser.add_argument("--host", default="auto", choices=sorted(SUPPORTED_HOSTS | {"auto"}))
    parser.add_argument("--writer-thread-id", help="Optional: restrict lifecycle writes to one native thread.")
    parser.add_argument("--codex-home", type=Path, help="Optional: restrict lifecycle writes to one CODEX_HOME.")
    args = parser.parse_args(argv)
    actual_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    home_matches = not args.codex_home or actual_home.resolve() == args.codex_home.resolve()
    host = args.host if args.host != "auto" else _host_for_home(args.mei_root, actual_home)
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
        if args.context_id:
            selection = {"status": "selected", "context_id": args.context_id}
        else:
            selection = select_context(args.repo_root, mei_root=args.mei_root)
        context_id = selection.get("context_id")
        if event in READ_EVENTS:
            context: dict[str, Any] = {"host": host, "codex_home": str(actual_home)}
            if context_id:
                state = read_context(args.repo_root, context_id, host=host, mei_root=args.mei_root)
                context.update({key: state[key] for key in ("repo_id", "context_id", "status", "project", "project_sha256", "index_sha256", "diagnostics")})
                context["generation"] = state["checkpointer"]["generation"]
                context["needs_reconcile"] = state["needs_reconcile"]
            else:
                context.update(selection)
            if _injection_enabled(args.mei_root):
                context["memory"] = _memory_digest(args.mei_root)
            if event == "PostCompact":
                context["obsidian"] = _refresh_projection(args.mei_root, context.get("generation", "none"))
            context["authority"] = "Transient task context only; current user instructions provide authorization."
            content = json.dumps(context, ensure_ascii=False)
            if event == "SessionStart":
                wire = {"continue": True, "hookSpecificOutput": {"hookEventName": event, "additionalContext": content}}
            else:
                wire = {"continue": True, "systemMessage": "Mei continuity after compact: " + content}
        else:
            session = envelope.get("session_id")
            is_writer = home_matches and isinstance(session, str) and bool(session) and (
                not args.writer_thread_id or session == args.writer_thread_id
            )
            if not context_id:
                # No selected context: nothing to gate or record. Never create one from a hook.
                print(json.dumps({"continue": True, "suppressOutput": True}))
                return 0
            if not is_writer:
                if event == "UserPromptSubmit":
                    print(json.dumps({"continue": True, "suppressOutput": True}))
                    return 0  # A readback witness must not change bookkeeping.
                state = read_context(args.repo_root, context_id, host=host, mei_root=args.mei_root)
                allow = automatic or state["status"] in {"clean", "completed", "retired"}
                wire = {"continue": allow, "systemMessage": "Mei continuity (read-only witness): " + json.dumps({"status": state["status"], "allow": allow})}
                if not allow:
                    wire["stopReason"] = "Explicitly sync the selected Mei context before manual compact."
                print(json.dumps(wire, ensure_ascii=False))
                return 0
            if __package__:
                # Existing writer modules use direct-script imports.
                sys.path.insert(0, str(Path(__file__).resolve().parent))
            from sync_continuity import handle_lifecycle_event

            lifecycle = "user_prompt_submit" if event == "UserPromptSubmit" else {
                "manual": "precompact_manual", "auto": "precompact_auto",
            }.get(envelope.get("trigger"), "precompact_unknown")
            result = handle_lifecycle_event(
                args.repo_root, context_id, lifecycle, mei_root=args.mei_root,
                writer_session_id=f"{host}-{session}"[:128],
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
