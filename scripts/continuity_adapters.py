"""Host-facing read adapters and a narrow native continuity hook command.

The functions provide stable read-only GUI/TUI entry points while keeping repository
identity, validation, status calculation, and diagnostics in ``load_context``.
The opt-in command handles native lifecycle metadata for one selected writer;
it neither installs hooks nor performs semantic sync.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:  # Support package imports from the repository root.
    from .check_continuity import ContinuityError, _assert_no_reparse, _safe_relative_path, load_context
else:  # Support direct imports when ``scripts`` is on ``sys.path``.
    from check_continuity import ContinuityError, _assert_no_reparse, _safe_relative_path, load_context


SUPPORTED_HOSTS = frozenset(("gui1", "gui2", "gui", "tui"))
READ_EVENTS = frozenset(("SessionStart", "PostCompact"))
WRITE_EVENTS = frozenset(("UserPromptSubmit", "PreCompact"))
STOP_EVENT = "Stop"


def _attachment(mei_root: Path, repo_root: Path) -> dict[str, Any] | None:
    """Read explicit local enrollment, never infer a writer from a GUI label."""
    if __package__:
        from .check_continuity import repo_id_for, _read_json
    else:
        from check_continuity import repo_id_for, _read_json
    directory = mei_root / "local/attachments"
    _assert_no_reparse(directory)
    if not directory.is_dir():
        return None
    repo_id = repo_id_for(repo_root)
    path = _safe_relative_path(mei_root, f"local/attachments/{repo_id}.json", "attachment")
    if not path.exists():
        return None
    value, _ = _read_json(path, 8192)
    if value.get("repo_id") != repo_id or Path(value.get("repo_root", "")).resolve() != repo_root.resolve():
        raise ContinuityError("attachment repository mismatch")
    writer = value.get("writer")
    # Read old attachments without mutating evidence; new ones store no profile.
    if (value.get("permission_profile") not in (None, "mei-context-" + repo_id[5:])
            or not isinstance(value.get("context_id"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", value["context_id"])
            or (writer is not None and (not isinstance(writer, dict) or set(writer) != {"host", "session_id"}
                or writer["host"] not in {"gui1", "gui2"} or not isinstance(writer["session_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", writer["session_id"])))):
        raise ContinuityError("invalid attachment scope")
    return value


def _native_sandbox_command(executable: Path, repo: Path, write_root: Path) -> list[str]:
    """One invocation-local worker grant, never a host-global menu entry."""
    _assert_no_reparse(write_root)
    grant = ('permissions.mei-worker={extends=":read-only",filesystem={'
             + json.dumps(str(write_root.resolve())) + '="write"},network={enabled=false}}')
    return [str(executable), "sandbox", "-P", "mei-worker", "-c", grant, "-C", str(repo)]


def _event_receipt(args, envelope, host, *, wire=None, error=None):
    """Five latest metadata receipts per repo/host, no prompts or transcript text.

    These record adapter observations, NOT host authenticity or effective grants.
    No receipt means unknown, not proof that the host never fired an event.
    """
    if args.lifecycle_child:
        return "disabled"
    if (_local_config(args.mei_root).get("host", {}).get("event_diagnostics") is not True
            and not (args.mei_root / "local/host-setup.json").is_file()):
        return "disabled"
    try:
        marker = args.mei_root / "local/host-setup.json"
        if marker.is_file() and not args.diagnostic_child:
            config = _local_config(args.mei_root)
            executable = Path(config.get("paths", {}).get("codex_cli", ""))
            if not executable.is_absolute() or not executable.is_file():
                return "native_sandbox_unavailable"
            command = _native_sandbox_command(executable, args.repo_root, args.mei_root / "local/hook-events")
            layered = config.get("host", {}).get(f"codex_config_profile_{host}")
            if layered:
                command.extend(["--profile", layered])
            command.extend(["--", sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()),
                            "--mei-root", str(args.mei_root), "--repo-root", str(args.repo_root),
                            "--host", host, "--diagnostic-child"])
            # Only allowlisted technical metadata crosses this boundary.
            payload = {key: envelope[key] for key in ("hook_event_name", "trigger", "session_id", "stop_hook_active") if key in envelope}
            source = envelope.get("source")
            if isinstance(source, str) and source in {"startup", "resume", "clear", "compact", "fork"}:
                payload["source"] = source
            payload["finished"] = wire is not None
            payload["allow"] = wire.get("continue", True) if wire else True
            payload["save_requested"] = wire.get("decision") == "block" if wire else False
            payload["outcome"] = ("adapter_failure" if error else "skipped" if wire and wire.get("suppressOutput") else
                                  "not_attached" if wire and "no selected context" in wire.get("systemMessage", "") else "adapter_returned")
            if isinstance(error, _LifecycleFailure):
                payload["failure_stage"] = error.diagnostic["stage"]
                if type(error.diagnostic.get("exit_code")) is int:
                    payload["exit_code"] = error.diagnostic["exit_code"]
            if args.context_id:
                payload["context_id"] = args.context_id
            child = subprocess.run(command, input=json.dumps(payload), capture_output=True, encoding="utf-8", timeout=2)
            if child.returncode:
                return f"sandbox_exit_{child.returncode}"
            if len(child.stdout) > 8192:
                return "invalid_response"
            result = json.loads(child.stdout).get("diagnostics")
            return result if isinstance(result, str) and len(result) <= 100 else "invalid_response"
        if __package__:
            from .check_continuity import repo_id_for
            from .checkpoint_continuity import _context_lock, _replace_record
        else:
            from check_continuity import repo_id_for
            from checkpoint_continuity import _context_lock, _replace_record
        event = envelope.get("hook_event_name")
        if event not in READ_EVENTS | WRITE_EVENTS | {STOP_EVENT}:
            return "unsupported_event"
        repo_id = repo_id_for(args.repo_root)
        directory = _safe_relative_path(args.mei_root, f"local/hook-events/{repo_id}/{host}", "event receipt")
        directory.mkdir(parents=True, exist_ok=True)
        finished = wire is not None or envelope.get("finished") is True
        value = {"event": event, "host": host, "host_source": "configured_home_or_explicit_argument",
                 "repo_id": repo_id, "time": datetime.now(timezone.utc).isoformat(),
                 "stage": "finished" if finished else "started", "native_event_verified": False,
                 "trigger": envelope.get("trigger") if envelope.get("trigger") in {"auto", "manual"} else "unspecified",
                 "session_sha256": hashlib.sha256(str(envelope.get("session_id", "")).encode()).hexdigest(),
                 "permission_source": "hook_process_environment", "effective_permissions_verified": False}
        if event == "SessionStart":
            value["source"] = (envelope.get("source") if isinstance(envelope.get("source"), str) and envelope.get("source") in
                               {"startup", "resume", "clear", "compact", "fork"} else "unspecified")
        if event == STOP_EVENT:
            value["stop_hook_active"] = envelope.get("stop_hook_active") is True
        if args.diagnostic_child and finished:
            value.update(allow=envelope.get("allow") is True, save_requested=envelope.get("save_requested") is True,
                         outcome=envelope.get("outcome") if envelope.get("outcome") in
                         {"adapter_failure", "skipped", "not_attached", "adapter_returned"} else "unknown")
        elif wire is not None:
            value.update(allow=wire.get("continue", True), save_requested=wire.get("decision") == "block",
                         outcome="adapter_failure" if error else "adapter_returned")
            message = wire.get("systemMessage", "")
            if "not_attached" in message or "no selected context" in message:
                value["outcome"] = "not_attached"
            elif wire.get("suppressOutput"):
                value["outcome"] = "skipped"
            if error:
                value["error_type"] = type(error).__name__
        if args.context_id and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", args.context_id):
            value["context_id"] = args.context_id
        elif isinstance(envelope.get("context_id"), str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", envelope["context_id"]):
            value["context_id"] = envelope["context_id"]
        failure_stage = error.diagnostic.get("stage") if isinstance(error, _LifecycleFailure) else envelope.get("failure_stage")
        if failure_stage in {"timeout", "response", "launch", "exit"}:
            value["failure_stage"] = failure_stage
        exit_code = error.diagnostic.get("exit_code") if isinstance(error, _LifecycleFailure) else envelope.get("exit_code")
        if type(exit_code) is int:
            value["exit_code"] = exit_code
        with _context_lock(directory, 0.2):
            path = _safe_relative_path(directory, f"{event}.json", "event receipt")
            _replace_record(path, (json.dumps(value) + "\n").encode())
        return "recorded"
    except (ContinuityError, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        return "unavailable:" + type(exc).__name__


def _emit(args, envelope, host, wire, *, error=None):
    status = _event_receipt(args, envelope, host, wire=wire, error=error)
    if status not in {"disabled", "recorded"}:
        # Do not pretend missing write permission produced a durable diagnostic.
        output = wire.get("hookSpecificOutput", {})
        if "additionalContext" in output:
            context = json.loads(output["additionalContext"])
            context["event_diagnostics"] = status
            output["additionalContext"] = json.dumps(context, ensure_ascii=False)
        elif wire.get("systemMessage", "").startswith("Mei continuity after compact: "):
            context = json.loads(wire["systemMessage"].split(": ", 1)[1])
            context["event_diagnostics"] = status
            wire["systemMessage"] = "Mei continuity after compact: " + json.dumps(context, ensure_ascii=False)
        else:
            wire["systemMessage"] = wire.get("systemMessage", "") + f" Mei diagnostics: {status}."
    print(json.dumps(wire, ensure_ascii=False))


class _LifecycleFailure(ContinuityError):
    """Only allowlisted metadata may cross the subprocess diagnostic boundary."""

    def __init__(self, stage: str, *, exit_code: int | None = None, exc: Exception | None = None):
        super().__init__("selected native lifecycle sandbox unavailable")
        self.diagnostic: dict[str, Any] = {"stage": stage}
        if exit_code is not None:
            self.diagnostic["exit_code"] = exit_code
        if exc is not None:
            self.diagnostic["exception_type"] = type(exc).__name__
            for key in ("errno", "winerror"):
                value = getattr(exc, key, None)
                if type(value) is int:
                    self.diagnostic[key] = value


def select_context(repo_root: str | Path, *, mei_root: str | Path, remote: str | None = None) -> dict[str, Any]:
    """Pick the one open context for a repository; never create or merge contexts."""
    if __package__:
        from .check_continuity import _read_yaml, _validate, _assert_no_reparse, _assert_private, MAX_INDEX_LINES, repo_id_for
    else:
        from check_continuity import _read_yaml, _validate, _assert_no_reparse, _assert_private, MAX_INDEX_LINES, repo_id_for
    repo_id = repo_id_for(repo_root, remote)
    attached = _attachment(Path(mei_root), Path(repo_root))
    if attached:
        selected = attached["context_id"]
        state = load_context(repo_root, selected, mei_root=mei_root, remote=remote)
        if state["project"]["status"] == "completed":
            return {"status": "context_completed", "repo_id": repo_id, "context_id": selected}
        return {"status": "selected", "repo_id": repo_id, "context_id": selected, "selection": "explicit_attachment"}
    selected = _local_config(Path(mei_root)).get("continuity", {}).get("selected_contexts", {}).get(repo_id)
    if selected is not None:
        # A local explicit repo/context binding is not a guess from a writable path.
        state = load_context(repo_root, selected, mei_root=mei_root, remote=remote)
        if state["project"]["status"] == "completed":
            return {"status": "context_completed", "repo_id": repo_id, "context_id": selected,
                    "required_action": "select_active_context"}
        return {"status": "selected", "repo_id": repo_id, "context_id": selected, "selection": "explicit_local_binding"}
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
    for host in ("gui2", "gui1"):
        home = paths.get(f"codex_home_{host}")
        try:
            if home and Path(home).resolve() == actual_home.resolve():
                return host
        except OSError:
            pass
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


def _direct_vault(mei_root: Path) -> bool:
    paths = _local_config(mei_root).get("paths", {})
    vault = paths.get("obsidian_vault")
    memory = paths.get("memory_root", mei_root / "memory/markdown")
    return not vault or Path(vault).expanduser().resolve() == Path(memory).expanduser().resolve()


def _refresh_projection(mei_root: Path, generation: Any) -> dict[str, Any]:
    """Refresh the Obsidian view after compact; staleness never blocks continuity."""
    try:
        if __package__:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        from sync_obsidian import sync_obsidian
        from memory_markdown import resolve_memory_root

        paths = _local_config(mei_root).get("paths", {})
        direct = _direct_vault(mei_root)
        result = sync_obsidian(None if direct else paths.get("obsidian_vault"), f"continuity-{generation}",
                               memory_root=resolve_memory_root(mei_root), namespace=None if direct else paths.get("obsidian_memory_dir"),
                               config_path=mei_root / "local" / "paths.toml")
    except Exception as exc:
        return {"status": "obsidian_projection_stale", "reason": type(exc).__name__}
    if result.get("status") != "synced":
        return {"status": "obsidian_projection_stale", "reason": result.get("status")}
    return {"status": "synced", "direct_root": result.get("direct_root", False),
            "added": result["added"], "updated": result["updated"]}


def _continuity_check(state, *, event, checkpoint_allowed, source=None, selection_status=None):
    """Read-time evidence and same-agent instructions, never a recovery receipt.

    ponytail: a command hook cannot author a semantic checkpoint before mid-turn
    compact; use milestone saves, not another model or a transcript reader.
    """
    check = {"source_event": event, "source": source if isinstance(source, str) and source in
             {"startup", "resume", "clear", "compact", "fork"} else "unspecified",
             "status": "blocked", "handoff_integrity": False,
             "recovery_verified": False, "latest_progress_verified": False,
             "checkpoint_allowed": False}
    if state is None and selection_status == "context_missing":
        check.update(status="not_attached", instructions=[
            "No Mei handoff is selected; do not claim continuity recovery. Continue the current user task within "
            "existing permissions without requiring Mei enrollment. Do not create or select a context automatically."])
        return check
    instructions = [
        "Do not wait for a user verification prompt. Before the next task action, perform this recovery check yourself; "
        "do not start a task merely because an old handoff was injected. Current user instructions take precedence.",
    ]
    if state is None:
        instructions.append(
            "Do not resume task mutations: the selected context is missing, ambiguous or invalid. "
            "Inspect only the authorized project's continuity configuration read-only; report an unresolved blocker once. "
            "Do not guess a context, create one, take over a writer or change permissions.")
    else:
        cp = state["checkpointer"]
        integrity = (cp["project_sha256"] == state["project_sha256"]
                     and cp["index_sha256"] == state["index_sha256"]
                     and cp["index_bytes"] == state["index_bytes"]
                     and cp["index_lines"] == state["index_lines"])
        readable = integrity and state["status"] in {"clean", "dirty", "needs_reconcile"}
        check.update(
            status="pending_agent_readback" if readable else "blocked", handoff_integrity=integrity,
            checkpoint_allowed=checkpoint_allowed and state["status"] in {"clean", "dirty"} and integrity,
            expected={key: state[key] for key in ("repo_id", "context_id", "project_sha256", "index_sha256")},
        )
        check["expected"].update(generation=cp["generation"], synced_generation=cp["synced_generation"],
                                 dirty=cp["dirty"], needs_reconcile=state["needs_reconcile"])
        instructions.extend([
            "First identify repo/context, saved hash, synced_generation, goal, constraints and next step from THIS "
            "injection, not from a generic compact summary. Injection is not proof of successful semantic recovery.",
            "Read a fresh snapshot through the installed mei-continuity entrypoint check for this exact repo/context. "
            "Compare the expected repo/context, project/index hashes and synced_generation. Recheck generation and status; "
            "lifecycle metadata may advance generation without a new semantic save. Drift requires read-only investigation, "
            "not overwriting newer state. Do not call recovery verified before this comparison.",
            "Check current user intent and the authorized project's actual files/diff, recorded test results and evidence "
            "of any interrupted operation before resuming mutations. Do not blindly replay deployments, sends or other "
            "external effects. A matching hash or clean status does not prove coverage of unsaved reasoning. "
            "When the readback and task evidence agree, continue the authorized task without asking for routine acceptance. "
            "Report only an unresolved gap or required authorization once.",
            "Do not automatically reconcile, clear debt, save, create or retire a context, promote memory, change "
            "permissions or take over a writer as part of this check. needs_reconcile after automatic compact is not "
            "itself recovery failure, but remains explicit recovery debt. Latest-progress coverage stays unverified "
            "unless the task evidence actually establishes it.",
        ])
        if not readable:
            instructions.append(
                "Do not resume task mutations: handoff integrity, conflict, stale or terminal state needs explicit review.")
        if check["checkpoint_allowed"]:
            instructions.append(
                "After a meaningful task milestone and before the next long phase, recheck current writer ownership "
                "and save changed bounded task fields through the installed mei-continuity save entrypoint with a fresh "
                "snapshot. Respect no-write requests. Read back handoff_saved or handoff_noop, hashes, generation, "
                "synced_generation, dirty and needs_reconcile; do not wait only for Stop. A denied save is not success "
                "and must not fall back to ambient writes. Mid-turn compact can still precede the next checkpoint.")
        else:
            instructions.append(
                "Do not save or take over: this reader has no matching enrolled checkpoint grant or the context "
                "requires explicit recovery. Read-only verification is allowed; checkpointing needs separately valid "
                "writer ownership and state, never a Full Access assumption.")
    check["instructions"] = instructions
    return check


def _sandboxed_lifecycle(args: argparse.Namespace, envelope: dict[str, Any], host: str, actual_home: Path,
                         context_id: str) -> dict[str, Any] | None:
    """Explicit writer selection, invocation-local native filesystem grant."""
    config = _local_config(args.mei_root)
    profile = config.get("host", {}).get("continuity_lifecycle_profile")
    attached = _attachment(args.mei_root, args.repo_root)
    if attached:
        writer = attached.get("writer")
        if not writer or writer != {"host": host, "session_id": envelope.get("session_id")}:
            return None
        profile = "mei-worker"
    if profile is None or profile is False:
        return None
    if profile != ("mei-worker" if attached else "mei-continuity-writer"):
        raise ContinuityError("unsupported lifecycle writer selection")
    executable = Path(config.get("paths", {}).get("codex_cli", ""))
    if not executable.is_absolute() or not executable.is_file():
        raise ContinuityError("native lifecycle sandbox executable is unavailable")
    if __package__:
        from .check_continuity import repo_id_for
    else:
        from check_continuity import repo_id_for
    if attached and context_id != attached["context_id"]:
        raise ContinuityError("lifecycle context differs from attachment")
    if attached:
        directory = _safe_relative_path(args.mei_root,
            f"memory/continuity/{repo_id_for(args.repo_root)}/{context_id}", "context")
        command = _native_sandbox_command(executable, args.repo_root, directory)
    else:
        # Legacy opt-in keeps its reviewed grant; never expand it from a label.
        command = [str(executable), "sandbox", "-P", profile, "-C", str(args.repo_root)]
    selected_config = config.get("host", {}).get(f"codex_config_profile_{host}")
    if selected_config:
        command.extend(["--profile", selected_config])
    command.extend(["--", sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()),
                    "--repo-root", str(args.repo_root), "--mei-root", str(args.mei_root), "--host", host, "--lifecycle-child"])
    if context_id:
        command.extend(["--context-id", context_id])
    metadata = {key: envelope[key] for key in ("hook_event_name", "cwd", "session_id", "trigger") if key in envelope}
    try:
        child = subprocess.run(command, input=json.dumps(metadata), capture_output=True, text=True, encoding="utf-8",
                               timeout=10, env=dict(os.environ, CODEX_HOME=str(actual_home), PYTHONDONTWRITEBYTECODE="1"))
    except subprocess.TimeoutExpired as exc:
        raise _LifecycleFailure("timeout", exc=exc) from None
    except UnicodeError as exc:
        raise _LifecycleFailure("response", exc=exc) from None
    except (OSError, ValueError) as exc:
        raise _LifecycleFailure("launch", exc=exc) from None
    if child.returncode:
        raise _LifecycleFailure("exit", exit_code=child.returncode)
    try:
        wire = json.loads(child.stdout)
    except (ValueError, TypeError) as exc:
        raise _LifecycleFailure("response", exc=exc) from None
    if not isinstance(wire, dict) or not isinstance(wire.get("continue"), bool):
        raise _LifecycleFailure("response")
    return wire


def handoff_status(repo_root: Path, state: dict[str, Any], *, mei_root: Path) -> dict[str, Any]:
    """Read-only workflow hints, not authorization or proof of task coverage."""
    attached = _attachment(mei_root, repo_root)
    selected = bool(attached and attached["context_id"] == state["context_id"])
    writer = attached.get("writer") if selected else None
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    host = _host_for_home(mei_root, home)
    session = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    identity_known = host in {"gui1", "gui2"} and isinstance(session, str) and bool(
        re.fullmatch(r"[A-Za-z0-9._-]{1,100}", session))
    owns = bool(writer and identity_known and writer == {"host": host, "session_id": session})
    status = state["status"]
    if status in {"needs_reconcile", "conflict", "stale"}:
        action = "explicit_recovery_required"
    elif status in {"completed", "retired"}:
        action = "terminal_context"
    elif not attached:
        action = "explicit_enrollment_required"
    elif not selected:
        action = "select_attached_context"
    elif not identity_known:
        action = "verify_current_identity"
    elif owns:
        action = "save_and_read_back"
    elif status == "clean":
        action = "explicit_clean_takeover" if writer else "explicit_writer_selection"
    else:
        action = "source_writer_save_required"
    result = {
        "role": "writer" if owns else "observer",
        "registered_writer": writer, "current_host": host if host in {"gui1", "gui2"} else None,
        "identity_source": "process_environment_and_configured_home",
        "authorization_verified": False,
        "can_save": owns and status in {"clean", "dirty"},
        "required_action": action, "latest_progress_verified": False,
        "coverage_note": "Clean validates saved records, not coverage of current work. "
                         "Only an agent-reviewed payload, successful save and independent readback verify a handoff; "
                         "Markdown alone is not a checkpoint.",
    }
    if action == "explicit_recovery_required":
        if not attached:
            recovery = "explicit_enrollment_required"
        elif not selected:
            recovery = "select_attached_context"
        elif not identity_known:
            recovery = "verify_current_identity"
        elif not writer:
            recovery = "scoped_maintenance_recovery_required"
        elif state["project"]["status"] == "completed":
            recovery = "completion_lifecycle_review_required"
        elif status == "conflict":
            recovery = "inspect_conflict_and_reread"
        elif status == "stale":
            recovery = "repair_stable_refs_and_reread"
        else:
            recovery = ("current_writer_reviewed_reconcile_requires_explicit_authorization"
                        if owns else "source_writer_recovery_required")
        result["recovery_action"] = recovery
    return result


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


def read_gui1_context(repo_root: str | Path, context_id: str, **kwargs: Any) -> dict[str, Any]:
    """Read the GUI1 context without changing historical GUI/TUI labels."""
    return read_context(repo_root, context_id, host="gui1", **kwargs)


def read_gui2_context(repo_root: str | Path, context_id: str, **kwargs: Any) -> dict[str, Any]:
    """Read the GUI2 context from the former TUI home, without writing."""
    return read_context(repo_root, context_id, host="gui2", **kwargs)


__all__ = ["SUPPORTED_HOSTS", "read_context", "read_gui1_context", "read_gui2_context",
           "read_gui_context", "read_tui_context"]


def main(argv: list[str] | None = None) -> int:
    """Native hook command; consume metadata, never open a transcript locator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, help="Default: the validated hook workspace.")
    parser.add_argument("--context-id", help="Explicit context; otherwise the one open context is selected.")
    parser.add_argument("--mei-root", required=True, type=Path)
    parser.add_argument("--host", default="auto", choices=sorted(SUPPORTED_HOSTS | {"auto"}))
    parser.add_argument("--writer-thread-id", help="Optional: restrict lifecycle writes to one native thread.")
    parser.add_argument("--codex-home", type=Path, help="Optional: restrict lifecycle writes to one CODEX_HOME.")
    parser.add_argument("--diagnose", action="store_true", help="Read-only host/context/memory diagnostics; no hook input or writes.")
    parser.add_argument("--lifecycle-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--diagnostic-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    actual_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    home_matches = not args.codex_home or actual_home.resolve() == args.codex_home.resolve()
    host = args.host if args.host != "auto" else _host_for_home(args.mei_root, actual_home)
    if args.diagnostic_child:
        try:
            envelope = json.loads(sys.stdin.buffer.read(8193))
            if not isinstance(envelope, dict) or args.repo_root is None:
                raise ValueError("invalid metadata")
            result = _event_receipt(args, envelope, host)
        except (ValueError, OSError, TypeError):
            result = "invalid_metadata"
        print(json.dumps({"diagnostics": result}))
        return 0
    if args.diagnose:
        try:
            repository = args.repo_root or Path.cwd()
            selected = {"status": "selected", "context_id": args.context_id} if args.context_id else select_context(repository, mei_root=args.mei_root)
            result = {"status": "ok", "host": host, "codex_home": str(actual_home), "context": selected,
                      "memory": _memory_digest(args.mei_root), "native_permissions_verified": False,
                      "cold_session_verified": False}
            if selected.get("status") == "selected":
                state = read_context(repository, selected["context_id"], host=host, mei_root=args.mei_root)
                result["context"].update({"status": state["status"], "project_sha256": state["project_sha256"],
                                         "generation": state["checkpointer"]["generation"]})
            if selected.get("status") not in {"selected", "clean"} or result["memory"].get("status") != "ok":
                result["status"] = "needs_attention"
        except (ContinuityError, OSError, ValueError, TypeError) as exc:
            result = {"status": "unavailable", "error": type(exc).__name__}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1
    event = None
    automatic = False
    startup = False
    envelope = {}
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
        # Preserve the validated selection in parent diagnostic receipts too;
        # native hooks normally omit the optional --context-id argument.
        args.context_id = context_id
        if not args.lifecycle_child:
            _event_receipt(args, envelope, host)
        if event in READ_EVENTS:
            state = None
            context: dict[str, Any] = {"host": host, "codex_home": str(actual_home)}
            context["permission_report"] = {
                "value": os.environ.get("CODEX_PERMISSION_PROFILE", "unreported"),
                "source": "hook_process_environment", "verified": False,
                "effective_permissions": "not_inspected",
            }
            attached = _attachment(args.mei_root, args.repo_root)
            context["lifecycle_writer"] = ("attached_context_worker" if attached and attached.get("writer")
                else "not_selected" if attached else
                _local_config(args.mei_root).get("host", {}).get("continuity_lifecycle_profile", "not_selected"))
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
                context["obsidian"] = (_refresh_projection(args.mei_root, context.get("generation", "none"))
                    if _direct_vault(args.mei_root) else {"status": "manual_sync_required"})
            context["authority"] = "Transient task context only; current user instructions provide authorization."
            context["continuity_check"] = _continuity_check(
                state, event=event, checkpoint_allowed=bool(attached and
                    home_matches and (not args.writer_thread_id or args.writer_thread_id == envelope.get("session_id")) and
                    attached.get("context_id") == context_id and
                    attached.get("writer") == {"host": host, "session_id": envelope.get("session_id")}),
                source=envelope.get("source"),
                selection_status=selection.get("status"),
            )
            # Keep the recovery guard at the head of model-visible output even
            # if a large saved project makes the host spill the middle to disk.
            context = {"continuity_check": context.pop("continuity_check"), **context}
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
                # A generic Stop request cannot authorize recovery or a foreign writer.
                owner = state["checkpointer"]["writer_session_id"]
                session = envelope.get("session_id")
                owns_state = isinstance(session, str) and owner == f"{host}-{session}"[:128]
                needs_save = state["status"] == "dirty" and owns_state
                attached = _attachment(args.mei_root, args.repo_root)
                if attached:
                    needs_save = needs_save and attached.get("writer") == {"host": host, "session_id": session}
                reason = ("current_writer_dirty" if needs_save else
                          "explicit_recovery_required" if state["status"] in {"needs_reconcile", "conflict", "stale"} else
                          "another_writer" if state["status"] == "dirty" else "no_save_required")
                wire = {
                    "continue": True,
                    "systemMessage": "Mei continuity Stop check: " + json.dumps({
                        "status": state["status"], "needs_save": needs_save, "reason": reason,
                        "repo_id": state["repo_id"], "context_id": context_id,
                        "generation": state["checkpointer"]["generation"],
                    }, ensure_ascii=False),
                }
                if needs_save:
                    # continue:false ends the turn; decision:block requests one
                    # continuation of this same agent. stop_hook_active guards it.
                    wire["decision"] = "block"
                    wire["reason"] = (
                        "Save the current handoff through the installed mei-continuity entrypoint for "
                        f"repo={state['repo_id']} context={context_id} generation={state['checkpointer']['generation']}. "
                        "Use only the current task's bounded fields and a fresh snapshot. If task ownership or writer authorization "
                        "does not match, report blocked once; do not retry, create a context, reconcile, or change permissions. "
                        "No background model was started."
                    )
        else:
            session = envelope.get("session_id")
            if context_id and not args.lifecycle_child and not (args.writer_thread_id or args.codex_home):
                wire = _sandboxed_lifecycle(args, envelope, host, actual_home, context_id)
                if wire is not None:
                    _emit(args, envelope, host, wire)
                    return 0
            selected_profile = os.environ.get("CODEX_PERMISSION_PROFILE")
            explicit_legacy_writer = bool(args.writer_thread_id or args.codex_home) and selected_profile in {"mei-maintainer", "mei-continuity-writer"}
            is_writer = (args.lifecycle_child or explicit_legacy_writer) and home_matches and isinstance(session, str) and bool(session) and (
                not args.writer_thread_id or session == args.writer_thread_id
            )
            attached = _attachment(args.mei_root, args.repo_root)
            if attached:
                is_writer = is_writer and attached.get("writer") == {"host": host, "session_id": session}
            if not context_id:
                # No selected context: nothing to gate or record. Never create one from a hook.
                wire = {"continue": True, "suppressOutput": True} if event == "UserPromptSubmit" else {
                    "continue": True, "systemMessage": "Mei continuity: not_attached; no selected context. "
                    "Compaction is allowed, but no handoff was saved. Use the explicit project attach entrypoint."}
                _emit(args, envelope, host, wire)
                return 0
            if not is_writer:
                if event == "UserPromptSubmit":
                    _emit(args, envelope, host, {"continue": True, "suppressOutput": True})
                    return 0  # A readback witness must not change bookkeeping.
                state = read_context(args.repo_root, context_id, host=host, mei_root=args.mei_root)
                allow = automatic or state["status"] in {"clean", "completed", "retired"}
                wire = {"continue": allow, "systemMessage": "Mei continuity (read-only witness): " + json.dumps({"status": state["status"], "allow": allow})}
                if not allow:
                    wire["stopReason"] = "Explicitly sync the selected Mei context before manual compact."
                _emit(args, envelope, host, wire)
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
    except (ContinuityError, OSError, ValueError, KeyError, TypeError) as exc:
        # Do not echo unvalidated input or record contents into host diagnostics.
        message = "Mei continuity unavailable; inspect the selected pair."
        if startup:
            message += (" Before the next task action, inspect continuity read-only. Do not resume task mutations "
                        "from an unverified handoff, automatically reconcile or change permissions; report an unresolved blocker once.")
        if isinstance(exc, _LifecycleFailure):
            message += " " + json.dumps(exc.diagnostic)
        wire = {"continue": automatic or startup or event in {"UserPromptSubmit", STOP_EVENT}, "systemMessage": message}
        if event == "SessionStart":
            wire["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": json.dumps({
                "status": "context_unavailable",
                "continuity_check": _continuity_check(None, event=event, checkpoint_allowed=False, source=envelope.get("source")),
            })}
        _emit(args, envelope, host, wire, error=exc)
        # Keep the response parseable by the native host. Automatic failures
        # remain fail-open; manual failures stop through continue:false.
        return 0
    _emit(args, envelope, host, wire)
    # A valid hook response is controlled by its JSON envelope. In particular,
    # PreCompact uses continue:false to stop compaction; a non-zero exit causes
    # the native host to discard that control response and report hook failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
