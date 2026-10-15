"""Project Mei Markdown memory into the configured local Obsidian vault namespace."""

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

import tomllib
import yaml

from _common import ROOT
from checkpoint_continuity import _LockTimeout, _context_lock
from memory_markdown import (
    INDEX_NAME,
    MEMORY_ID_PATTERN,
    MemoryError,
    _assert_no_reparse,
    _assert_private,
    _relative,
    _split_note,
    doctor as markdown_doctor,
    parse_note,
    read_memory,
)

DEFAULT_NAMESPACE = "Mei Memory"
MANIFEST_NAME = ".mei-vault.json"
MANIFEST_SCHEMA = "v0.1.0"
MAX_MANIFEST_BYTES = 65_536
MAX_VAULT_NOTE_BYTES = 65_536
PATH_PATTERN = re.compile(r"^views/[a-z0-9][a-z0-9-]{2,79}\.md$")


def _unique_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MemoryError(f"manifest duplicate key: {key}")
        result[key] = value
    return result


def _safe_vault_relative(root: Path, locator: str, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise MemoryError(f"{label}: path must be a non-empty relative path")
    normalized = locator.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise MemoryError(f"{label}: absolute path is not allowed")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts) or ".obsidian" in parts:
        raise MemoryError(f"{label}: unsafe vault path")
    candidate = root.joinpath(*parts)
    _assert_no_reparse(root)
    _assert_no_reparse(candidate)
    try:
        if not candidate.resolve(strict=False).is_relative_to(root.resolve(strict=False)):
            raise MemoryError(f"{label}: path escapes the vault")
    except ValueError as exc:
        raise MemoryError(f"{label}: path escapes the vault") from exc
    return candidate


def _namespace_parts(namespace: str) -> list[str]:
    if not isinstance(namespace, str) or not namespace.strip():
        raise MemoryError("obsidian namespace must be non-empty")
    normalized = namespace.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if any(part in ("", ".", "..", ".obsidian") for part in parts):
        raise MemoryError("unsafe Obsidian namespace; it must stay inside the vault and outside .obsidian")
    return parts


def resolve_vault(
    vault_root: str | Path | None = None,
    namespace: str | None = None,
    *,
    config_path: str | Path | None = None,
) -> tuple[Path, Path, str]:
    config: dict[str, Any] = {}
    if config_path is None:
        config_path = ROOT / "local" / "paths.toml"
    config_file = Path(config_path)
    if vault_root is None and config_file.is_file():
        try:
            with config_file.open("rb") as stream:
                config = tomllib.load(stream).get("paths", {})
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise MemoryError(f"cannot read local paths: {exc}") from exc
        vault_root = config.get("obsidian_vault")
    if vault_root is None:
        raise MemoryError("obsidian_vault is not configured")
    vault = Path(vault_root).expanduser()
    if not vault.is_absolute():
        raise MemoryError("obsidian_vault must be an absolute path")
    vault = vault.resolve()
    if not vault.is_dir():
        raise MemoryError(f"configured Obsidian vault is missing: {vault}")
    _assert_no_reparse(vault)
    namespace = namespace or config.get("obsidian_memory_dir") or DEFAULT_NAMESPACE
    parts = _namespace_parts(namespace)
    namespace_path = vault.joinpath(*parts)
    _assert_no_reparse(namespace_path)
    if namespace_path.resolve(strict=False) == (vault / ".obsidian").resolve(strict=False):
        raise MemoryError("Mei namespace cannot be .obsidian")
    return vault, namespace_path, "/".join(parts)


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


