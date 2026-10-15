"""Explicit maintainer creation of one context, with an audited index-only rebase.

Hooks never call this command. It locks the registry and all existing contexts,
backs up the old locator/checkpointers, and preserves every existing project and
semantic generation. Interrupted creation requires reviewed recovery from backup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from _common import ROOT
from check_continuity import (
    ContinuityError, MAX_CHECKPOINTER_BYTES, MAX_INDEX_BYTES, MAX_INDEX_LINES,
    MAX_PROJECT_BYTES, _assert_no_reparse, _assert_private, _check_context_id,
    _expected_paths, _read_json, _read_yaml, _safe_relative_path, _validate,
    load_context, repo_id_for,
)
from checkpoint_continuity import _context_lock, _record_bytes, _replace_record


def create_context(repo_root: Path, context_id: str, project: dict, *, mei_root: Path = ROOT,
                   expected_index_sha256: str | None = None, writer_session_id: str,
                   rebase_index: bool = False) -> dict:
    """Create, never reuse, an explicitly selected context. No memory promotion."""
    _assert_no_reparse(mei_root)
    mei_root = mei_root.resolve()
    _check_context_id(context_id)
    repo_id = repo_id_for(repo_root)
    if project.get("repo_id") != repo_id or project.get("context_id") != context_id or project.get("status") != "active":
        raise ContinuityError("new project must be active and match the selected repo/context")
    project_locator, checkpointer_locator = _expected_paths(repo_id, context_id)
    target = _safe_relative_path(mei_root, project_locator, "new project")
    project_raw = _record_bytes(project, "continuity-project.schema.json", target, MAX_PROJECT_BYTES)
    registry = mei_root / "registry"
    _assert_no_reparse(registry)
    registry.mkdir(exist_ok=True)
    index_path = registry / "continuity.yaml"
    backup = _safe_relative_path(mei_root, f"local/context-creations/{repo_id}-{context_id}", "creation backup")
    with _context_lock(registry, 5.0), ExitStack() as locks:
        _assert_no_reparse(index_path)
        if index_path.exists():
            index, old_index = _read_yaml(index_path)
            _validate(index, "continuity-index.schema.json", index_path)
            _assert_private(index, index_path)
        else:
            old_index = None
            index = {"schema_version": "v0.1.0", "version": (mei_root / "VERSION").read_text().strip(), "entries": []}
        observed_hash = hashlib.sha256(old_index).hexdigest() if old_index is not None else None
        if expected_index_sha256 != observed_hash:
            raise ContinuityError("stale index hash; reread before explicit creation")
        keys = [(entry["repo_id"], entry["context_id"]) for entry in index["entries"]]
        if len(keys) != len(set(keys)) or (repo_id, context_id) in keys or target.parent.exists():
            raise ContinuityError("duplicate or previously used context identity")
        if index["entries"] and not rebase_index:
            raise ContinuityError("explicit rebase_index approval is required for existing index entries")
        old_pairs = []
        for entry in sorted(index["entries"], key=lambda item: (item["repo_id"], item["context_id"])):
            expected = _expected_paths(entry["repo_id"], entry["context_id"])
            if (entry["project_path"], entry["checkpointer_path"]) != expected:
                raise ContinuityError("index entry path is not deterministic")
            project_path = _safe_relative_path(mei_root, entry["project_path"], "existing project")
            checkpoint_path = _safe_relative_path(mei_root, entry["checkpointer_path"], "existing checkpointer")
            locks.enter_context(_context_lock(project_path.parent, 5.0))
            old_project, raw_project = _read_json(project_path, MAX_PROJECT_BYTES)
            checkpoint, raw_checkpoint = _read_json(checkpoint_path, MAX_CHECKPOINTER_BYTES)
            _validate(old_project, "continuity-project.schema.json", project_path)
            _validate(checkpoint, "continuity-checkpointer.schema.json", checkpoint_path)
            for value in (old_project, checkpoint):
                _assert_private(value, checkpoint_path)
                if (value["repo_id"], value["context_id"]) != (entry["repo_id"], entry["context_id"]):
                    raise ContinuityError("existing pair identity mismatch")
            if checkpoint["project_sha256"] != hashlib.sha256(raw_project).hexdigest() or checkpoint["index_sha256"] != observed_hash:
                raise ContinuityError("existing pair needs reconciliation before index rebase")
            old_pairs.append((checkpoint_path, checkpoint, raw_checkpoint))
        updated_index = dict(index, entries=index["entries"] + [{
            "repo_id": repo_id, "context_id": context_id, "project_path": project_locator,
            "checkpointer_path": checkpointer_locator, "status": "active",
        }])
        index_raw = _record_bytes(updated_index, "continuity-index.schema.json", index_path, MAX_INDEX_BYTES)
        if len(index_raw.splitlines()) > MAX_INDEX_LINES:
            raise ContinuityError("index line cap exceeded")
        index_fields = {"index_sha256": hashlib.sha256(index_raw).hexdigest(),
                        "index_bytes": len(index_raw), "index_lines": len(index_raw.splitlines())}
        checkpoint = {
            "schema_version": "v0.1.0", "repo_id": repo_id, "context_id": context_id,
            "writer_session_id": writer_session_id, "generation": 0, "synced_generation": 0,
            "dirty": False, "needs_reconcile": False, "last_event": "explicit-context-creation",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "project_sha256": hashlib.sha256(project_raw).hexdigest(), "last_conflict": None, **index_fields,
        }
        checkpoint_path = _safe_relative_path(mei_root, checkpointer_locator, "new checkpointer")
        checkpoint_raw = _record_bytes(checkpoint, "continuity-checkpointer.schema.json", checkpoint_path, MAX_CHECKPOINTER_BYTES)
        rebased = [(path, _record_bytes(dict(value, **index_fields), "continuity-checkpointer.schema.json", path, MAX_CHECKPOINTER_BYTES))
                   for path, value, _ in old_pairs]
        if (index_path.read_bytes() if index_path.exists() else None) != old_index or any(path.read_bytes() != raw for path, _, raw in old_pairs):
            raise ContinuityError("index or checkpointer changed externally; reread before creation")
        backup.mkdir(parents=True, exist_ok=False)
        receipt = {"previous_index": old_index.decode("utf-8") if old_index is not None else None,
                   "previous_checkpointers": {path.relative_to(mei_root).as_posix(): raw.decode("utf-8") for path, _, raw in old_pairs},
                   "new_context": project_locator, "index_sha256": index_fields["index_sha256"],
                   "rollback": "Restore only the recorded index/checkpointer bytes after stopping writers; retain new context files outside the restored index."}
        (backup / "before.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=False)
        _replace_record(target, project_raw)
        _replace_record(checkpoint_path, checkpoint_raw)
        for path, raw in rebased:
            _replace_record(path, raw)
        _replace_record(index_path, index_raw)  # Publish last; readers detect an interrupted rebase.
    state = load_context(repo_root, context_id, mei_root=mei_root)
    return {"status": state["status"], "context_id": context_id, "repo_id": repo_id,
            "project_sha256": state["project_sha256"], "index_sha256": state["index_sha256"],
            "rebased_checkpointers": [path.relative_to(mei_root).as_posix() for path, _ in rebased],
            "backup": str(backup / "before.json")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--mei-root", type=Path, default=ROOT)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--expected-index-sha256")
    parser.add_argument("--writer-session-id", required=True)
    parser.add_argument("--rebase-index", action="store_true")
    args = parser.parse_args()
    try:
        project, _ = _read_json(args.project, MAX_PROJECT_BYTES)
        result = create_context(args.repo_root, args.context_id, project, mei_root=args.mei_root,
                                expected_index_sha256=args.expected_index_sha256,
                                writer_session_id=args.writer_session_id, rebase_index=args.rebase_index)
    except (ContinuityError, OSError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
