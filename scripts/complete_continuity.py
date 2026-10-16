"""Complete one context, export one reviewed trajectory, and hand off to Curator."""

from __future__ import annotations

import copy
import argparse
import hashlib
import json
import math
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from _common import ROOT, json_text, publish_new_file
from check_continuity import (
    MAX_CHECKPOINTER_BYTES, MAX_PROJECT_BYTES, ContinuityError, _assert_private,
    _check_context_id, _completion_id, _RETIRED_FIELDS, _safe_relative_path, _read_json, load_context, repo_id_for,
)
from checkpoint_continuity import _LockTimeout, _context_lock, _record_bytes, _replace_record
from sync_continuity import _snapshot_base

MAX_TRAJECTORY_BYTES = 65_536


@contextmanager
def _selected_context(repo_root, context_id, snapshot, writer_session_id, mei_root, remote, lock_timeout):
    _check_context_id(context_id)
    if not isinstance(snapshot, dict):
        raise ContinuityError("snapshot must be an object")
    if not isinstance(writer_session_id, str) or not 1 <= len(writer_session_id) <= 128:
        raise ContinuityError("writer_session_id must contain 1 to 128 characters")
    _assert_private(writer_session_id, Path("writer_session_id"))
    if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float)) or not math.isfinite(lock_timeout) or not 0 <= lock_timeout <= 60:
        raise ContinuityError("lock_timeout must be a finite number from 0 to 60 seconds")
    repo_id = repo_id_for(repo_root, remote)
    if snapshot.get("repo_id") != repo_id or snapshot.get("context_id") != context_id:
        raise ContinuityError("snapshot identity does not match selected repo/context")
    base = _snapshot_base(snapshot)
    root = Path(mei_root or ROOT).expanduser()
    directory = _safe_relative_path(root, f"memory/continuity/{repo_id}/{context_id}", "context")
    if not directory.is_dir():
        raise ContinuityError("selected continuity context directory is missing")
    with _context_lock(directory, lock_timeout):
        current = load_context(repo_root, context_id, mei_root=root, remote=remote)
        observed = (current["checkpointer"]["generation"], current["project_sha256"], current["index_sha256"])
        conflict = None if base == observed else {
            "status": "conflict", "repo_id": repo_id, "context_id": context_id,
            "required_action": "reread", "generation": observed[0],
            "project_sha256": observed[1], "index_sha256": observed[2],
        }
        yield root, current, conflict


def _prepared_trajectory(current, trajectory, receipt=None):
    if not isinstance(trajectory, dict):
        raise ContinuityError("reviewed_trajectory must be an object")
    receipt = receipt or {
        "source_project_sha256": current["project_sha256"],
        "source_generation": current["checkpointer"]["generation"],
    }
    trajectory_id = _completion_id(current["repo_id"], current["context_id"])
    value = copy.deepcopy(trajectory)
    value["id"] = trajectory_id  # The task/context, never a caller's rotating ID, owns the export.
    if not isinstance(value.get("result"), dict) or value["result"].get("status") != "completed":
        raise ContinuityError("completion requires a completed trajectory result")
    evidence = value["result"].get("evidence")
    if not isinstance(evidence, list):
        raise ContinuityError("trajectory result evidence must be an array")
    provenance = (
        f'Continuity source: memory/continuity/{current["repo_id"]}/{current["context_id"]}/project.json; '
        f'source_generation={receipt["source_generation"]}; source_sha256={receipt["source_project_sha256"]}'
    )
    if provenance not in evidence:
        evidence.append(provenance)
    _record_bytes(value, "trajectory.schema.json", Path(trajectory_id + ".json"), MAX_TRAJECTORY_BYTES)
    return value, json_text(value).encode("utf-8")


