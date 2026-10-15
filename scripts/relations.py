"""Hash-bound reciprocal relation proposals and recoverable two-note transactions."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from _common import ROOT
from checkpoint_continuity import _LockTimeout, _context_lock
from memory_markdown import (CATEGORIES, MemoryError, _memory_root, _relative, _atomic_write,
                             list_notes, read_note, render_note, resolve_memory_root)

INVERSE = {"supersedes": "superseded_by", "superseded_by": "supersedes",
           "depends_on": "dependency_of", "dependency_of": "depends_on",
           "supports": "supported_by", "supported_by": "supports",
           "contradicts": "contradicted_by", "contradicted_by": "contradicts"}
RELATION_TYPES = set(INVERSE)
JOURNAL = ".relation-transaction.json"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _edges(note: dict) -> tuple[set[str], set[tuple[str, str]]]:
    return (set(note.get("related", [])) | set(note.get("relationships", [])),
            {(item["type"], item["target"]) for item in note.get("relations", [])})


def _set_edges(note: dict, related: set[str], typed: set[tuple[str, str]]) -> None:
    note["related"] = sorted(related)
    if "relationships" in note:
        note["relationships"] = sorted(related)
    note["relations"] = [{"type": kind, "target": target} for kind, target in sorted(typed)]


def _content(note: dict) -> str:
    return render_note({key: value for key, value in note.items() if key not in {"path", "sha256"}})


def _paths(root: Path, bundle: dict) -> dict[str, Path]:
    if not isinstance(bundle, dict) or set(bundle) != {"notes", "base_sha256"}:
        raise MemoryError("relation bundle must contain notes and base_sha256")
    notes, bases = bundle["notes"], bundle["base_sha256"]
    if not isinstance(notes, dict) or not isinstance(bases, dict) or not 1 <= len(notes) <= 2 or set(notes) != set(bases):
        raise MemoryError("relation transaction must bind one or two canonical notes")
    paths = {}
    for locator, content in notes.items():
        path = _relative(root, locator, "relation note")
        if len(Path(locator).parts) != 2 or Path(locator).parts[0] not in CATEGORIES:
            raise MemoryError("relation target must be a canonical note")
        if not isinstance(content, str) or not isinstance(bases[locator], str) or len(bases[locator]) != 64:
            raise MemoryError("invalid relation content or base hash")
        paths[locator] = path
    return paths


def snapshot(root: str | Path, bundle: dict, *, desired: bool = False) -> bytes:
    root = _memory_root(root)
    paths = _paths(root, bundle)
    return _json({locator: _sha(bundle["notes"][locator].encode("utf-8") if desired else path.read_bytes())
                  for locator, path in sorted(paths.items())})


def draft_relation(root: str | Path, source: str, target: str, relation_type: str = "related", *,
                   operation: str = "add", mei_root: str | Path = ROOT) -> dict:
    root = _memory_root(root)
    if root != resolve_memory_root(mei_root):
        raise MemoryError("relation root differs from selected Mei memory_root")
    if source == target or operation not in {"add", "remove"} or relation_type not in {"related", *RELATION_TYPES}:
        raise MemoryError("invalid relation operation, type, or self-relation")
    with _context_lock(root, 5.0):
        notes = {note["memory_id"]: note for note in list_notes(root)}
        if source not in notes or target not in notes:
            raise MemoryError("relation endpoints must be existing canonical notes")
        pair = [notes[source], notes[target]]
        for note, other, kind in ((pair[0], target, relation_type),
                                  (pair[1], source, INVERSE.get(relation_type, "related"))):
            related, typed = _edges(note)
            values, value = (related, other) if kind == "related" else (typed, (kind, other))
            (values.add if operation == "add" else values.discard)(value)
            _set_edges(note, related, typed)
        bundle = {"notes": {note["path"]: _content(note) for note in pair},
                  "base_sha256": {note["path"]: note["sha256"] for note in pair}}
    from review_proposals import draft_proposal
    return draft_proposal("relation_pair", "memory/markdown", bundle,
                          scope="reciprocal memory relation", source={"kind": "human_review", "locator": "explicit relation request"},
                          reason=f"{operation} {source} {relation_type} {target}; both endpoints are hash-bound.", mei_root=mei_root,
                          proposal_id="relation-" + _sha(_json(bundle))[:32])


def add(root: str | Path, source: str, target: str, relation_type: str | None = None, *, mei_root: str | Path = ROOT) -> dict:
    return draft_relation(root, source, target, relation_type or "related", mei_root=mei_root)


def remove(root: str | Path, source: str, target: str, relation_type: str | None = None, *, mei_root: str | Path = ROOT) -> dict:
    return draft_relation(root, source, target, relation_type or "related", operation="remove", mei_root=mei_root)


def _check_notes(notes: list[dict]) -> dict:
    values = {note["memory_id"]: note for note in notes}
    errors = []
    for memory_id, note in values.items():
        related, typed = _edges(note)
        for kind, target in [("related", value) for value in sorted(related)] + sorted(typed):
            entry = {"source": memory_id, "target": target, "type": kind}
            if target == memory_id:
                errors.append(dict(entry, kind="self_relation"))
            elif kind != "related" and kind not in INVERSE:
                errors.append(dict(entry, kind="invalid_type"))
            elif target not in values:
                errors.append(dict(entry, kind="dangling"))
            elif (memory_id not in _edges(values[target])[0] if kind == "related"
                  else (INVERSE[kind], memory_id) not in _edges(values[target])[1]):
                errors.append(dict(entry, kind="missing_reciprocal"))
    return {"status": "drift" if errors else "ok", "errors": errors}


def check(root: str | Path) -> dict:
    try:
        return _check_notes(list_notes(root))
    except (MemoryError, OSError, ValueError) as exc:
        # The schema rejects duplicate edges and illegal types before traversal.
        return {"status": "invalid", "errors": [{"kind": "invalid_note", "error": str(exc)}]}


def apply_bundle(root: str | Path, bundle: dict, *, expected_sha256: str, approved_content_sha256: str) -> dict:
    root = _memory_root(root)
    paths = _paths(root, bundle)
    if _sha(_json(bundle)) != approved_content_sha256:
        raise MemoryError("approved relation content hash mismatch")
    with _context_lock(root, 5.0):
        journal = _relative(root, JOURNAL, "relation journal")
        if journal.exists():
            raise MemoryError("incomplete relation transaction; explicit recovery is required")
        if _sha(snapshot(root, bundle)) != expected_sha256:
            raise MemoryError("stale relation endpoints; reread and redraft")
        originals = {locator: path.read_bytes() for locator, path in paths.items()}
        for locator, raw in originals.items():
            if _sha(raw) != bundle["base_sha256"][locator]:
                raise MemoryError("stale relation endpoint base hash")
        current = {note["path"]: note for note in list_notes(root)}
        original_errors = {_json(item) for item in _check_notes(list(current.values()))["errors"]}
        from memory_markdown import parse_note, _validate_note_path
        for locator, path in paths.items():
            proposed = parse_note(bundle["notes"][locator], path)
            _validate_note_path(root, path, proposed)
            old = current[locator]
            # This writer changes relations only; all other approved note content stays byte-semantic stable.
            ignored = {"path", "sha256", "related", "relationships", "relations"}
            if {k: v for k, v in old.items() if k not in ignored} != {k: v for k, v in proposed.items() if k not in ignored}:
                raise MemoryError("relation transaction cannot change note claims or other metadata")
            current[locator] = proposed
        if any(_json(item) not in original_errors for item in _check_notes(list(current.values()))["errors"]):
            raise MemoryError("proposed transaction introduces unresolved relation drift")
        record = {"schema_version": "v0.1.0", "approval_sha256": approved_content_sha256,
                  "before": {key: value.decode("utf-8") for key, value in originals.items()},
                  "after_sha256": {key: _sha(value.encode("utf-8")) for key, value in bundle["notes"].items()}}
        _atomic_write(journal, _json(record))
        try:
            for locator, path in paths.items():
                if path.read_bytes() != originals[locator]:
                    raise MemoryError("relation endpoint changed externally before commit")
                _atomic_write(path, bundle["notes"][locator].encode("utf-8"))
            if snapshot(root, bundle) != snapshot(root, bundle, desired=True):
                raise MemoryError("relation transaction readback differs")
        except Exception as exc:
            conflicts = []
            for locator, path in paths.items():
                observed = path.read_bytes()
                if observed == originals[locator]:
                    continue
                if _sha(observed) != record["after_sha256"][locator]:
                    conflicts.append(locator)
                    continue
                try:
                    _atomic_write(path, originals[locator])
                except OSError:
                    conflicts.append(locator)
            if conflicts:
                raise MemoryError("relation rollback needs explicit recovery; external edits were preserved") from exc
            journal.unlink()  # One explicitly named technical journal, never a bulk cleanup.
            raise
        journal.unlink()
    return {"status": "written", "target_sha256": _sha(snapshot(root, bundle, desired=True))}


def recover(root: str | Path, *, journal_sha256: str, confirm: bool = False) -> dict:
    root = _memory_root(root)
    journal = _relative(root, JOURNAL, "relation journal")
    with _context_lock(root, 5.0):
        with journal.open("rb") as stream:
            raw = stream.read(262145)
        if not confirm or _sha(raw) != journal_sha256:
            raise MemoryError("explicit confirmation and current journal SHA-256 are required")
        if len(raw) > 262144:
            raise MemoryError("invalid relation recovery journal")
        record = json.loads(raw)
        if (not isinstance(record, dict) or set(record) != {"schema_version", "approval_sha256", "before", "after_sha256"}
                or record["schema_version"] != "v0.1.0"
                or not isinstance(record["approval_sha256"], str) or len(record["approval_sha256"]) != 64):
            raise MemoryError("invalid relation recovery journal")
        bundle = {"notes": record["before"], "base_sha256": record["after_sha256"]}
        paths = _paths(root, bundle)
        for locator, path in paths.items():
            from memory_markdown import parse_note, _validate_note_path
            _validate_note_path(root, path, parse_note(record["before"][locator], path))
            if _sha(path.read_bytes()) not in {_sha(record["before"][locator].encode("utf-8")), record["after_sha256"][locator]}:
                raise MemoryError("external edit conflicts with recovery; no files were changed")
        for locator, path in paths.items():
            _atomic_write(path, record["before"][locator].encode("utf-8"))
        journal.unlink()
    return {"status": "recovered", "action": "restored_pre_transaction_notes"}


def repair_proposal(root: str | Path, *, mei_root: str | Path = ROOT) -> dict:
    report = check(root)
    if report["status"] == "ok":
        return {"status": "no_drift", "proposals": []}
    repairable = [item for item in report["errors"] if item["kind"] == "missing_reciprocal"]
    if len(repairable) != len(report["errors"]):
        return {"status": "review_required", "errors": report["errors"], "proposals": []}
    # One pair per review prevents an unbounded implicit rewrite of the graph.
    first = repairable[0]
    proposal = draft_relation(root, first["source"], first["target"], first["type"], mei_root=mei_root)
    return {"status": "proposal_created", "proposals": [proposal], "remaining_errors": report["errors"][1:]}


def traverse(root: str | Path, source: str, *, depth: int = 1) -> dict:
    if not 1 <= depth <= 4:
        raise MemoryError("relation traversal depth must be 1..4")
    from memory_markdown import read_memory
    notes = {note["memory_id"]: note for note in read_memory(root)["notes"]}
    if source not in notes:
        raise MemoryError("source must be an active, retrievable note")
    seen, frontier, edges = {source}, {source}, []
    for _ in range(depth):
        following = set()
        for current in sorted(frontier):
            related, typed = _edges(notes[current])
            for kind, target in [("related", value) for value in sorted(related)] + sorted(typed):
                if target in notes:
                    edges.append({"source": current, "type": kind, "target": target})
                    if target not in seen:
                        following.add(target)
        seen |= following
        if len(seen) > 100:
            raise MemoryError("relation traversal exceeds 100-note cap")
        frontier = following
    return {"status": "ok", "nodes": sorted(seen), "edges": edges}


def _cli() -> int:
    from memory import main
    import sys
    return main(["relations", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(_cli())
