"""Read, validate, and atomically update Mei's Markdown memory notes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from _common import ROOT, SECRET_PATTERNS

MEMORY_SCHEMA = "memory-note.schema.json"
INDEX_NAME = "MEMORY.md"
MAX_INDEX_BYTES = 65_536
MAX_INDEX_LINES = 400
MAX_DIGEST_BYTES = 16_384
MAX_NOTE_BYTES = 32_768
MEMORY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
LINK_PATTERN = re.compile(r"\[\[([^]|#]+)(?:#[^]|]+)?(?:\|[^]]+)?\]\]")
FORBIDDEN_PARTS = {"", ".", ".."}


class MemoryError(ValueError):
    """A deterministic failure at the Markdown memory boundary."""


class _UniqueLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise MemoryError("frontmatter keys must be strings")
        if key in mapping:
            raise MemoryError(f"frontmatter duplicate key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _schema(root: Path = ROOT) -> dict:
    try:
        return json.loads((root / "schemas" / MEMORY_SCHEMA).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryError(f"cannot load {MEMORY_SCHEMA}: {exc}") from exc


def _validate_frontmatter(value: dict[str, Any], source: Path, root: Path = ROOT) -> None:
    errors = sorted(
        Draft202012Validator(_schema(root), format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(map(str, error.path)) or "<root>"
        raise MemoryError(f"{source}: {MEMORY_SCHEMA} {location}: {error.message}")


def _assert_private(text: str, source: Path) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise MemoryError(f"{source}: possible secret or private key")


def _assert_no_reparse(path: Path) -> None:
    try:
        import stat

        absolute = Path(os.path.abspath(path))
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current /= part
            info = current.lstat()
            if current.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise MemoryError(f"{path}: symlink or junction component is not allowed")
    except FileNotFoundError:
        return
    except OSError as exc:
        raise MemoryError(f"{path}: cannot inspect path: {exc}") from exc


def _relative(root: Path, locator: str, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise MemoryError(f"{label}: path must be a non-empty relative path")
    normalized = locator.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise MemoryError(f"{label}: absolute path is not allowed")
    parts = normalized.split("/")
    if any(part in FORBIDDEN_PARTS for part in parts):
        raise MemoryError(f"{label}: path contains an unsafe component")
    candidate = root.joinpath(*parts)
    _assert_no_reparse(root)
    _assert_no_reparse(candidate)
    try:
        if not candidate.resolve(strict=False).is_relative_to(root.resolve(strict=False)):
            raise MemoryError(f"{label}: path escapes the memory root")
    except ValueError as exc:
        raise MemoryError(f"{label}: path escapes the memory root") from exc
    return candidate


def _split_note(raw: str, source: Path) -> tuple[dict[str, Any], str]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not raw.startswith("---\n"):
        raise MemoryError(f"{source}: frontmatter must start with ---")
    marker = raw.find("\n---\n", 4)
    if marker < 0:
        raise MemoryError(f"{source}: frontmatter closing marker is missing")
    try:
        frontmatter = yaml.load(raw[4:marker], Loader=_UniqueLoader)
    except (yaml.YAMLError, MemoryError) as exc:
        raise MemoryError(f"{source}: invalid frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise MemoryError(f"{source}: frontmatter must be a YAML object")
    body = raw[marker + len("\n---\n") :]
    return frontmatter, body


def parse_note(raw: str, source: Path, root: Path = ROOT) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_NOTE_BYTES:
        raise MemoryError(f"{source}: note exceeds {MAX_NOTE_BYTES} byte hard cap")
    frontmatter, body = _split_note(raw, source)
    _validate_frontmatter(frontmatter, source, root)
    _assert_private(raw, source)
    note = dict(frontmatter)
    note["body"] = body
    return note


def render_note(note: dict[str, Any], root: Path = ROOT) -> str:
    body = note.get("body")
    if not isinstance(body, str):
        raise MemoryError("note body must be a string")
    frontmatter = {key: value for key, value in note.items() if key != "body"}
    _validate_frontmatter(frontmatter, Path("<note>"), root)
    raw_frontmatter = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip()
    raw = f"---\n{raw_frontmatter}\n---\n{body}"
    if len(raw.encode("utf-8")) > MAX_NOTE_BYTES:
        raise MemoryError(f"note exceeds {MAX_NOTE_BYTES} byte hard cap")
    _assert_private(raw, Path("<note>"))
    return raw


def _note_locator(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _validate_note_path(root: Path, path: Path, note: dict[str, Any]) -> None:
    relative = path.relative_to(root)
    if len(relative.parts) != 2 or relative.suffix.casefold() != ".md":
        raise MemoryError(f"{path}: note must be <category>/<memory_id>.md")
    category, filename = relative.parts
    if filename != f"{note['memory_id']}.md" or category != note["category"]:
        raise MemoryError(f"{path}: path does not match note category and memory_id")


def read_note(root: str | Path, locator: str, *, schema_root: str | Path | None = None) -> dict[str, Any]:
    memory_root = Path(root).expanduser().resolve()
    path = _relative(memory_root, locator, "note")
    if not path.is_file():
        raise MemoryError(f"{path}: note is missing")
    raw = path.read_text(encoding="utf-8")
    note = parse_note(raw, path, Path(schema_root or ROOT))
    _validate_note_path(memory_root, path, note)
    note["path"] = _note_locator(memory_root, path)
    note["sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return note


def read_index(root: str | Path) -> dict[str, Any]:
    memory_root = Path(root).expanduser().resolve()
    path = memory_root / INDEX_NAME
    if not path.is_file():
        raise MemoryError(f"{path}: memory index is missing")
    raw = path.read_bytes()
    if len(raw) > MAX_INDEX_BYTES:
        raise MemoryError(f"{path}: index exceeds {MAX_INDEX_BYTES} byte hard cap")
    text = raw.decode("utf-8")
    if len(text.splitlines()) > MAX_INDEX_LINES:
        raise MemoryError(f"{path}: index exceeds {MAX_INDEX_LINES} line hard cap")
    _assert_private(text, path)
    ids = []
    for value in LINK_PATTERN.findall(text):
        memory_id = value.strip()
        if not MEMORY_ID_PATTERN.fullmatch(memory_id):
            raise MemoryError(f"{path}: invalid index wikilink target: {memory_id}")
        if memory_id not in ids:
            ids.append(memory_id)
    return {"path": INDEX_NAME, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "ids": ids, "text": text}


def list_notes(root: str | Path, *, schema_root: str | Path | None = None) -> list[dict[str, Any]]:
    memory_root = Path(root).expanduser().resolve()
    if not memory_root.is_dir():
        raise MemoryError(f"{memory_root}: memory root is missing")
    notes: list[dict[str, Any]] = []
    for path in sorted(memory_root.glob("*/*.md")):
        notes.append(read_note(memory_root, path.relative_to(memory_root).as_posix(), schema_root=schema_root))
    ids = [note["memory_id"] for note in notes]
    if len(ids) != len(set(ids)):
        raise MemoryError("duplicate memory_id in Markdown notes")
    return notes


def read_memory(root: str | Path, relevant_ids: set[str] | None = None, *, schema_root: str | Path | None = None) -> dict[str, Any]:
    index = read_index(root)
    memory_root = Path(root).expanduser().resolve()
    if relevant_ids is None:
        notes = list_notes(memory_root, schema_root=schema_root)
    else:
        notes = []
        for memory_id in sorted(relevant_ids):
            if not MEMORY_ID_PATTERN.fullmatch(memory_id):
                raise MemoryError(f"invalid relevant memory_id: {memory_id}")
            matches = sorted(memory_root.glob(f"*/{memory_id}.md"))
            if len(matches) != 1:
                raise MemoryError(f"memory index target is missing or duplicated: {memory_id}")
            notes.append(read_note(memory_root, matches[0].relative_to(memory_root).as_posix(), schema_root=schema_root))
    return {"index": index, "notes": notes}


def doctor(root: str | Path, *, schema_root: str | Path | None = None) -> dict[str, Any]:
    try:
        memory = read_memory(root, schema_root=schema_root)
        note_ids = {note["memory_id"] for note in memory["notes"]}
        missing = [memory_id for memory_id in memory["index"]["ids"] if memory_id not in note_ids]
        if missing:
            raise MemoryError(f"memory index points to missing notes: {', '.join(missing)}")
        targets = set()
        for note in memory["notes"]:
            body_links = set(LINK_PATTERN.findall(note["body"]))
            relationships = set(note.get("relationships", []))
            for target in body_links | relationships:
                target = target.strip()
                if not MEMORY_ID_PATTERN.fullmatch(target) or target not in note_ids:
                    raise MemoryError(f"{note['path']}: unsafe or missing link target: {target}")
                targets.add(target)
        return {
            "status": "ok",
            "index": {key: value for key, value in memory["index"].items() if key != "text"},
            "notes": len(memory["notes"]),
            "linked_targets": len(targets),
        }
    except (MemoryError, OSError, UnicodeError) as exc:
        return {"status": "invalid", "error": str(exc)}


def _atomic_write(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_note(
    root: str | Path,
    locator: str,
    content: str,
    *,
    expected_sha256: str | None = None,
    schema_root: str | Path | None = None,
) -> dict[str, Any]:
    memory_root = Path(root).expanduser().resolve()
    path = _relative(memory_root, locator, "note")
    note = parse_note(content, path, Path(schema_root or ROOT))
    _validate_note_path(memory_root, path, note)
    path.parent.mkdir(parents=True, exist_ok=True)
    from checkpoint_continuity import _LockTimeout, _context_lock

    raw = content.encode("utf-8")
    try:
        with _context_lock(memory_root, 5.0):
            current = path.read_bytes() if path.exists() else None
            current_sha256 = hashlib.sha256(current).hexdigest() if current is not None else None
            if expected_sha256 is not None and current_sha256 != expected_sha256:
                raise MemoryError(f"{path}: stale note hash; reread before writing")
            _assert_no_reparse(path.parent)
            _atomic_write(path, raw)
            if path.read_bytes() != raw:
                raise MemoryError(f"{path}: readback differs from the written note")
    except _LockTimeout as exc:
        raise MemoryError("Markdown memory root is locked; retry") from exc
    return {"status": "written", "path": locator.replace("\\", "/"), "sha256": hashlib.sha256(raw).hexdigest()}


def memory_digest(root: str | Path) -> dict[str, Any]:
    """Return the bounded index for host startup; never raises (fail-open)."""
    try:
        index = read_index(root)
    except (MemoryError, OSError, UnicodeError) as exc:
        return {"status": "unavailable", "error": str(exc)}
    text = index["text"]
    truncated = len(text.encode("utf-8")) > MAX_DIGEST_BYTES
    if truncated:
        text = text.encode("utf-8")[:MAX_DIGEST_BYTES].decode("utf-8", errors="ignore")
    return {
        "status": "ok",
        "path": INDEX_NAME,
        "index_sha256": index["sha256"],
        "ids": index["ids"],
        "truncated": truncated,
        "text": text,
        "retrieval": "Open only relevant notes with scripts/memory_markdown.py read --id <memory_id>.",
    }


def initialize_memory(root: str | Path) -> list[str]:
    """Create the empty Markdown memory layout without replacing any file."""
    memory_root = Path(root).expanduser().resolve()
    memory_root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for category in ("profile", "preferences", "domain", "procedural", "project", "feedback"):
        directory = memory_root / category
        if not directory.exists():
            directory.mkdir()
            created.append(f"{category}/")
    index = memory_root / INDEX_NAME
    if not index.exists():
        index.write_text("# Mei Memory\n\n<!-- Bounded index; detail notes live under category folders. -->\n", encoding="utf-8")
        created.append(INDEX_NAME)
    return created


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", required=True, type=Path)
    subparsers.add_parser("init", parents=[common])
    doctor_parser = subparsers.add_parser("doctor", parents=[common])
    read_parser = subparsers.add_parser("read", parents=[common])
    read_parser.add_argument("--id", action="append", dest="ids")
    write_parser = subparsers.add_parser("write", parents=[common])
    write_parser.add_argument("--path", required=True)
    write_parser.add_argument("--content-file", required=True, type=Path)
    write_parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = {"status": "initialized", "created": initialize_memory(args.root)}
        elif args.command == "doctor":
            result = doctor(args.root)
        elif args.command == "read":
            result = read_memory(args.root, set(args.ids) if args.ids else None)
            result["notes"] = [{key: value for key, value in note.items() if key != "sha256"} for note in result["notes"]]
        else:
            result = write_note(args.root, args.path, args.content_file.read_text(encoding="utf-8"), expected_sha256=args.expected_sha256)
    except (MemoryError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"invalid", "error"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
