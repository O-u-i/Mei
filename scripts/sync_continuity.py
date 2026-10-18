"""Explicitly synchronize one reviewed continuity payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import ROOT
from check_continuity import (
    MAX_CHECKPOINTER_BYTES,
    MAX_PROJECT_BYTES,
    ContinuityError,
    _assert_private,
    _check_context_id,
    _safe_relative_path,
    _stable_ref_status,
    SHA256_PATTERN,
    load_context,
    repo_id_for,
)
from checkpoint_continuity import (
    _LockTimeout,
    _context_lock,
    _record_bytes,
    _replace_record,
)


_PROJECT_FIELDS = (
    "schema_version",
    "repo_id",
    "context_id",
    "status",
    "goal",
    "decisions",
    "constraints",
    "blockers",
    "next_step",
    "stable_refs",
)


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    added: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    for key in _PROJECT_FIELDS:
        if key not in before and key in after:
            added.append(key)
        elif before.get(key) != after.get(key):
            updated.append(key)
        else:
            skipped.append(key)
    return {
        "added": added,
        "updated": updated,
        "archived": [],
        "skipped": skipped,
        "reasons": {
            "added": {key: "present in the reviewed payload only" for key in added},
            "updated": {key: "value differs from the fresh snapshot" for key in updated},
            "archived": {"none": "Phase 3 has no archive sink; completion export is a later phase"},
            "skipped": {key: "unchanged from the fresh snapshot" for key in skipped},
        },
    }


def _snapshot_base(snapshot: dict[str, Any]) -> tuple[int, str, str]:
    try:
        checkpointer = snapshot["checkpointer"]
        generation = checkpointer["generation"]
        project_sha256 = snapshot["project_sha256"]
        index_sha256 = snapshot["index_sha256"]
    except (KeyError, TypeError) as exc:
        raise ContinuityError("fresh snapshot is incomplete") from exc
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ContinuityError("fresh snapshot generation is invalid")
    if not isinstance(project_sha256, str) or not SHA256_PATTERN.fullmatch(project_sha256):
        raise ContinuityError("fresh snapshot project hash is invalid")
    if not isinstance(index_sha256, str) or not SHA256_PATTERN.fullmatch(index_sha256):
        raise ContinuityError("fresh snapshot index hash is invalid")
    return generation, project_sha256, index_sha256


def _check_enrolled_writer(repo_root, context_id, writer_session_id, mei_root):
    # Recheck under the context lock, after a queued save/lifecycle launch.
    # The attachment is a cooperative gate, not an OS permission credential.
    from continuity_adapters import _attachment

    attached = _attachment(Path(mei_root or ROOT), Path(repo_root))
    if attached:
        writer = attached.get("writer")
        expected = f'{writer["host"]}-{writer["session_id"]}'[:128] if writer else None
        if attached["context_id"] != context_id or writer_session_id != expected:
            raise ContinuityError("enrolled writer changed; reread the explicit attachment")


def _sync_locked(
    repo_root: str | Path,
    context_id: str,
    snapshot: dict[str, Any],
    proposed_project: dict[str, Any],
    *,
    writer_session_id: str,
    mei_root: str | Path | None = None,
    remote: str | None = None,
    reconcile: bool = False,
) -> dict[str, Any]:
    _check_enrolled_writer(repo_root, context_id, writer_session_id, mei_root)
    if snapshot.get("repo_id") != repo_id_for(repo_root, remote) or snapshot.get("context_id") != context_id:
        raise ContinuityError("snapshot identity does not match selected repo/context")
    current = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
    if current["project"]["status"] == "completed":
        raise ContinuityError("completed contexts require the completion lifecycle, not sync")
    base_generation, base_project_sha256, base_index_sha256 = _snapshot_base(snapshot)
    if (
        base_generation != current["checkpointer"]["generation"]
        or base_project_sha256 != current["project_sha256"]
        or base_index_sha256 != current["index_sha256"]
    ):
        return {
            "status": "conflict",
            "repo_id": current["repo_id"],
            "context_id": context_id,
            "conflict": {
                "expected_generation": base_generation,
                "observed_generation": current["checkpointer"]["generation"],
                "expected_project_sha256": base_project_sha256,
                "observed_project_sha256": current["project_sha256"],
                "expected_index_sha256": base_index_sha256,
                "observed_index_sha256": current["index_sha256"],
                "required_action": "reread",
            },
            "attempted_project": proposed_project,
        }
    if current["status"] == "conflict" and not reconcile:
        return {
            "status": "conflict",
            "repo_id": current["repo_id"],
            "context_id": context_id,
            "required_action": "explicit_reconcile",
            "diagnostics": current["diagnostics"],
        }
    if current["needs_reconcile"] and not reconcile:
        return {
            "status": "needs_reconcile",
            "repo_id": current["repo_id"],
            "context_id": context_id,
            "required_action": "explicit_reconcile",
            "diagnostics": current["diagnostics"],
        }
    root = Path(mei_root or ROOT).expanduser()
    entry = next(e for e in current["index"]["entries"] if (e["repo_id"], e["context_id"]) == (current["repo_id"], context_id))
    project_path = _safe_relative_path(root, entry["project_path"], "project_path")
    checkpointer_path = _safe_relative_path(root, entry["checkpointer_path"], "checkpointer_path")
    for key in ("repo_id", "context_id", "status"):
        if proposed_project.get(key) != current["project"].get(key):
            raise ContinuityError(f"sync cannot change {key} or the continuity index")
    project_raw = _record_bytes(proposed_project, "continuity-project.schema.json", project_path, MAX_PROJECT_BYTES)
    _stable_ref_status(Path(repo_root), proposed_project["stable_refs"])
    changes = _changes(current["project"], proposed_project)
    semantic_changed = bool(changes["added"] or changes["updated"])
    if not semantic_changed:
        # Timestamp-only payloads do not rewrite semantic state or count as a sync.
        project_raw = project_path.read_bytes()
    if not semantic_changed and current["status"] == "clean":
        return {
            "status": "noop",
            "repo_id": current["repo_id"],
            "context_id": context_id,
            "generation": current["checkpointer"]["generation"],
            "changes": changes,
            "sizes": {"project_bytes": len(project_raw), "index_bytes": current["index_bytes"], "index_lines": current["index_lines"]},
            "validation": {"ok": True},
        }
    same_project_bytes = hashlib.sha256(project_raw).hexdigest() == current["project_sha256"]
    next_generation = current["checkpointer"]["generation"] + (0 if same_project_bytes else 1)
    checkpointer = dict(
        current["checkpointer"],
        writer_session_id=writer_session_id,
        generation=next_generation,
        synced_generation=next_generation,
        dirty=False,
        needs_reconcile=False,
        last_event="reconcile-sync" if reconcile else "sync",
        updated_at=datetime.now(timezone.utc).isoformat(),
        project_sha256=hashlib.sha256(project_raw).hexdigest(),
        index_sha256=current["index_sha256"],
        index_lines=current["index_lines"],
        index_bytes=current["index_bytes"],
        last_conflict=None,
    )
    checkpointer_raw = _record_bytes(checkpointer, "continuity-checkpointer.schema.json", checkpointer_path, MAX_CHECKPOINTER_BYTES)
    try:
        if not same_project_bytes:
            _replace_record(project_path, project_raw)
        _replace_record(checkpointer_path, checkpointer_raw)
    except OSError:
        observed = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
        if observed["needs_reconcile"] or observed["status"] == "conflict":
            return {
                "status": "needs_reconcile",
                "repo_id": observed["repo_id"],
                "context_id": context_id,
                "generation": observed["checkpointer"]["generation"],
                "required_action": "explicit_reconcile",
                "diagnostics": ["sync replacement failed; re-read before recovery", *observed["diagnostics"]],
            }
        raise
    saved = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
    if saved["status"] != "clean":
        return {
            "status": "needs_reconcile",
            "repo_id": saved["repo_id"],
            "context_id": context_id,
            "generation": saved["checkpointer"]["generation"],
            "required_action": "explicit_reconcile",
            "diagnostics": saved["diagnostics"],
        }
    return {
        "status": "synced",
        "repo_id": saved["repo_id"],
        "context_id": context_id,
        "generation": saved["checkpointer"]["generation"],
        "changes": changes,
        "sizes": {"project_bytes": len(project_raw), "index_bytes": saved["index_bytes"], "index_lines": saved["index_lines"]},
        "validation": {"ok": saved["status"] == "clean"},
    }


def sync_context(
    repo_root: str | Path,
    context_id: str,
    snapshot: dict[str, Any],
    proposed_project: dict[str, Any],
    *,
    writer_session_id: str,
    mei_root: str | Path | None = None,
    remote: str | None = None,
    reconcile: bool = False,
    lock_timeout: float = 5.0,
) -> dict[str, Any]:
    """Synchronize an explicitly reviewed payload against a fresh loader snapshot."""
    _check_context_id(context_id)
    if not isinstance(snapshot, dict) or not isinstance(proposed_project, dict):
        raise ContinuityError("snapshot and proposed_project must be objects")
    if not isinstance(writer_session_id, str) or not writer_session_id:
        raise ContinuityError("writer_session_id must be non-empty")
    _assert_private(writer_session_id, Path("writer_session_id"))
    if not isinstance(reconcile, bool):
        raise ContinuityError("reconcile must be boolean")
    if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float)) or not math.isfinite(lock_timeout) or not 0 <= lock_timeout <= 60:
        raise ContinuityError("lock_timeout must be a finite number from 0 to 60 seconds")
    root = Path(mei_root or ROOT).expanduser()
    repo_id = repo_id_for(repo_root, remote)
    directory = _safe_relative_path(root, f"memory/continuity/{repo_id}/{context_id}", "context")
    if not directory.is_dir():
        raise ContinuityError("selected continuity context directory is missing")
    try:
        with _context_lock(directory, lock_timeout):
            return _sync_locked(
                repo_root, context_id, snapshot, proposed_project,
                writer_session_id=writer_session_id, mei_root=root,
                remote=remote, reconcile=reconcile,
            )
    except _LockTimeout:
        return {"status": "lock_timeout", "repo_id": repo_id, "context_id": context_id, "required_action": "retry"}
    except OSError as exc:
        raise ContinuityError(str(exc)) from exc


def _lifecycle_locked(
    repo_root: str | Path,
    context_id: str,
    event: str,
    *,
    writer_session_id: str,
    mei_root: str | Path | None = None,
    remote: str | None = None,
) -> dict[str, Any]:
    _check_enrolled_writer(repo_root, context_id, writer_session_id, mei_root)
    current = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
    if current["status"] in {"completed", "retired"}:
        return {
            "event": event, "allow": True, "recorded": False,
            "reason": "completed_context", "status": current["status"],
            "generation": current["checkpointer"]["generation"],
        }
    if event == "precompact_manual":
        allow = current["status"] == "clean"
        return {
            "event": event,
            "allow": allow,
            "reason": "clean" if allow else "sync_required",
            "status": current["status"],
            "generation": current["checkpointer"]["generation"],
        }
    if event == "user_prompt_submit":
        dirty = True
        needs_reconcile = current["needs_reconcile"]
        last_event = event
        allow = True
    else:
        dirty = current["checkpointer"]["dirty"]
        needs_reconcile = True
        last_event = event
        allow = True
    entry = next(e for e in current["index"]["entries"] if (e["repo_id"], e["context_id"]) == (current["repo_id"], context_id))
    checkpointer_path = _safe_relative_path(Path(mei_root or ROOT), entry["checkpointer_path"], "checkpointer_path")
    checkpointer = dict(
        current["checkpointer"],
        writer_session_id=writer_session_id,
        generation=current["checkpointer"]["generation"] + 1,
        dirty=dirty,
        needs_reconcile=needs_reconcile,
        last_event=last_event,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    raw = _record_bytes(checkpointer, "continuity-checkpointer.schema.json", checkpointer_path, MAX_CHECKPOINTER_BYTES)
    _replace_record(checkpointer_path, raw)
    saved = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
    return {
        "event": event,
        "allow": allow,
        "status": saved["status"],
        "generation": saved["checkpointer"]["generation"],
        "recorded": True,
        "diagnostics": saved["diagnostics"],
    }


def handle_lifecycle_event(
    repo_root: str | Path,
    context_id: str,
    event: str,
    *,
    mei_root: str | Path | None = None,
    remote: str | None = None,
    writer_session_id: str = "lifecycle-hook",
    lock_timeout: float = 5.0,
) -> dict[str, Any]:
    """Handle a compact lifecycle signal without accepting prompt contents."""
    _check_context_id(context_id)
    allowed_events = {"user_prompt_submit", "precompact_manual", "precompact_auto", "precompact_unknown"}
    if event not in allowed_events:
        raise ContinuityError("event must be user_prompt_submit, precompact_manual, precompact_auto, or precompact_unknown")
    if not isinstance(writer_session_id, str) or not writer_session_id:
        raise ContinuityError("writer_session_id must be non-empty")
    if len(writer_session_id) > 128:
        raise ContinuityError("writer_session_id must contain at most 128 characters")
    if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float)) or not math.isfinite(lock_timeout) or not 0 <= lock_timeout <= 60:
        raise ContinuityError("lock_timeout must be a finite number from 0 to 60 seconds")
    _assert_private(writer_session_id, Path("writer_session_id"))
    root = Path(mei_root or ROOT).expanduser()
    repo_id = repo_id_for(repo_root, remote)
    directory = _safe_relative_path(root, f"memory/continuity/{repo_id}/{context_id}", "context")
    if not directory.is_dir():
        raise ContinuityError("selected continuity context directory is missing")
    try:
        with _context_lock(directory, lock_timeout):
            return _lifecycle_locked(
                repo_root, context_id, event,
                writer_session_id=writer_session_id, mei_root=root, remote=remote,
            )
    except _LockTimeout:
        if event in {"precompact_auto", "precompact_unknown"}:
            return {"event": event, "allow": True, "recorded": False, "reason": "lock_timeout"}
        return {"event": event, "allow": False, "recorded": False, "reason": "lock_timeout"}
    except (ContinuityError, OSError) as exc:
        if event in {"precompact_auto", "precompact_unknown"}:
            return {"event": event, "allow": True, "recorded": False, "reason": "record_failed", "diagnostics": [str(exc)]}
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--mei-root", type=Path, default=ROOT)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--writer-session-id", required=True)
    parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        proposed = json.loads(args.project.read_text(encoding="utf-8"))
        result = sync_context(
            args.repo_root, args.context_id, snapshot, proposed,
            writer_session_id=args.writer_session_id, mei_root=args.mei_root,
            reconcile=args.reconcile,
        )
    except (ContinuityError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return {"synced": 0, "noop": 0, "conflict": 2}.get(result["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