def _save_pair(repo_root, root, current, project, receipt, writer_session_id, event, remote):
    directory = f'memory/continuity/{current["repo_id"]}/{current["context_id"]}'
    project_path = _safe_relative_path(root, directory + "/project.json", "project_path")
    checkpointer_path = _safe_relative_path(root, directory + "/checkpointer.json", "checkpointer_path")
    project_raw = _record_bytes(project, "continuity-project.schema.json", project_path, MAX_PROJECT_BYTES)
    generation = current["checkpointer"]["generation"] + 1
    checkpointer = dict(
        current["checkpointer"], generation=generation, synced_generation=generation,
        dirty=False, needs_reconcile=False, last_conflict=None,
        writer_session_id=writer_session_id, last_event=event,
        updated_at=datetime.now(timezone.utc).isoformat(),
        project_sha256=hashlib.sha256(project_raw).hexdigest(), completion=receipt,
        index_sha256=current["index_sha256"], index_lines=current["index_lines"], index_bytes=current["index_bytes"],
    )
    raw = _record_bytes(checkpointer, "continuity-checkpointer.schema.json", checkpointer_path, MAX_CHECKPOINTER_BYTES)
    try:
        _replace_record(project_path, project_raw)
        _replace_record(checkpointer_path, raw)
    except OSError:
        observed = load_context(repo_root, current["context_id"], mei_root=root, remote=remote)
        if observed["needs_reconcile"]:
            return {"status": "needs_reconcile", "required_action": "explicit_reconcile", "diagnostics": observed["diagnostics"]}
        raise
    saved = load_context(repo_root, current["context_id"], mei_root=root, remote=remote)
    return {
        "status": saved["status"], "repo_id": saved["repo_id"], "context_id": saved["context_id"],
        "generation": saved["checkpointer"]["generation"], "project_sha256": saved["project_sha256"],
        "index_sha256": saved["index_sha256"], "required_action": saved["required_action"],
    }


def completion_checkpoint(
    repo_root: str | Path, context_id: str, snapshot: dict, reviewed_trajectory: dict,
    *, privacy_reviewed: bool, writer_session_id: str, mei_root: str | Path | None = None,
    remote: str | None = None, reconcile: bool = False, lock_timeout: float = 5.0,
) -> dict:
    """Bind a human-reviewed completed result to one context; do not export it yet."""
    if privacy_reviewed is not True:
        raise ContinuityError("explicit privacy review is required before completion")
    if not isinstance(reconcile, bool):
        raise ContinuityError("reconcile must be boolean")
    try:
        with _selected_context(repo_root, context_id, snapshot, writer_session_id, mei_root, remote, lock_timeout) as (root, current, conflict):
            if conflict:
                return conflict
            if (current["needs_reconcile"] or current["status"] == "conflict") and not reconcile:
                return {"status": current["status"], "required_action": "explicit_reconcile"}
            receipt = current["checkpointer"].get("completion")
            if receipt is None and current["project"]["status"] == "completed" and current["project_sha256"] != current["checkpointer"]["project_sha256"]:
                # Explicit recovery of a project-first interrupted checkpoint.
                receipt = {
                    "source_project_sha256": current["checkpointer"]["project_sha256"],
                    "source_generation": current["checkpointer"]["generation"],
                }
            value, raw = _prepared_trajectory(current, reviewed_trajectory, receipt)
            digest = hashlib.sha256(raw).hexdigest()
            if receipt and "trajectory_sha256" in receipt:
                if receipt["trajectory_id"] != value["id"] or receipt["trajectory_sha256"] != digest:
                    return {"status": "conflict", "required_action": "review_existing_completion"}
                if not current["needs_reconcile"] and current["status"] != "conflict":
                    return {"status": "noop", "trajectory": value, "completion": receipt}
            receipt = dict(
                receipt or {"source_project_sha256": current["project_sha256"], "source_generation": current["checkpointer"]["generation"]},
                trajectory_id=value["id"], trajectory_sha256=digest, retired=bool(receipt and receipt.get("retired")),
            )
            project = dict(current["project"], status="completed", updated_at=datetime.now(timezone.utc).isoformat())
            result = _save_pair(repo_root, root, current, project, receipt, writer_session_id, "completion-checkpoint", remote)
            result.update(trajectory=value, completion=receipt)
            return result
    except _LockTimeout:
        return {"status": "lock_timeout", "required_action": "retry"}


