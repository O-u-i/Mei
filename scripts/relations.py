"""Reciprocal Markdown memory relation operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import yaml

from _common import ROOT
from checkpoint_continuity import _LockTimeout, _context_lock
from memory_markdown import MEMORY_ID_PATTERN, MemoryError, _assert_no_reparse, _relative, parse_note, list_notes

RELATION_TYPES = {"supersedes", "superseded_by", "depends_on", "dependency_of", "supports", "supported_by", "contradicts", "contradicted_by"}
INVERSE = {
    "supersedes": "superseded_by", "superseded_by": "supersedes",
    "depends_on": "dependency_of", "dependency_of": "depends_on",
    "supports": "supported_by", "supported_by": "supports",
    "contradicts": "contradicted_by", "contradicted_by": "contradicts",
}


def _atomic(path: Path, raw: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _find(root: Path, memory_id: str) -> tuple[Path, dict[str, Any], str]:
    if not MEMORY_ID_PATTERN.fullmatch(memory_id):
        raise MemoryError(f"invalid memory_id: {memory_id}")
    matches = sorted(root.glob(f"*/{memory_id}.md"))
    if len(matches) != 1:
        raise MemoryError(f"memory_id must resolve to one canonical note: {memory_id}")
    path = matches[0]
    raw = path.read_text(encoding="utf-8")
    parsed = parse_note(raw, path)
    body = parsed.pop("body")
    parsed.pop("path", None)
    parsed.pop("sha256", None)
    frontmatter = parsed
    return path, frontmatter, body


def _render(frontmatter: dict[str, Any], body: str) -> bytes:
    return ("---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n" + body).encode("utf-8")


def _edges(note: dict[str, Any]) -> tuple[set[str], set[tuple[str, str]]]:
    related = note.get("related", note.get("relationships", [])) or []
    typed = note.get("relations", []) or []
    return set(related), {(item.get("type"), item.get("target")) for item in typed if isinstance(item, dict)}


def _set_edges(note: dict[str, Any], related: set[str], typed: set[tuple[str, str]]) -> None:
    note["related"] = sorted(related)
    note["relations"] = [{"type": relation_type, "target": target} for relation_type, target in sorted(typed)]
    # Preserve compatibility with pre-v0.2 notes while they are upgraded.
    if "relationships" in note:
        note["relationships"] = sorted(related)


def _write_pair(changes: list[tuple[Path, bytes, bytes]]) -> None:
    originals = {path: old for path, old, _ in changes}
    try:
        for path, _, raw in changes:
            _atomic(path, raw)
        for path, _, raw in changes:
            if path.read_bytes() != raw:
                raise MemoryError(f"{path}: relation write readback differs")
    except Exception:
        for path, old in originals.items():
            try:
                _atomic(path, old)
            except OSError:
                pass
        raise


def add(root: str | Path, source: str, target: str, relation_type: str | None = None, *, lock_timeout: float = 5.0) -> dict[str, Any]:
    root = Path(root).resolve()
    if source == target:
        raise MemoryError("self-relations are not allowed")
    if relation_type is not None and relation_type not in RELATION_TYPES:
        raise MemoryError(f"invalid relation type: {relation_type}")
    with _context_lock(root, lock_timeout):
        source_path, source_note, source_body = _find(root, source)
        target_path, target_note, target_body = _find(root, target)
        source_related, source_typed = _edges(source_note)
        target_related, target_typed = _edges(target_note)
        if relation_type is None:
            source_related.add(target)
            target_related.add(source)
        else:
            source_typed.add((relation_type, target))
            target_typed.add((INVERSE[relation_type], source))
        _set_edges(source_note, source_related, source_typed)
        _set_edges(target_note, target_related, target_typed)
        _write_pair([(source_path, source_path.read_bytes(), _render(source_note, source_body)), (target_path, target_path.read_bytes(), _render(target_note, target_body))])
    return {"status": "added", "source": source, "target": target, "type": relation_type, "inverse": INVERSE.get(relation_type)}


def remove(root: str | Path, source: str, target: str, relation_type: str | None = None, *, lock_timeout: float = 5.0) -> dict[str, Any]:
    root = Path(root).resolve()
    if relation_type is not None and relation_type not in RELATION_TYPES:
        raise MemoryError(f"invalid relation type: {relation_type}")
    with _context_lock(root, lock_timeout):
        source_path, source_note, source_body = _find(root, source)
        target_path, target_note, target_body = _find(root, target)
        source_related, source_typed = _edges(source_note)
        target_related, target_typed = _edges(target_note)
        if relation_type is None:
            source_related.discard(target)
            target_related.discard(source)
        else:
            source_typed.discard((relation_type, target))
            target_typed.discard((INVERSE[relation_type], source))
        _set_edges(source_note, source_related, source_typed)
        _set_edges(target_note, target_related, target_typed)
        _write_pair([(source_path, source_path.read_bytes(), _render(source_note, source_body)), (target_path, target_path.read_bytes(), _render(target_note, target_body))])
    return {"status": "removed", "source": source, "target": target, "type": relation_type}


def check(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    notes = {note["memory_id"]: note for note in list_notes(root)}
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for memory_id, note in notes.items():
        related, typed = _edges(note)
        for target in sorted(related):
            edge = (memory_id, target, None)
            if target not in notes:
                errors.append({"kind": "dangling", "source": memory_id, "target": target})
            elif memory_id not in _edges(notes[target])[0]:
                errors.append({"kind": "missing_reciprocal", "source": memory_id, "target": target})
            if edge in seen:
                errors.append({"kind": "duplicate_edge", "source": memory_id, "target": target})
            seen.add(edge)
        for relation_type, target in sorted(typed):
            if relation_type not in RELATION_TYPES:
                errors.append({"kind": "invalid_type", "source": memory_id, "target": target, "type": relation_type})
                continue
            if target not in notes:
                errors.append({"kind": "dangling", "source": memory_id, "target": target, "type": relation_type})
            elif (INVERSE[relation_type], memory_id) not in _edges(notes[target])[1]:
                errors.append({"kind": "missing_reciprocal", "source": memory_id, "target": target, "type": relation_type})
            edge = (memory_id, target, relation_type)
            if edge in seen:
                errors.append({"kind": "duplicate_edge", "source": memory_id, "target": target, "type": relation_type})
            seen.add(edge)
    return {"status": "ok" if not errors else "drift", "errors": errors}


def repair_proposal(root: str | Path) -> dict[str, Any]:
    report = check(root)
    proposal_id = f"repair-relations-{hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:12]}"
    proposal_root = Path(root).resolve().parent / "proposals"
    proposal_root.mkdir(parents=True, exist_ok=True)
    path = proposal_root / f"{proposal_id}.md"
    body = "## Reason\n\nRelation drift was detected; no notes were changed.\n\n## Proposed Change\n\n"
    body += "\n".join(f"- `{json.dumps(item, sort_keys=True)}`" for item in report["errors"]) or "- No drift."
    body += "\n\n## Diff\n\nRepair requires explicit approval and should use `relations add/remove`.\n\n## Unresolved\n\nHuman review required.\n\n## Approval History\n"
    frontmatter = {"proposal_id": proposal_id, "status": "draft", "target_locator": "memory/markdown", "target_base_sha256": None, "suggested_content_sha256": hashlib.sha256(body.encode()).hexdigest(), "scope": "relation repair", "source": {"kind": "relation_check", "locator": str(Path(root).resolve())}, "approval": {"explicit": False, "approved_content_sha256": None, "approved_at": None}}
    _atomic(path, ("---\n" + yaml.safe_dump(frontmatter, sort_keys=False).rstrip() + "\n---\n" + body).encode())
    return {"status": "proposal_created", "path": str(path), "proposal_id": proposal_id, "errors": report["errors"]}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("add", "remove", "check", "repair"))
    parser.add_argument("source", nargs="?")
    parser.add_argument("target", nargs="?")
    parser.add_argument("relation_type", nargs="?")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--proposal", action="store_true")
    args = parser.parse_args()
    try:
        if args.command in {"add", "remove"}:
            result = (add if args.command == "add" else remove)(args.root, args.source or "", args.target or "", args.relation_type)
        elif args.command == "check":
            result = check(args.root)
        else:
            result = repair_proposal(args.root)
    except (MemoryError, OSError, _LockTimeout) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"added", "removed", "ok", "proposal_created"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
