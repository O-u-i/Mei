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
import subprocess
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
STOP_EVENT = "Stop"


def select_context(repo_root: str | Path, *, mei_root: str | Path, remote: str | None = None) -> dict[str, Any]:
    """Pick the one open context for a repository; never create or merge contexts."""
    if __package__:
        from .check_continuity import _read_yaml, _validate, _assert_no_reparse, _assert_private, MAX_INDEX_LINES, repo_id_for
    else:
        from check_continuity import _read_yaml, _validate, _assert_no_reparse, _assert_private, MAX_INDEX_LINES, repo_id_for
    repo_id = repo_id_for(repo_root, remote)
    index_path = Path(mei_root) / "registry" / "continuity.yaml"
    _assert_no_reparse(index_path)
    if not index_path.is_file():
        return {"status": "context_missing", "repo_id": repo_id, "candidates": []}
    index, raw = _read_yaml(index_path)
    _validate(index, "continuity-index.schema.json", index_path)
    _assert_private(index, index_path)
    if len(raw.splitlines()) > MAX_INDEX_LINES:
        raise ContinuityError("continuity index exceeds its line cap")
    keys = [(item["repo_id"], item["context_id"]) for item in index["entries"]]
    if len(keys) != len(set(keys)):
        raise ContinuityError("duplicate repo/context entry")
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
        from memory_markdown import memory_digest, resolve_memory_root

        return memory_digest(resolve_memory_root(mei_root))
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
        from sync_obsidian import sync_obsidian
        from memory_markdown import resolve_memory_root

        result = sync_obsidian(None, f"continuity-{generation}", memory_root=resolve_memory_root(mei_root), config_path=mei_root / "local" / "paths.toml")
    except Exception as exc:
        return {"status": "obsidian_projection_stale", "reason": type(exc).__name__}
    if result.get("status") != "synced":
        return {"status": "obsidian_projection_stale", "reason": result.get("status")}
    return {"status": "synced", "added": result["added"], "updated": result["updated"]}


def _sandboxed_hook(args: argparse.Namespace, envelope: dict[str, Any], host: str, actual_home: Path,
                    context_id: str | None, *, selection: str, profile_name: str, child_flag: str) -> dict[str, Any] | None:
    """A separately selected native writer, not an ambient profile-name guess.

    Hook envelopes do not promise a named permission profile. A configured local
    lifecycle writer therefore executes inside the real native sandbox. The
    executable/profile selection is machine-local and explicitly user-reviewed.
    """
    config = _local_config(args.mei_root)
    profile = config.get("host", {}).get(selection)
    if profile is None or profile is False:
        return None
    if profile != profile_name:
        raise ContinuityError("unsupported lifecycle writer selection")
    executable = Path(config.get("paths", {}).get("codex_cli", ""))
    if not executable.is_absolute() or not executable.is_file():
        raise ContinuityError("native lifecycle sandbox executable is unavailable")
    command = [str(executable), "sandbox", "-P", profile, "-C", str(args.repo_root)]
    selected_config = config.get("host", {}).get(f"codex_config_profile_{host}")
    if selected_config:
        command.extend(["--profile", selected_config])
    command.extend(["--", sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()),
                    "--repo-root", str(args.repo_root), "--mei-root", str(args.mei_root), "--host", host, child_flag])
    if context_id:
        command.extend(["--context-id", context_id])
    metadata = {key: envelope[key] for key in ("hook_event_name", "cwd", "session_id", "trigger") if key in envelope}
    try:
        child = subprocess.run(command, input=json.dumps(metadata), capture_output=True, text=True, encoding="utf-8",
                               timeout=10, env=dict(os.environ, CODEX_HOME=str(actual_home), PYTHONDONTWRITEBYTECODE="1"))
        if child.returncode:
            raise ContinuityError("selected native lifecycle sandbox failed")
        wire = json.loads(child.stdout)
        if not isinstance(wire, dict) or not isinstance(wire.get("continue"), bool):
            raise ContinuityError("native lifecycle response is invalid")
        return wire
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        raise ContinuityError("selected native lifecycle sandbox unavailable") from exc