def _read_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise MemoryError(f"{path}: manifest exceeds {MAX_MANIFEST_BYTES} byte hard cap")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json)
    except (OSError, UnicodeError, json.JSONDecodeError, MemoryError) as exc:
        raise MemoryError(f"{path}: invalid manifest: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != MANIFEST_SCHEMA or not isinstance(value.get("notes"), dict):
        raise MemoryError(f"{path}: unsupported manifest shape")
    for memory_id, entry in value["notes"].items():
        if not MEMORY_ID_PATTERN.fullmatch(memory_id) or not isinstance(entry, dict):
            raise MemoryError(f"{path}: invalid manifest note entry")
        locator = entry.get("path")
        if not isinstance(locator, str) or not PATH_PATTERN.fullmatch(locator):
            raise MemoryError(f"{path}: manifest path is outside views: {locator}")
    return value, hashlib.sha256(raw).hexdigest()


def _source_digest(notes: list[dict[str, Any]]) -> str:
    payload = "\n".join(f"{note['memory_id']}:{note['sha256']}" for note in sorted(notes, key=lambda item: item["memory_id"]))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _frontmatter_text(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip()


def _projection(note: dict[str, Any]) -> str:
    frontmatter = {
        "schema_version": note["schema_version"],
        "memory_id": note["memory_id"],
        "category": note["category"],
        "status": note["status"],
        "scope": note["scope"],
        "source": note["source"],
        "mei_generated": True,
        "source_path": note["path"],
        "source_sha256": note["sha256"],
    }
    for key in ("review_triggers", "updated_at"):
        if key in note:
            frontmatter[key] = note[key]
    if note.get("relationships"):
        frontmatter["relationships"] = [f"[[{target}]]" for target in note["relationships"]]
    return f"---\n{_frontmatter_text(frontmatter)}\n---\n{note['body']}"


def _projection_index(notes: list[dict[str, Any]], source_generation: str | int, source_digest: str) -> str:
    lines = [
        "---",
        "schema_version: v0.1.0",
        "mei_generated: true",
        f"source_generation: {json.dumps(source_generation, ensure_ascii=False)}",
        f"source_sha256: {source_digest}",
        "---",
        "# Mei Memory",
        "",
    ]
    for note in sorted(notes, key=lambda item: (item["category"], item["memory_id"])):
        lines.append(f"- [[views/{note['memory_id']}]] `{note['category']}` {note['scope']}")
    return "\n".join(lines) + "\n"


def _plan(
    memory_root: Path,
    vault: Path,
    namespace_path: Path,
    source_generation: str | int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source_doctor = markdown_doctor(memory_root)
    if source_doctor.get("status") != "ok":
        raise MemoryError(f"Markdown memory invalid: {source_doctor.get('error', source_doctor)}")
    memory = read_memory(memory_root)
    notes = memory["notes"]
    source_digest = _source_digest(notes)
    rendered = {note["memory_id"]: _projection(note) for note in notes}
    views = namespace_path / "views"
    old_manifest, manifest_hash = _read_manifest(namespace_path / MANIFEST_NAME)
    old_notes = old_manifest.get("notes", {}) if old_manifest else {}
    changes = {"added": [], "updated": [], "unchanged": [], "drifted": [], "skipped": []}
    page_paths: dict[str, Path] = {}
    for memory_id, content in rendered.items():
        page = _safe_vault_relative(vault, f"{namespace_path.relative_to(vault).as_posix()}/views/{memory_id}.md", "projection")
        page_paths[memory_id] = page
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        previous = old_notes.get(memory_id)
        if not page.exists():
            changes["added"].append(memory_id)
        else:
            current_hash = hashlib.sha256(page.read_bytes()).hexdigest()
            if current_hash == expected_hash:
                changes["unchanged"].append(memory_id)
            elif not previous or current_hash != previous.get("rendered_sha256"):
                changes["drifted"].append(memory_id)
            else:
                changes["updated"].append(memory_id)
    for memory_id, previous in old_notes.items():
        if memory_id not in rendered:
            changes["skipped"].append(memory_id)
    desired_index = _projection_index(notes, source_generation, source_digest)
    index_path = _safe_vault_relative(vault, f"{namespace_path.relative_to(vault).as_posix()}/index.md", "projection index")
    desired_index_hash = hashlib.sha256(desired_index.encode("utf-8")).hexdigest()
    index_change = "added"
    if index_path.exists():
        current_index_hash = hashlib.sha256(index_path.read_bytes()).hexdigest()
        if current_index_hash == desired_index_hash:
            index_change = "unchanged"
        elif not old_manifest or current_index_hash != old_manifest.get("index_sha256"):
            changes["drifted"].append("index.md")
            index_change = "drifted"
        else:
            index_change = "updated"
    manifest_notes = dict(old_notes)
    for note in notes:
        content = rendered[note["memory_id"]]
        manifest_notes[note["memory_id"]] = {
            "path": f"views/{note['memory_id']}.md",
            "source_path": note["path"],
            "source_sha256": note["sha256"],
            "rendered_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
    desired_manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "namespace": namespace_path.relative_to(vault).as_posix(),
        "source_generation": source_generation,
        "source_sha256": source_digest,
        "index_sha256": desired_index_hash,
        "notes": dict(sorted(manifest_notes.items())),
    }
    return {
        "memory": memory,
        "rendered": rendered,
        "page_paths": page_paths,
        "index_path": index_path,
        "desired_index": desired_index,
        "desired_manifest": desired_manifest,
        "manifest_hash": manifest_hash,
        "changes": changes,
        "index_change": index_change,
    }, old_manifest


def _report(plan: dict[str, Any], *, status: str, committed: bool = False) -> dict[str, Any]:
    changes = plan["changes"]
    return {
        "status": status,
        "committed": committed,
        "source_generation": plan["desired_manifest"]["source_generation"],
        "source_sha256": plan["desired_manifest"]["source_sha256"],
        "added": changes["added"] + (["index.md"] if plan["index_change"] == "added" else []),
        "updated": changes["updated"] + (["index.md"] if plan["index_change"] == "updated" else []),
        "unchanged": changes["unchanged"] + (["index.md"] if plan["index_change"] == "unchanged" else []),
        "drifted": changes["drifted"],
        "skipped": changes["skipped"],
    }


def sync_obsidian(
    vault_root: str | Path | None,
    source_generation: str | int,
    mode: str = "sync",
    *,
    memory_root: str | Path | None = None,
    namespace: str | None = None,
    lock_timeout: float = 5.0,
) -> dict[str, Any]:
    if mode not in {"sync", "check"}:
        raise MemoryError("mode must be sync or check")
    if isinstance(source_generation, bool) or not isinstance(source_generation, (str, int)) or not str(source_generation):
        raise MemoryError("source_generation must be a non-empty string or integer")
    memory_root = Path(memory_root or ROOT / "memory" / "markdown").expanduser().resolve()
    vault, namespace_path, namespace_name = resolve_vault(vault_root, namespace)
    if not namespace_path.exists() and mode == "check":
        return {"status": "missing", "namespace": namespace_name, "required_action": "sync"}
    def make_plan() -> dict[str, Any]:
        plan, _ = _plan(memory_root, vault, namespace_path, source_generation)
        plan["namespace"] = namespace_name
        return plan
    if mode == "check":
        plan = make_plan()
        result = _report(plan, status="drifted" if plan["changes"]["drifted"] else "ok")
        result["namespace"] = namespace_name
        return result
    try:
        with _context_lock(vault, lock_timeout):
            namespace_path.mkdir(parents=True, exist_ok=True)
            (namespace_path / "views").mkdir(parents=True, exist_ok=True)
            (namespace_path / "inbox").mkdir(parents=True, exist_ok=True)
            plan = make_plan()
            if plan["changes"]["drifted"]:
                result = _report(plan, status="drifted")
                result["namespace"] = namespace_name
                result["required_action"] = "review_drift"
                return result
            for memory_id in plan["changes"]["added"] + plan["changes"]["updated"]:
                _atomic_write(plan["page_paths"][memory_id], plan["rendered"][memory_id].encode("utf-8"))
            if plan["index_change"] in {"added", "updated"}:
                _atomic_write(plan["index_path"], plan["desired_index"].encode("utf-8"))
            manifest_raw = json.dumps(plan["desired_manifest"], ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
            manifest_path = namespace_path / MANIFEST_NAME
            if not manifest_path.exists() or manifest_path.read_bytes() != manifest_raw:
                _atomic_write(manifest_path, manifest_raw)
            result = _report(plan, status="synced", committed=True)
            result["namespace"] = namespace_name
            return result
    except _LockTimeout:
        return {"status": "lock_timeout", "namespace": namespace_name, "required_action": "retry"}


def check_vault(
    vault_root: str | Path | None,
    *,
    namespace: str | None = None,
    memory_root: str | Path | None = None,
) -> dict[str, Any]:
    try:
        vault, namespace_path, namespace_name = resolve_vault(vault_root, namespace)
        if not namespace_path.is_dir():
            return {"status": "missing", "namespace": namespace_name, "errors": ["Mei namespace is missing"]}
        manifest, _ = _read_manifest(namespace_path / MANIFEST_NAME)
        errors: list[str] = []
        if manifest is None:
            errors.append("manifest is missing")
            manifest = {"notes": {}}
        index_path = namespace_path / "index.md"
        expected_index_hash = manifest.get("index_sha256")
        if not index_path.is_file():
            errors.append(f"{index_path}: generated index is missing")
        elif not isinstance(expected_index_hash, str) or hashlib.sha256(index_path.read_bytes()).hexdigest() != expected_index_hash:
            errors.append(f"{index_path}: generated index drifted from manifest")
        for memory_id, entry in manifest.get("notes", {}).items():
            locator = entry.get("path")
            try:
                path = _safe_vault_relative(vault, f"{namespace_path.relative_to(vault).as_posix()}/{locator}", "manifest note")
            except MemoryError as exc:
                errors.append(str(exc))
                continue
            if not path.is_file():
                errors.append(f"{path}: projected note is missing")
                continue
            raw = path.read_bytes()
            if len(raw) > MAX_VAULT_NOTE_BYTES:
                errors.append(f"{path}: projected note exceeds {MAX_VAULT_NOTE_BYTES} bytes")
                continue
            if hashlib.sha256(raw).hexdigest() != entry.get("rendered_sha256"):
                errors.append(f"{path}: projected note drifted from manifest")
            try:
                frontmatter, body = _split_note(raw.decode("utf-8"), path)
                if frontmatter.get("mei_generated") is not True or frontmatter.get("memory_id") != memory_id:
                    errors.append(f"{path}: generated marker or memory_id is invalid")
                _assert_private(raw.decode("utf-8"), path)
            except (MemoryError, UnicodeError) as exc:
                errors.append(str(exc))
        inbox = namespace_path / "inbox"
        if inbox.is_dir():
            for path in sorted(inbox.glob("*.md")):
                raw = path.read_bytes()
                if len(raw) > MAX_VAULT_NOTE_BYTES:
                    errors.append(f"{path}: inbox note exceeds {MAX_VAULT_NOTE_BYTES} bytes")
                    continue
                try:
                    parse_note(raw.decode("utf-8"), path)
                except (MemoryError, UnicodeError) as exc:
                    errors.append(str(exc))
        if memory_root is not None:
            try:
                source = read_memory(memory_root)
                by_id = {note["memory_id"]: note for note in source["notes"]}
                for memory_id, entry in manifest.get("notes", {}).items():
                    if memory_id in by_id and entry.get("source_sha256") != by_id[memory_id]["sha256"]:
                        errors.append(f"{memory_id}: projected source hash differs from Markdown memory")
            except MemoryError as exc:
                errors.append(str(exc))
        return {"status": "invalid" if errors else "ok", "namespace": namespace_name, "errors": errors, "notes": len(manifest.get("notes", {}))}
    except (MemoryError, OSError, UnicodeError) as exc:
        return {"status": "invalid", "errors": [str(exc)]}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sync", "check"])
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--namespace")
    parser.add_argument("--memory-root", type=Path, default=ROOT / "memory" / "markdown")
    parser.add_argument("--source-generation", default="manual")
    parser.add_argument("--lock-timeout", type=float, default=5.0)
    args = parser.parse_args()
    try:
        if args.command == "sync":
            result = sync_obsidian(args.vault, args.source_generation, memory_root=args.memory_root, namespace=args.namespace, lock_timeout=args.lock_timeout)
        else:
            result = check_vault(args.vault, namespace=args.namespace, memory_root=args.memory_root)
    except (MemoryError, OSError, UnicodeError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "synced"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
