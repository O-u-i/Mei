"""Checkpoint one existing continuity context without writing other Mei records."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import uuid
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import ROOT
from check_continuity import (
    MAX_CHECKPOINTER_BYTES,
    MAX_PROJECT_BYTES,
    SHA256_PATTERN,
    ContinuityError,
    _assert_no_reparse,
    _assert_private,
    _check_context_id,
    _safe_relative_path,
    _read_json,
    _stable_ref_status,
    _validate,
    load_context,
    repo_id_for,
)


class _LockTimeout(ContinuityError):
    pass


@contextmanager
def _context_lock(directory: Path, timeout: float):
    """Use a kernel-released lock without a third durable continuity record."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        identity = directory.resolve().as_posix().casefold().encode("utf-8")
        name = "Global\\MeiContinuity-" + hashlib.sha256(identity).hexdigest()
        handle = kernel.CreateMutexW(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
            if result == 258:
                raise _LockTimeout("selected continuity context is locked")
            if result not in (0, 128):  # WAIT_OBJECT_0 or WAIT_ABANDONED after owner death.
                raise ctypes.WinError(ctypes.get_last_error())
            acquired = True
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        import fcntl

        descriptor = os.open(directory, os.O_RDONLY)
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _LockTimeout("selected continuity context is locked")
                    time.sleep(min(0.05, remaining))
            yield
        finally:
            os.close(descriptor)


def _record_bytes(value: dict, schema: str, path: Path, limit: int) -> bytes:
    if not isinstance(value, dict):
        raise ContinuityError("checkpoint payload must be a JSON object")
    _assert_private(value, path)
    try:
        raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ContinuityError("checkpoint payload is not valid UTF-8 JSON") from exc
    if len(raw) > limit:
        raise ContinuityError(f"checkpoint record exceeds {limit} byte hard cap")
    _validate(value, schema, path)
    return raw


def _replace_record(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    _assert_no_reparse(path)
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _assert_no_reparse(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()  # Only this invocation's explicitly named temporary file.


def _checkpoint_locked(
    repo_root: str | Path,
    context_id: str,
    proposed_project: dict,
    *,
    base_generation: int,
    base_project_sha256: str,
    base_index_sha256: str,
    writer_session_id: str,
    mei_root: str | Path | None = None,
    remote: str | None = None,
    reconcile: bool = False,
) -> dict[str, Any]:
    """Save a reviewed payload into the selected, already indexed context."""
    state = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
    if state["project"]["status"] == "completed":
        raise ContinuityError("completed contexts require the completion lifecycle, not checkpoint")
    root = Path(mei_root or ROOT)
    entry = next(e for e in state["index"]["entries"] if (e["repo_id"], e["context_id"]) == (state["repo_id"], context_id))
    project_path = _safe_relative_path(root, entry["project_path"], "project_path")
    checkpointer_path = _safe_relative_path(root, entry["checkpointer_path"], "checkpointer_path")
    project_raw = _record_bytes(proposed_project, "continuity-project.schema.json", project_path, MAX_PROJECT_BYTES)
    if (
        base_generation != state["checkpointer"]["generation"]
        or base_project_sha256 != state["project_sha256"]
        or base_index_sha256 != state["index_sha256"]
    ):
        return {
            "status": "conflict",
            "repo_id": state["repo_id"],
            "context_id": context_id,
            "conflict": {
                "expected_generation": base_generation,
                "observed_generation": state["checkpointer"]["generation"],
                "expected_project_sha256": base_project_sha256,
                "observed_project_sha256": state["project_sha256"],
                "expected_index_sha256": base_index_sha256,
                "observed_index_sha256": state["index_sha256"],
                "writer_session_id": writer_session_id,
                "required_action": "reread",
            },
            "attempted_project": proposed_project,
        }
    if state["needs_reconcile"] and not reconcile:
        return {
            "status": "needs_reconcile",
            "repo_id": state["repo_id"],
            "context_id": context_id,
            "required_action": "explicit_reconcile",
            "diagnostics": state["diagnostics"],
        }
    for key in ("repo_id", "context_id", "status"):
        if proposed_project[key] != state["project"][key]:
            raise ContinuityError(f"checkpoint cannot change {key} or the continuity index")
    _stable_ref_status(Path(repo_root), proposed_project["stable_refs"])
    checkpointer = dict(
        state["checkpointer"],
        writer_session_id=writer_session_id,
        generation=state["checkpointer"]["generation"] + 1,
        dirty=True,
        needs_reconcile=False,
        last_event="reconcile-checkpoint" if reconcile else "checkpoint",
        updated_at=datetime.now(timezone.utc).isoformat(),
        project_sha256=hashlib.sha256(project_raw).hexdigest(),
        index_sha256=state["index_sha256"],
        index_lines=state["index_lines"],
        index_bytes=state["index_bytes"],
        last_conflict=None,
    )
    checkpointer_raw = _record_bytes(checkpointer, "continuity-checkpointer.schema.json", checkpointer_path, MAX_CHECKPOINTER_BYTES)
    try:
        _replace_record(project_path, project_raw)
        _replace_record(checkpointer_path, checkpointer_raw)
    except OSError:
        observed = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
        if not observed["needs_reconcile"] and observed["project_sha256"] == state["project_sha256"]:
            raise
        return {
            "status": "needs_reconcile",
            "repo_id": observed["repo_id"],
            "context_id": context_id,
            "generation": observed["checkpointer"]["generation"],
            "project_sha256": observed["project_sha256"],
            "index_sha256": observed["index_sha256"],
            "required_action": "explicit_reconcile",
            "diagnostics": ["checkpoint replacement failed; re-read before explicit recovery", *observed["diagnostics"]],
        }
    saved = load_context(repo_root, context_id, mei_root=mei_root, remote=remote)
    return {
        "status": "checkpointed",
        "repo_id": saved["repo_id"],
        "context_id": context_id,
        "generation": saved["checkpointer"]["generation"],
        "project_sha256": saved["project_sha256"],
        "index_sha256": saved["index_sha256"],
        "continuity_status": saved["status"],
        "warnings": saved["warnings"],
    }


def checkpoint_context(
    repo_root: str | Path,
    context_id: str,
    proposed_project: dict,
    *,
    base_generation: int,
    base_project_sha256: str,
    base_index_sha256: str,
    writer_session_id: str,
    mei_root: str | Path | None = None,
    remote: str | None = None,
    reconcile: bool = False,
    lock_timeout: float = 5.0,
) -> dict[str, Any]:
    """Lock and checkpoint a selected existing context against observed hashes.

    A conflict returns the attempted payload to the caller and preserves both
    records. Reconciliation requires an explicit reviewed payload and fresh
    generation/project/index hashes. Neither operation marks semantic sync done.
    """
    _check_context_id(context_id)
    if isinstance(base_generation, bool) or not isinstance(base_generation, int) or base_generation < 0:
        raise ContinuityError("base_generation must be a non-negative integer")
    if not isinstance(base_project_sha256, str) or not SHA256_PATTERN.fullmatch(base_project_sha256):
        raise ContinuityError("base_project_sha256 must be a lowercase SHA-256")
    if not isinstance(base_index_sha256, str) or not SHA256_PATTERN.fullmatch(base_index_sha256):
        raise ContinuityError("base_index_sha256 must be a lowercase SHA-256")
    if not isinstance(writer_session_id, str) or not 1 <= len(writer_session_id) <= 128:
        raise ContinuityError("writer_session_id must contain 1 to 128 characters")
    _assert_private(writer_session_id, Path("writer_session_id"))
    if not isinstance(reconcile, bool):
        raise ContinuityError("reconcile must be boolean")
    if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float)) or not math.isfinite(lock_timeout) or not 0 <= lock_timeout <= 60:
        raise ContinuityError("lock_timeout must be a finite number from 0 to 60 seconds")
    root = Path(mei_root or ROOT).expanduser()
    _assert_no_reparse(root)
    repo_id = repo_id_for(repo_root, remote)
    directory = _safe_relative_path(root, f"memory/continuity/{repo_id}/{context_id}", "context")
    if not directory.is_dir():
        raise ContinuityError("selected continuity context directory is missing")
    try:
        with _context_lock(directory, lock_timeout):
            return _checkpoint_locked(
                repo_root, context_id, proposed_project,
                base_generation=base_generation,
                base_project_sha256=base_project_sha256,
                base_index_sha256=base_index_sha256,
                writer_session_id=writer_session_id,
                mei_root=root,
                remote=remote,
                reconcile=reconcile,
            )
    except _LockTimeout:
        return {"status": "lock_timeout", "repo_id": repo_id, "context_id": context_id, "required_action": "retry"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--mei-root", type=Path, default=ROOT)
    parser.add_argument("--remote")
    parser.add_argument("--base-generation", required=True, type=int)
    parser.add_argument("--base-project-sha256", required=True)
    parser.add_argument("--base-index-sha256", required=True)
    parser.add_argument("--writer-session-id", required=True)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--lock-timeout", type=float, default=5.0, help="Seconds to wait for the context lock (default: 5).")
    parser.add_argument("--reconcile", action="store_true", help="Explicitly submit a reviewed recovery payload against a fresh snapshot.")
    args = parser.parse_args(argv)
    try:
        project, _ = _read_json(args.project, MAX_PROJECT_BYTES)
        result = checkpoint_context(
            args.repo_root,
            args.context_id,
            project,
            base_generation=args.base_generation,
            base_project_sha256=args.base_project_sha256,
            base_index_sha256=args.base_index_sha256,
            writer_session_id=args.writer_session_id,
            mei_root=args.mei_root,
            remote=args.remote,
            reconcile=args.reconcile,
            lock_timeout=args.lock_timeout,
        )
    except (ContinuityError, OSError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return {"checkpointed": 0, "conflict": 2, "needs_reconcile": 3, "lock_timeout": 4}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
