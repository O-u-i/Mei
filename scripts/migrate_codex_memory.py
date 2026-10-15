"""Inventory, copy, and verify Codex host memories into Mei without moving CODEX_HOME.

Sources are only read. Eligible files are copied byte-for-byte into
``memory/markdown/archive/codex-<host>/`` (outside the note glob) and recorded in a
machine-local manifest with source/target hashes, classes, and privacy results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import ROOT, SECRET_PATTERNS
from memory_markdown import _assert_no_reparse

MANIFEST_SCHEMA = "v0.1.0"
MAX_SOURCE_BYTES = 2_000_000
RUNTIME_NAMES = {"auth.json", "cap_sid", "config.toml", "history.jsonl", "installation_id"}
RUNTIME_SUFFIXES = (".sqlite", ".sqlite-shm", ".sqlite-wal", ".lock", ".guard")
COPY_CLASSES = {"durable_index", "summary", "raw_episode"}


def classify(relative: str) -> str:
    parts = relative.split("/")
    name = parts[-1]
    if parts[0] == ".git":
        return "vcs_excluded"
    if name in RUNTIME_NAMES or name.endswith(RUNTIME_SUFFIXES):
        return "runtime_excluded"
    if parts[0] == "skills":
        return "executable_skill"
    if parts[0] == "extensions":
        return "extension"
    if relative == "MEMORY.md":
        return "durable_index"
    if relative == "memory_summary.md":
        return "summary"
    if relative == "raw_memories.md" or parts[0] == "rollout_summaries":
        return "raw_episode"
    return "unclassified"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _privacy(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "not_utf8"
    return "flagged" if any(pattern.search(text) for pattern in SECRET_PATTERNS) else "clean"


def inventory(source: str | Path) -> list[dict[str, Any]]:
    """Classify every file under one memories root; content is hashed, never returned."""
    source = Path(source).resolve()
    _assert_no_reparse(source)
    if not source.is_dir():
        raise FileNotFoundError(f"memory source is missing: {source}")
    entries = []
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = path.relative_to(source).as_posix()
        kind = classify(relative)
        entry: dict[str, Any] = {"path": relative, "class": kind, "bytes": path.stat().st_size}
        if kind in COPY_CLASSES:
            if entry["bytes"] > MAX_SOURCE_BYTES:
                entry.update(source_sha256=None, privacy="oversize", action="blocked")
            else:
                raw = path.read_bytes()
                entry["source_sha256"] = _sha256(raw)
                entry["privacy"] = _privacy(raw)
                entry["action"] = "copy" if entry["privacy"] == "clean" else "blocked"
        else:
            entry["action"] = "retain_in_host"
        entries.append(entry)
    return entries


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _target_root(mei_root: Path, host: str) -> Path:
    if host not in {"gui", "tui"}:
        raise ValueError("host must be gui or tui")
    return mei_root / "memory" / "markdown" / "archive" / f"codex-{host}"


def migrate(sources: dict[str, Path], mei_root: Path = ROOT, manifest_path: Path | None = None) -> dict[str, Any]:
    """Copy clean eligible files; never overwrite a differing target or touch a source."""
    mei_root = Path(mei_root).resolve()
    manifest_path = Path(manifest_path or mei_root / "local" / "codex-memory-migration.json")
    hosts: dict[str, Any] = {}
    totals = {"copied": 0, "unchanged": 0, "blocked": 0, "conflict": 0, "retained": 0}
    for host, source in sorted(sources.items()):
        source = Path(source).resolve()
        target_root = _target_root(mei_root, host)
        _assert_no_reparse(target_root)
        entries = inventory(source)
        for entry in entries:
            if entry["action"] != "copy":
                totals["blocked" if entry["action"] == "blocked" else "retained"] += 1
                continue
            raw = (source / entry["path"]).read_bytes()
            if _sha256(raw) != entry["source_sha256"]:
                entry["action"] = "conflict"
                entry["reason"] = "source changed during migration"
                totals["conflict"] += 1
                continue
            target = target_root / entry["path"]
            entry["target"] = target.relative_to(mei_root).as_posix()
            if target.exists():
                current = _sha256(target.read_bytes())
                if current == entry["source_sha256"]:
                    entry.update(action="unchanged", target_sha256=current)
                    totals["unchanged"] += 1
                    continue
                entry.update(action="conflict", target_sha256=current, reason="target differs; reconcile explicitly")
                totals["conflict"] += 1
                continue
            _atomic_write(target, raw)
            entry.update(action="copied", target_sha256=_sha256(target.read_bytes()))
            totals["copied"] += 1
        hosts[host] = {"source": str(source), "target_root": target_root.relative_to(mei_root).as_posix(), "entries": entries}
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hosts": hosts,
        "totals": totals,
        "rollback": "Set [host] memory_injection = false in local/paths.toml; sources and CODEX_HOME are untouched.",
    }
    _atomic_write(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"status": "conflict" if totals["conflict"] else "migrated", "manifest": str(manifest_path), "totals": totals}


def verify(manifest_path: Path, mei_root: Path = ROOT) -> dict[str, Any]:
    """Dual-read: re-hash every recorded source and target and report differences."""
    mei_root = Path(mei_root).resolve()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    errors: list[str] = []
    checked = 0
    for host, record in manifest["hosts"].items():
        source_root = Path(record["source"])
        for entry in record["entries"]:
            if entry["action"] not in {"copied", "unchanged"}:
                continue
            checked += 1
            source = source_root / entry["path"]
            target = mei_root / entry["target"]
            if not target.is_file() or _sha256(target.read_bytes()) != entry["target_sha256"]:
                errors.append(f"{host}:{entry['path']}: target missing or changed")
            if not source.is_file():
                errors.append(f"{host}:{entry['path']}: source missing")
            elif _sha256(source.read_bytes()) != entry["source_sha256"]:
                errors.append(f"{host}:{entry['path']}: source changed since migration (re-run to refresh)")
            if entry["source_sha256"] != entry["target_sha256"]:
                errors.append(f"{host}:{entry['path']}: source/target hash mismatch")
    excluded = [
        f"{host}:{entry['path']}"
        for host, record in manifest["hosts"].items()
        for entry in record["entries"]
        if entry["class"] in {"runtime_excluded", "vcs_excluded", "executable_skill", "extension"} and "target" in entry
    ]
    errors.extend(f"{item}: excluded class was copied" for item in excluded)
    return {"status": "invalid" if errors else "ok", "checked": checked, "errors": errors[:50]}


def _sources_from_config(mei_root: Path) -> dict[str, Path]:
    import tomllib

    with (mei_root / "local" / "paths.toml").open("rb") as stream:
        paths = tomllib.load(stream)["paths"]
    return {host: Path(paths[f"codex_memory_source_{host}"]) for host in ("gui", "tui") if paths.get(f"codex_memory_source_{host}")}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inventory", "migrate", "verify"])
    parser.add_argument("--mei-root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    mei_root = args.mei_root.resolve()
    manifest = args.manifest or mei_root / "local" / "codex-memory-migration.json"
    try:
        if args.command == "inventory":
            result: dict[str, Any] = {}
            for host, source in _sources_from_config(mei_root).items():
                summary: dict[str, dict[str, int]] = {}
                for entry in inventory(source):
                    bucket = summary.setdefault(entry["class"], {"files": 0, "bytes": 0})
                    bucket["files"] += 1
                    bucket["bytes"] += entry["bytes"]
                    if entry["action"] == "blocked":
                        summary.setdefault("blocked", {"files": 0, "bytes": 0})["files"] += 1
                result[host] = summary
        elif args.command == "migrate":
            result = migrate(_sources_from_config(mei_root), mei_root, manifest)
        else:
            result = verify(manifest, mei_root)
    except (OSError, ValueError, KeyError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "ok") in {"ok", "migrated"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