def _completion_ready(current):
    if current["needs_reconcile"] or current["status"] == "conflict":
        return {"status": current["status"], "required_action": "explicit_reconcile"}
    if current["project"]["status"] != "completed" or "completion" not in current["checkpointer"]:
        raise ContinuityError("a reviewed completion checkpoint is required")
    return None


def _trajectory_location(root, current):
    receipt = current["checkpointer"]["completion"]
    name = receipt["trajectory_id"] + ".json"
    matches = []
    for folder in ("inbox", "processed", "rejected", "archive"):
        path = _safe_relative_path(root, f"trajectories/{folder}/{name}", "trajectory")
        if path.exists():
            value, raw = _read_json(path, MAX_TRAJECTORY_BYTES)
            _record_bytes(value, "trajectory.schema.json", path, MAX_TRAJECTORY_BYTES)
            if value["id"] != receipt["trajectory_id"] or hashlib.sha256(raw).hexdigest() != receipt["trajectory_sha256"]:
                return {"status": "conflict", "required_action": "review_existing_export"}
            matches.append(path.relative_to(root).as_posix())
    if len(matches) > 1:
        return {"status": "conflict", "required_action": "review_duplicate_exports", "source_paths": matches}
    if matches:
        return {"status": "already_exported", "source_path": matches[0], "source_sha256": receipt["trajectory_sha256"]}
    for folder in ("normalized", "archive/normalized"):
        path = _safe_relative_path(root, f"trajectories/{folder}/{name}", "normalized trajectory")
        if path.exists():
            return {"status": "conflict", "required_action": "restore_missing_trajectory_source"}
    return None


def export_completed_trajectory(
    repo_root: str | Path, context_id: str, snapshot: dict, reviewed_trajectory: dict,
    *, mei_root: str | Path | None = None, remote: str | None = None, lock_timeout: float = 5.0,
) -> dict:
    """Create only the receipt-bound inbox file; identical replay never rewrites it."""
    try:
        with _selected_context(repo_root, context_id, snapshot, "completion-export", mei_root, remote, lock_timeout) as (root, current, conflict):
            if conflict:
                return conflict
            blocked = _completion_ready(current)
            if blocked:
                return blocked
            receipt = current["checkpointer"]["completion"]
            value, raw = _prepared_trajectory(current, reviewed_trajectory, receipt)
            if value["id"] != receipt["trajectory_id"] or hashlib.sha256(raw).hexdigest() != receipt["trajectory_sha256"]:
                return {"status": "conflict", "required_action": "use_reviewed_completion_payload"}
            existing = _trajectory_location(root, current)
            if existing:
                return existing
            if receipt["retired"]:
                return {"status": "conflict", "required_action": "restore_missing_trajectory_source"}
            target = _safe_relative_path(root, f'trajectories/inbox/{value["id"]}.json', "trajectory")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = _safe_relative_path(root, f'trajectories/inbox/.{value["id"]}.{uuid.uuid4().hex}.tmp', "trajectory temporary")
            try:
                with temporary.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                _safe_relative_path(root, target.relative_to(root).as_posix(), "trajectory")
                try:
                    publish_new_file(temporary, target)
                except FileExistsError:
                    return _trajectory_location(root, current) or {"status": "conflict", "required_action": "reread"}
            finally:
                if temporary.exists():
                    temporary.unlink()  # Exactly this invocation's named temporary file.
            saved = _trajectory_location(root, current)
            if saved and saved["status"] == "already_exported":
                saved["status"] = "exported"
            return saved or {"status": "conflict", "required_action": "restore_missing_trajectory_source"}
    except _LockTimeout:
        return {"status": "lock_timeout", "required_action": "retry"}