def _sandboxed_lifecycle(args: argparse.Namespace, envelope: dict[str, Any], host: str, actual_home: Path,
                         context_id: str) -> dict[str, Any] | None:
    return _sandboxed_hook(args, envelope, host, actual_home, context_id,
                           selection="continuity_lifecycle_profile", profile_name="mei-continuity-writer",
                           child_flag="--lifecycle-child")


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
    parser.add_argument("--repo-root", type=Path, help="Default: the validated hook workspace.")
    parser.add_argument("--context-id", help="Explicit context; otherwise the one open context is selected.")
    parser.add_argument("--mei-root", required=True, type=Path)
    parser.add_argument("--host", default="auto", choices=sorted(SUPPORTED_HOSTS | {"auto"}))
    parser.add_argument("--writer-thread-id", help="Optional: restrict lifecycle writes to one native thread.")
    parser.add_argument("--codex-home", type=Path, help="Optional: restrict lifecycle writes to one CODEX_HOME.")
    parser.add_argument("--lifecycle-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--projection-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    actual_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    home_matches = not args.codex_home or actual_home.resolve() == args.codex_home.resolve()
    host = args.host if args.host != "auto" else _host_for_home(args.mei_root, actual_home)
    event = None
    automatic = False
    startup = False
    try:
        raw = sys.stdin.buffer.read(65_537)
        if len(raw) > 65_536:
            raise ContinuityError("hook envelope exceeds its hard cap")
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            raise ContinuityError("hook envelope must be an object")
        event = envelope["hook_event_name"]
        startup = event in READ_EVENTS
        automatic = (event == "PreCompact" and envelope.get("trigger") != "manual") or event == STOP_EVENT
        workspace = envelope.get("cwd")
        if not isinstance(workspace, str) or not Path(workspace).is_absolute() or not Path(workspace).is_dir():
            raise ContinuityError("hook workspace must be an existing absolute directory")
        args.repo_root = args.repo_root or Path(workspace)
        if Path(envelope["cwd"]).resolve() != args.repo_root.resolve():
            raise ContinuityError("hook workspace differs from selected repository")
        if event not in {"SessionStart", "UserPromptSubmit", "PreCompact", "PostCompact", STOP_EVENT}:
            raise ContinuityError("unsupported host event")
        if args.context_id:
            selection = {"status": "selected", "context_id": args.context_id}
        else:
            try:
                selection = select_context(args.repo_root, mei_root=args.mei_root)
            except (ContinuityError, OSError, ValueError, TypeError):
                if not startup:
                    raise
                selection = {"status": "context_invalid", "required_action": "explicit_reconcile"}
        context_id = selection.get("context_id")
        projection_failure = None
        if event == "PostCompact" and not args.projection_child:
            try:
                projected = _sandboxed_hook(args, envelope, host, actual_home, context_id,
                                           selection="obsidian_projection_profile", profile_name="mei-obsidian-projection",
                                           child_flag="--projection-child")
            except ContinuityError:
                projected = None
                projection_failure = {"status": "obsidian_projection_stale", "reason": "native_projection_sandbox_unavailable"}
            if projected is not None:
                print(json.dumps(projected, ensure_ascii=False))
                return 0
        if event in READ_EVENTS:
            context: dict[str, Any] = {"host": host, "codex_home": str(actual_home)}
            context["permission_profile"] = os.environ.get("CODEX_PERMISSION_PROFILE", "unreported")
            context["lifecycle_writer"] = _local_config(args.mei_root).get("host", {}).get("continuity_lifecycle_profile", "not_selected")
            if context_id:
                try:
                    state = read_context(args.repo_root, context_id, host=host, mei_root=args.mei_root)
                    context.update({key: state[key] for key in ("repo_id", "context_id", "status", "project", "project_sha256", "index_sha256", "diagnostics")})
                    context["generation"] = state["checkpointer"]["generation"]
                    context["needs_reconcile"] = state["needs_reconcile"]
                except (ContinuityError, OSError, ValueError):
                    context.update(status="context_invalid", required_action="explicit_reconcile")
            else:
                context.update(selection)
            if _injection_enabled(args.mei_root):
                context["memory"] = _memory_digest(args.mei_root)
            if event == "PostCompact":
                context["obsidian"] = projection_failure or (_refresh_projection(args.mei_root, context.get("generation", "none"))
                                       if args.projection_child else {"status": "obsidian_projection_stale", "reason": "projection_writer_not_selected"})
            context["authority"] = "Transient task context only; current user instructions provide authorization."
            content = json.dumps(context, ensure_ascii=False)
            if event == "SessionStart":
                wire = {"continue": True, "hookSpecificOutput": {"hookEventName": event, "additionalContext": content}}
            else:
                wire = {"continue": True, "systemMessage": "Mei continuity after compact: " + content}
        elif event == STOP_EVENT:
            # The Stop hook only asks the current agent to save missing state. It
            # never reads a transcript, starts a model, or writes a handoff.
            if envelope.get("stop_hook_active") is True:
                wire = {"continue": True, "systemMessage": "Mei continuity Stop check already attempted."}
            elif not context_id:
                wire = {"continue": True, "systemMessage": "Mei continuity Stop check skipped: no selected context."}
            else:
                state = read_context(args.repo_root, context_id, host=host, mei_root=args.mei_root)
                needs_save = state["status"] in {"dirty", "needs_reconcile", "conflict", "stale"}
                wire = {
                    "continue": not needs_save,
                    "systemMessage": "Mei continuity Stop check: " + json.dumps({"status": state["status"], "needs_save": needs_save}, ensure_ascii=False),
                }
                if needs_save:
                    wire["stopReason"] = "Save the current handoff before ending this task; no background model was started."
                    wire["meiStopCheck"] = {"attempted": True, "required_action": "save_handoff"}
        else:
            session = envelope.get("session_id")
            if context_id and not args.lifecycle_child and not (args.writer_thread_id or args.codex_home):
                wire = _sandboxed_lifecycle(args, envelope, host, actual_home, context_id)
                if wire is not None:
                    print(json.dumps(wire, ensure_ascii=False))
                    return 0
            selected_profile = os.environ.get("CODEX_PERMISSION_PROFILE")
            explicit_legacy_writer = bool(args.writer_thread_id or args.codex_home) and selected_profile in {"mei-maintainer", "mei-continuity-writer"}
            is_writer = (args.lifecycle_child or explicit_legacy_writer) and home_matches and isinstance(session, str) and bool(session) and (
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
        print(json.dumps({"continue": automatic or startup or event in {"UserPromptSubmit", STOP_EVENT}, "systemMessage": "Mei continuity unavailable; inspect the selected pair."}))
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
