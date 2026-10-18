"""Two-layer Mei enrollment: setup each host once, attach each project once.

Plan by default; --apply is explicit maintenance authorization. No trust hashes,
background agents, dependency installation, memory promotion or project edits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from _common import ROOT
from check_continuity import (ContinuityError, MAX_INDEX_LINES, MAX_PROJECT_BYTES, _assert_no_reparse, _assert_private,
                              _check_context_id, _read_json, _read_yaml, _validate, load_context, repo_id_for)
from checkpoint_continuity import _context_lock, _replace_record, _record_bytes
from continuity_adapters import _attachment, select_context
from create_continuity import create_context

EVENTS = ("SessionStart", "UserPromptSubmit", "PreCompact", "PostCompact", "Stop")


def _config(root):
    path = root / "local/paths.toml"
    _assert_no_reparse(path)
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    if Path(value["paths"]["mei_root"]).resolve() != root.resolve():
        raise ContinuityError("configured Mei root differs from this checkout")
    return value


def _bytes(path):
    _assert_no_reparse(path)
    if not path.exists():
        return None
    if path.stat().st_size > 1_048_576:
        raise ContinuityError("configuration exceeds its hard cap")
    return path.read_bytes()


def _publish(root, changes, apply):
    """Preserve unrelated bytes; CAS and backups before any replacement.

    ponytail: no cross-file transaction; backups + idempotent rerun recover an
    interrupted setup. Never claim applied until every exact file reads back.
    """
    pending = [(path, old, new) for path, old, new in changes if old != new]
    result = {"changed_files": [str(path) for path, _, _ in pending], "applied": False}
    if not apply or not pending:
        result["applied"] = apply
        return result
    with _context_lock(root / "local", 5.0):
        if any(_bytes(path) != old for path, old, _ in pending):
            raise ContinuityError("configuration drift; rerun the plan")
        for path, old, new in pending:
            if old is not None:
                key = hashlib.sha256(str(path).encode() + b"\0" + old).hexdigest()
                backup = root / "local/setup-backups" / f"{key}.bak"
                _assert_no_reparse(backup)
                backup.parent.mkdir(exist_ok=True)
                if backup.exists() and backup.read_bytes() != old:
                    raise ContinuityError("setup backup collision")
                if not backup.exists():
                    with backup.open("xb") as stream:
                        stream.write(old)
            _assert_no_reparse(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if _bytes(path) != old:
                raise ContinuityError("configuration changed during apply")
            _replace_record(path, new)
            if _bytes(path) != new:
                raise ContinuityError("configuration readback failed")
    result["applied"] = True
    return result


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def setup(root, hosts, *, apply=False):
    config = _config(root)
    changes, installed = [], []
    command = (f'{subprocess.list2cmdline([sys.executable])} -X utf8 -B '
               f'"{root / "scripts/continuity_adapters.py"}" --mei-root "{root}"')
    for host in hosts:
        home = Path(config["paths"][f"codex_home_{host}"]).expanduser()
        if not home.is_absolute() or not home.is_dir():
            raise ContinuityError("host home must be an existing configured absolute path")
        hooks = home / "hooks.json"
        old = _bytes(hooks)
        value = _read_json(hooks, 1_048_576)[0] if old else {"hooks": {}}
        if not isinstance(value.get("hooks"), dict):
            raise ContinuityError("invalid host hooks object")
        for event in EVENTS:
            groups = value["hooks"].setdefault(event, [])
            if not isinstance(groups, list) or any(not isinstance(g, dict) or not isinstance(g.get("hooks"), list) for g in groups):
                raise ContinuityError("invalid hook group")
            existing = [hook for group in groups for hook in group["hooks"]
                        if isinstance(hook, dict) and "continuity_adapters.py" in hook.get("command", "")]
            if len(existing) > 1:
                raise ContinuityError("duplicate Mei user hooks; review before setup")
            wanted = {"type": "command", "command": command, "timeout": 15}
            if existing:
                # Existing command options might select an obsolete task/home.
                if existing[0].get("command") != command:
                    raise ContinuityError("existing Mei hook command differs; review exact options before replacement")
            else:
                groups.append({"hooks": [wanted]})
        new = old if old and json.loads(old) == value else _encoded(value)
        changes.append((hooks, old, new))
        settings = home / "config.toml"
        raw = _bytes(settings)
        if raw is None:
            raise ContinuityError("setup requires an existing Codex config")
        parsed = tomllib.loads(raw.decode("utf-8"))
        profile = "mei-hook-diagnostics"
        grant = {"extends": ":read-only", "filesystem": {str(root / "local/hook-events"): "write"},
                 "network": {"enabled": False}}
        existing = parsed.get("permissions", {}).get(profile)
        if existing is not None and existing != grant:
            raise ContinuityError("diagnostics profile differs; review instead of broadening")
        addition = (f"\n[permissions.{profile}]\nextends = \":read-only\"\n"
                    f"[permissions.{profile}.filesystem]\n{json.dumps(str(root / 'local/hook-events'))} = \"write\"\n"
                    f"[permissions.{profile}.network]\nenabled = false\n").encode()
        changes.append((settings, raw, raw if existing else raw + addition))
        installed.append({"host": host, "home": str(home), "events": list(EVENTS),
                          "discovery": "unverified", "trust": "requires_native_host_review"})
    skills = Path(config["paths"]["skills_root"])
    if not skills.is_absolute() or not skills.is_dir():
        raise ContinuityError("configured skills root must exist")
    pointer = skills / ".mei-root.json"
    old = _bytes(pointer)
    wanted = {"mei_root": str(root.resolve()), "skills_root": str(skills.resolve())}
    if old and json.loads(old) != wanted:
        raise ContinuityError("installed skill pointer belongs to another checkout")
    changes.append((pointer, old, old or _encoded(wanted)))
    marker = root / "local/host-setup.json"
    old = _bytes(marker)
    previous = json.loads(old) if old else {"hosts": {}}
    previous["diagnostics_profile"] = "mei-hook-diagnostics"
    for item in installed:
        previous["hosts"][item["host"]] = item
    changes.append((marker, old, old if old and json.loads(old) == previous else _encoded(previous)))
    if apply:
        diagnostic_root = root / "local/hook-events"
        _assert_no_reparse(diagnostic_root)
        diagnostic_root.mkdir(exist_ok=True)
    return {"status": "setup", **_publish(root, changes, apply), "hosts": installed,
            "native_permissions_verified": False, "live_compact_verified": False}


def _repository(path):
    _assert_no_reparse(path)
    path = path.resolve()
    result = subprocess.run(["git", "--no-optional-locks", "-C", str(path), "rev-parse", "--show-toplevel"],
                            capture_output=True, encoding="utf-8", timeout=10)
    if result.returncode or Path(result.stdout.strip()).resolve() != path:
        raise ContinuityError("repo-root must be the actual Git root")
    return path


def attach(root, repo, *, context_id="project", goal=None, host=None, session_id=None,
           role="observer", takeover=False, apply=False):
    config = _config(root)
    repo = _repository(repo)
    _check_context_id(context_id)
    repo_id = repo_id_for(repo)
    path = root / f"local/attachments/{repo_id}.json"
    old = _bytes(path)
    previous = _attachment(root, repo)
    # Enrollment may choose a new deterministic ID, never guess an existing one.
    if previous and previous["context_id"] != context_id:
        raise ContinuityError("already attached to another context; select it explicitly")
    index_path = root / "registry/continuity.yaml"
    index, raw_index = _read_yaml(index_path) if index_path.exists() else ({"entries": []}, None)
    if raw_index is not None:
        _validate(index, "continuity-index.schema.json", index_path)
        _assert_private(index, index_path)
        if len(raw_index.splitlines()) > MAX_INDEX_LINES:
            raise ContinuityError("index exceeds line cap")
        keys = [(e["repo_id"], e["context_id"]) for e in index["entries"]]
        if len(keys) != len(set(keys)):
            raise ContinuityError("duplicate repository/context index entry")
    candidates = [e["context_id"] for e in index["entries"] if e["repo_id"] == repo_id]
    creation = context_id not in candidates
    if creation and candidates:
        raise ContinuityError("existing contexts require explicit selection; no automatic extra context")
    if creation and not goal:
        raise ContinuityError("new context requires the current task's explicit bounded --goal")
    state = None if creation else load_context(repo, context_id, mei_root=root)
    if state and state["project"]["status"] == "completed":
        raise ContinuityError("completed context cannot be attached for writing")
    writer = previous.get("writer") if previous else None
    if role == "writer":
        if host not in {"gui1", "gui2"} or not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", session_id):
            raise ContinuityError("writer enrollment requires --host and actual --session-id")
        if not writer and state and state["status"] != "clean":
            raise ContinuityError("dirty/recovery context cannot be claimed automatically")
        requested = {"host": host, "session_id": session_id}
        if writer and writer != requested and (not takeover or not state or state["status"] != "clean"):
            raise ContinuityError("writer already selected; explicit takeover requires clean state")
        writer = requested
    profile = "mei-context-" + repo_id[5:]
    value = {"repo_id": repo_id, "repo_root": str(repo), "context_id": context_id,
             "permission_profile": profile, "writer": writer}
    _assert_private(value, path)
    changes = [(path, old, old if previous == value else _encoded(value))]
    context_dir = root / f"memory/continuity/{repo_id}/{context_id}"
    if host not in {None, "gui1", "gui2", "all"}:
        raise ContinuityError("unknown attachment host")
    for surface in ((host,) if host in {"gui1", "gui2"} else ("gui1", "gui2")):
        home = Path(config["paths"][f"codex_home_{surface}"])
        settings = home / "config.toml"
        raw = _bytes(settings)
        if raw is None:
            raise ContinuityError("run host setup with an existing Codex config first")
        parsed = tomllib.loads(raw.decode("utf-8"))
        grant = {"extends": ":read-only", "filesystem": {str(context_dir): "write"}, "network": {"enabled": False}}
        permissions = parsed.get("permissions", {})
        existing = permissions.get(profile)
        if existing is not None and existing != grant:
            raise ContinuityError("context profile differs; never silently broaden an existing profile")
        addition = (f"\n[permissions.{profile}]\nextends = \":read-only\"\n"
                    f"[permissions.{profile}.filesystem]\n{json.dumps(str(context_dir))} = \"write\"\n"
                    f"[permissions.{profile}.network]\nenabled = false\n").encode()
        proposed = raw if existing else raw + addition
        tomllib.loads(proposed.decode("utf-8"))
        changes.append((settings, raw, proposed))
    rebase = [e["checkpointer_path"] for e in index["entries"]] if creation else []
    result = {"status": "attach", "repo_id": repo_id, "context_id": context_id, "writer": writer,
              "creation_required": creation, "technical_index_rebase": rebase,
              "native_permissions_verified": False, "live_compact_verified": False}
    project = None
    if creation:
        project = {"schema_version": "v0.1.0", "repo_id": repo_id, "context_id": context_id,
                   "status": "active", "goal": goal, "decisions": [], "constraints": [], "blockers": [],
                   "next_step": "Await the explicitly selected project writer and its current task.",
                   "stable_refs": [], "updated_at": datetime.now(timezone.utc).isoformat()}
        # Invalid/private task text fails even the plan, before host config edits.
        _record_bytes(project, "continuity-project.schema.json", context_dir / "project.json", MAX_PROJECT_BYTES)
    if creation and apply:
        # Profiles are inert until the last attachment publication. If native
        # approval is missing, fail before rebasing any existing context.
        result["configuration"] = _publish(root, changes[1:], True)
        result["creation"] = create_context(repo, context_id, project, mei_root=root,
            expected_index_sha256=hashlib.sha256(raw_index).hexdigest() if raw_index else None,
            writer_session_id="explicit-attach", rebase_index=True)
    # Publish host grants before selecting the writer, also for an existing
    # context. A denied config must leave the previous attachment untouched.
    publication = changes[:1] if creation and apply else changes[1:] + changes[:1]
    if apply and not creation and writer != (previous.get("writer") if previous else None):
        # Serialize a writer transfer with both lifecycle events and saves.
        # A clean preflight is stale as soon as the old writer submits a prompt.
        with _context_lock(context_dir, 5.0):
            fresh = load_context(repo, context_id, mei_root=root)
            if fresh["status"] != "clean" or fresh["checkpointer"] != state["checkpointer"]:
                raise ContinuityError("context changed before writer publication; reread and retry explicitly")
            result.update(_publish(root, publication, True))
    else:
        result.update(_publish(root, publication, apply))
    result["health"] = status(root, repo) if apply else {"status": "plan_only"}
    return result


def status(root, repo):
    _config(root)
    repo = _repository(repo)
    selected = select_context(repo, mei_root=root)
    attached = _attachment(root, repo)
    repo_id = repo_id_for(repo)
    events = {}
    for host in ("gui1", "gui2"):
        events[host] = {}
        for event in EVENTS:
            path = root / f"local/hook-events/{repo_id}/{host}/{event}.json"
            events[host][event] = _read_json(path, 8192)[0] if path.is_file() else {"status": "unobserved", "cause": "unknown"}
    result = {"status": "attached" if attached else "not_attached", "context": selected,
              "writer": attached.get("writer") if attached else None, "events": events,
              "permission_report": {"value": os.environ.get("CODEX_PERMISSION_PROFILE", "unreported"),
                                    "source": "process_environment", "verified": False},
              "live_compact_verified": False}
    if selected.get("status") == "selected":
        state = load_context(repo, selected["context_id"], mei_root=root)
        result["context"].update(state=state["status"], generation=state["checkpointer"]["generation"],
                                 project_sha256=state["project_sha256"])
    return result


def save_enrolled(root, repo, context_id, snapshot, project, *, writer_session_id, reconcile=False):
    """Narrow native sandbox for an enrolled writer; no fallback to ambient access."""
    config = _config(root)
    repo = _repository(repo)
    attached = _attachment(root, repo)
    if not attached:
        raise ContinuityError("project is not attached")
    writer = attached.get("writer")
    if not writer or attached["context_id"] != context_id:
        raise ContinuityError("no selected writer for this context")
    if writer_session_id != f'{writer["host"]}-{writer["session_id"]}'[:128]:
        raise ContinuityError("save identity differs from selected writer")
    actual_session = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    actual_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    if (actual_session != writer["session_id"]
            or actual_home != Path(config["paths"][f'codex_home_{writer["host"]}']).resolve()):
        raise ContinuityError("save process does not match the selected host/session; labels are not permission proof")
    executable = Path(config["paths"]["codex_cli"])
    _assert_no_reparse(executable)
    if not executable.is_absolute() or not executable.is_file():
        raise ContinuityError("native context sandbox executable is unavailable")
    # A nested native sandbox need not see the outer sandbox's private TEMP.
    # Read bounded inputs as the caller; transfer bytes, not temporary paths.
    try:
        snapshot_value = _read_json(snapshot.resolve(), 1_048_576)[0]
        project_value = _read_json(project.resolve(), MAX_PROJECT_BYTES)[0]
        # Reuse the writer's read-only validation before spending a native
        # initialization/approval; the locked writer still revalidates all input.
        from sync_continuity import _snapshot_base
        from check_continuity import _stable_ref_status
        _snapshot_base(snapshot_value)
        _record_bytes(project_value, "continuity-project.schema.json", project, MAX_PROJECT_BYTES)
        _stable_ref_status(repo, project_value["stable_refs"])
        payload = json.dumps({"snapshot": snapshot_value, "project": project_value},
                             ensure_ascii=False, allow_nan=False)
    except (ContinuityError, OSError, ValueError):
        raise ContinuityError("native save input unavailable or invalid; no fallback was attempted") from None
    if len(payload.encode("utf-8")) > 1_048_576:
        raise ContinuityError("native save input exceeds its hard cap")
    command = [str(executable), "sandbox", "-P", attached["permission_profile"], "-C", str(repo)]
    layered = config.get("host", {}).get(f'codex_config_profile_{writer["host"]}')
    if layered:
        command.extend(["--profile", layered])
    command.extend(["--", sys.executable, "-X", "utf8", "-B", str(root / "scripts/continuity_handoff.py"),
                    "--mei-root", str(root), "--repo-root", str(repo), "--context-id", context_id,
                    "--input-stdin",
                    "--writer-session-id", writer_session_id])
    if reconcile:
        command.append("--reconcile")
    child = subprocess.run(command, input=payload, capture_output=True, encoding="utf-8", timeout=20,
                           env=dict(os.environ, CODEX_HOME=str(actual_home), PYTHONDONTWRITEBYTECODE="1"))
    if child.returncode:
        # Distinguish writer rejection from native initialization; never forward
        # raw error/stdout/stderr or arbitrary fields from child output.
        stderr = child.stderr or ""
        reason = ("runtime_cap_sid_unavailable" if "write cap sid file" in stderr else
                  "runtime_restricted_token_unavailable" if "createrestrictedtoken failed:" in stderr.lower() else
                  "runtime_desktop_unavailable" if "createdesktopw failed:" in stderr.lower() else
                  "runtime_home_unavailable" if "canonicaliz" in stderr.lower() else
                  "native_child_failed")
        try:
            wire = json.loads(child.stdout)
        except (TypeError, ValueError):
            wire = None
        stage = "; stage=native_initialization" if reason.startswith("runtime_") else ""
        if isinstance(wire, dict):
            writer_status = wire.get("status")
            if writer_status in ("handoff_conflict", "handoff_needs_reconcile", "handoff_lock_timeout"):
                reason = "writer_" + writer_status.removeprefix("handoff_")
            elif writer_status == "error":
                code = wire.get("error_code")
                reason = ("writer_" + code if code in (
                    "invalid_json", "invalid_encoding", "permission_denied", "file_not_found",
                    "io_error", "continuity_rejected", "proposal_rejected", "invalid_value")
                    else "writer_rejected_unclassified")
                if wire.get("failure_stage") in ("read_stdin", "read_snapshot", "read_project", "read_memory_draft", "save_handoff"):
                    stage = "; stage=" + wire["failure_stage"]
        raise ContinuityError(f"selected native save failed: exit={child.returncode}; reason={reason}{stage}; "
                              "no fallback was attempted")
    wire = json.loads(child.stdout)
    if wire.get("status") not in {"handoff_saved", "handoff_noop"}:
        raise ContinuityError("selected native writer did not save")
    return wire


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("setup", "attach", "status", "save"))
    parser.add_argument("--mei-root", type=Path, default=ROOT)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--host", choices=("gui1", "gui2", "all"))
    parser.add_argument("--context-id", default="project")
    parser.add_argument("--goal")
    parser.add_argument("--role", choices=("observer", "writer"), default="observer")
    parser.add_argument("--session-id")
    parser.add_argument("--takeover", action="store_true", help="Explicit clean-state writer transfer.")
    parser.add_argument("--apply", action="store_true", help="Authorize the scoped plan, backups and index-only rebase.")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--writer-session-id")
    parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.mei_root.resolve()
        if args.operation == "setup":
            if not args.host:
                raise ContinuityError("setup requires explicit --host")
            result = setup(root, ("gui1", "gui2") if args.host == "all" else (args.host,), apply=args.apply)
        elif args.operation == "attach":
            result = attach(root, args.repo_root, context_id=args.context_id, goal=args.goal, host=args.host,
                            session_id=args.session_id, role=args.role, takeover=args.takeover, apply=args.apply)
        elif args.operation == "save":
            if not args.snapshot or not args.project or not args.writer_session_id:
                raise ContinuityError("save requires --snapshot, --project and --writer-session-id")
            result = save_enrolled(root, args.repo_root, args.context_id, args.snapshot, args.project,
                                   writer_session_id=args.writer_session_id, reconcile=args.reconcile)
        else:
            if args.apply:
                raise ContinuityError("status is always read-only")
            result = status(root, args.repo_root)
    except (ContinuityError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