def retire_completed_context(
    repo_root: str | Path, context_id: str, snapshot: dict,
    *, writer_session_id: str, mei_root: str | Path | None = None,
    remote: str | None = None, lock_timeout: float = 5.0,
) -> dict:
    """Retire task-local fields only after the receipt-bound source is verified."""
    try:
        with _selected_context(repo_root, context_id, snapshot, writer_session_id, mei_root, remote, lock_timeout) as (root, current, conflict):
            if conflict:
                return conflict
            blocked = _completion_ready(current)
            if blocked:
                return blocked
            source = _trajectory_location(root, current)
            if source is None:
                return {"status": "completed", "required_action": "export_and_retire"}
            if source["status"] == "conflict":
                return source
            receipt = current["checkpointer"]["completion"]
            if receipt["retired"]:
                return {"status": "noop", "required_action": "none", **{k: source[k] for k in ("source_path", "source_sha256")}}
            project = dict(
                current["project"], **_RETIRED_FIELDS,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            result = _save_pair(
                repo_root, root, current, project, dict(receipt, retired=True),
                writer_session_id, "completion-retired", remote,
            )
            result.update(source_path=source["source_path"], source_sha256=source["source_sha256"])
            return result
    except _LockTimeout:
        return {"status": "lock_timeout", "required_action": "retry"}


def curator_handoff(
    repo_root: str | Path, context_id: str,
    *, mei_root: str | Path | None = None, remote: str | None = None,
) -> dict:
    """Read-only evidence handoff; every review/promotion step remains Curator-owned."""
    current = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
    blocked = _completion_ready(current)
    if blocked:
        return blocked
    source = _trajectory_location(Path(mei_root or ROOT).expanduser(), current)
    if source is None:
        return {"status": "completed", "required_action": "export_and_retire"}
    if source["status"] == "conflict":
        return source
    receipt = current["checkpointer"]["completion"]
    return {
        "status": "curator_handoff", "repo_id": current["repo_id"], "context_id": context_id,
        "trajectory_id": receipt["trajectory_id"], "source_path": source["source_path"],
        "source_sha256": source["source_sha256"],
        "continuity_source_sha256": receipt["source_project_sha256"],
        "continuity_source_generation": receipt["source_generation"],
        "review_required": ["privacy", "provenance", "duplicates", "conflicts", "currentness", "explicit_promotion"],
        "promotion_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("checkpoint", "export", "retire", "curator"))
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--mei-root", type=Path, default=ROOT)
    parser.add_argument("--remote")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--trajectory", type=Path)
    parser.add_argument("--writer-session-id")
    parser.add_argument("--privacy-reviewed", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--lock-timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    if args.operation != "curator" and args.snapshot is None:
        parser.error("--snapshot is required for mutation")
    if args.operation in {"checkpoint", "export"} and args.trajectory is None:
        parser.error("--trajectory is required for checkpoint/export")
    if args.operation in {"checkpoint", "retire"} and args.writer_session_id is None:
        parser.error("--writer-session-id is required for checkpoint/retire")
    if args.reconcile and args.operation != "checkpoint":
        parser.error("--reconcile is only allowed with a reviewed completion checkpoint")
    try:
        options = {"mei_root": args.mei_root, "remote": args.remote}
        if args.operation == "curator":
            result = curator_handoff(args.repo_root, args.context_id, **options)
        else:
            snapshot, _ = _read_json(args.snapshot, 524_288)
            options["lock_timeout"] = args.lock_timeout
            if args.operation == "retire":
                result = retire_completed_context(args.repo_root, args.context_id, snapshot, writer_session_id=args.writer_session_id, **options)
            else:
                trajectory, _ = _read_json(args.trajectory, MAX_TRAJECTORY_BYTES)
                if args.operation == "checkpoint":
                    result = completion_checkpoint(
                        args.repo_root, args.context_id, snapshot, trajectory,
                        writer_session_id=args.writer_session_id, privacy_reviewed=args.privacy_reviewed,
                        reconcile=args.reconcile, **options,
                    )
                else:
                    result = export_completed_trajectory(args.repo_root, args.context_id, snapshot, trajectory, **options)
    except (ContinuityError, OSError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] == "completed" and args.operation != "checkpoint":
        return 3  # Export is still required; retirement/handoff did not succeed.
    return {"conflict": 2, "needs_reconcile": 3, "lock_timeout": 4}.get(result["status"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
