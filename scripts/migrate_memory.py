"""Migrate legacy JSON memory records into the Markdown canonical vault.

The legacy files are copied byte-for-byte into ``memory/legacy-json`` and are
never used as a runtime memory source.  Markdown files created by this module
are recorded in a YAML manifest so migration can be verified and rolled back
without touching the evidence copies or technical JSON stores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from _common import ROOT, SECRET_PATTERNS

SCHEMA_VERSION = "v0.2.0"
MANIFEST_VERSION = "v0.2.0"
MEMORY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
CATEGORIES = {"profile", "preferences", "domain", "procedural", "project", "feedback"}
RELATION_TYPES = {"supersedes", "superseded_by", "depends_on", "dependency_of", "supports", "supported_by", "contradicts", "contradicted_by"}
EVIDENCE_TYPES = {"current_file", "prior_memory", "trajectory", "user_decision", "test_result"}
INVERSE = {
    "supersedes": "superseded_by", "superseded_by": "supersedes",
    "depends_on": "dependency_of", "dependency_of": "depends_on",
    "supports": "supported_by", "supported_by": "supports",
    "contradicts": "contradicted_by", "contradicted_by": "contradicts",
}


class MigrationError(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assert_safe(root: Path, path: Path) -> None:
    root = root.resolve()
    path = path.resolve(strict=False)
    if not path.is_relative_to(root):
        raise MigrationError(f"path escapes migration root: {path}")
    for part in [root, *path.parents]:
        if part == root.parent:
            break
        if part.exists() and part.is_symlink():
            raise MigrationError(f"reparse or symlink path is not allowed: {part}")


def _atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _yaml_load(raw: bytes, source: Path) -> Any:
    class Loader(yaml.SafeLoader):
        pass

    def unique(loader: Loader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise MigrationError(f"{source}: duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique)
    try:
        return yaml.load(raw.decode("utf-8"), Loader=Loader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise MigrationError(f"{source}: invalid YAML: {exc}") from exc


def _source_records(root: Path) -> list[tuple[Path, str]]:
    records: list[tuple[Path, str]] = []
    for group in ("candidates", "canonical", "archive"):
        base = root / "memory" / group
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.json")):
            _assert_safe(root, path)
            records.append((path, group))
    return records


def _target_for(root: Path, source: Path, group: str, data: dict[str, Any]) -> tuple[Path, str]:
    memory_id = data.get("id", source.stem)
    category = data.get("category")
    if not isinstance(memory_id, str) or not MEMORY_ID.fullmatch(memory_id):
        raise MigrationError(f"{source}: invalid memory id")
    if group == "canonical":
        if category not in CATEGORIES:
            raise MigrationError(f"{source}: invalid category")
        return root / "memory" / "markdown" / category / f"{memory_id}.md", "canonical"
    if group == "archive":
        # Archived records retain their category when known, with procedural as
        # a deterministic fallback for old records that omitted it.
        category = category if category in CATEGORIES else "procedural"
        return root / "memory" / "markdown" / category / f"{memory_id}.md", "archived"
    # Candidate content is unapproved. Keep it as a proposal, never a
    # canonical note with a candidate status.
    return root / "memory" / "proposals" / f"{memory_id}.md", "proposal"


def _relations(data: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    related = data.get("related", data.get("relationships", []))
    if related is None:
        related = []
    if not isinstance(related, list) or any(not isinstance(value, str) for value in related):
        raise MigrationError("related/relationships must be a list of ids")
    if len(related) != len(set(related)):
        raise MigrationError("duplicate related edge")
    typed = data.get("relations", [])
    if typed is None:
        typed = []
    if not isinstance(typed, list) or any(not isinstance(item, dict) for item in typed):
        raise MigrationError("relations must be a list of objects")
    output: list[dict[str, str]] = []
    for item in typed:
        relation_type, target = item.get("type"), item.get("target")
        if relation_type not in RELATION_TYPES or not isinstance(target, str) or not MEMORY_ID.fullmatch(target):
            raise MigrationError(f"invalid typed relation: {item!r}")
        output.append({"type": relation_type, "target": target})
    if len({(item["type"], item["target"]) for item in output}) != len(output):
        raise MigrationError("duplicate typed relation")
    return sorted(set(related)), output


def _provenance(data: dict[str, Any], source: Path) -> list[dict[str, str]]:
    value = data.get("provenance")
    if value is None:
        # Missing provenance is explicitly review-required; do not invent an
        # evidence source merely because a legacy record exists.
        return [{"source": str(source), "locator": str(source), "evidence_type": "prior_memory", "verified_at": None}]
    if not isinstance(value, list):
        raise MigrationError(f"{source}: provenance must be a list")
    result = []
    for item in value:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("source", "locator", "evidence_type")):
            raise MigrationError(f"{source}: invalid provenance entry")
        if item["evidence_type"] not in EVIDENCE_TYPES:
            raise MigrationError(f"{source}: invalid provenance evidence_type")
        result.append({"source": item["source"], "locator": item["locator"], "evidence_type": item["evidence_type"], "verified_at": item.get("verified_at")})
    return result


def _note(root: Path, source: Path, data: dict[str, Any], group: str, target: Path, status: str, *, migration_at: str | None = None) -> bytes:
    memory_id = data.get("id", source.stem)
    category = data.get("category") if data.get("category") in CATEGORIES else "procedural"
    if group == "canonical" and data.get("category") not in CATEGORIES:
        raise MigrationError(f"{source}: canonical record category is required")
    related, typed = _relations(data)
    missing_provenance = not data.get("provenance")
    frontmatter: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "memory_id": memory_id,
        "category": category,
        "status": "active" if status == "canonical" else "archived",
        "scope": str(data.get("scope") or "legacy memory; migration review required"),
        "source": f"legacy JSON: {source.relative_to(root).as_posix()}",
        "source_format": "json",
        "provenance": _provenance(data, source),
        "review_due_at": data.get("review_due_at"),
        "review_triggers": data.get("review_triggers", []),
        "references": data.get("references", []),
        "conflicts": data.get("conflicts", []),
        "related": related,
        "relations": typed,
        "updated_at": data.get("updated_at") or migration_at or _now(),
        "migrated_from": source.relative_to(root).as_posix(),
        "migrated_at": migration_at or _now(),
    }
    if missing_provenance:
        frontmatter["review_required"] = True
    claim = data.get("claim", "")
    notes = data.get("notes", "")
    provenance_lines = [f"- {item['evidence_type']}: {item['source']} ({item['locator']})" for item in frontmatter["provenance"]]
    body = "## For future agent\n\n"
    body += f"This migrated {status} memory applies to {frontmatter['scope']}.\n\n"
    body += "## Claim\n\n" + str(claim).strip() + "\n\n"
    body += "## Evidence\n\n" + ("\n".join(provenance_lines) or "Migration source unavailable; review required.") + "\n"
    if notes:
        body += "\n## History\n\n" + str(notes).strip() + "\n"
    if frontmatter["conflicts"]:
        body += "\n## Conflicts\n\n" + "\n".join(f"- {item}" for item in frontmatter["conflicts"]) + "\n"
    return ("---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n" + body).encode("utf-8")


def _proposal(root: Path, source: Path, data: dict[str, Any]) -> bytes:
    """Render an unapproved candidate as the Markdown proposal contract."""
    proposal_id = str(data.get("id", source.stem))
    body = "## Reason\n\nMigrated from an unapproved legacy candidate; review before applying.\n\n"
    body += "## Proposed Change\n\n" + str(data.get("claim", "")).strip() + "\n\n"
    body += "## Diff\n\nLegacy candidate content is preserved in `memory/legacy-json`.\n\n"
    body += "## Unresolved\n\nProvenance and conflicts require curator review.\n\n## Approval History\n"
    frontmatter = {
        "proposal_id": proposal_id,
        "status": "draft",
        "target_locator": f"memory/markdown/{data.get('category', 'procedural')}/{proposal_id}.md",
        "target_base_sha256": None,
        "suggested_content_sha256": _sha(body.encode("utf-8")),
        "scope": str(data.get("scope") or "legacy candidate"),
        "source": {"kind": "legacy_json", "locator": source.relative_to(root).as_posix()},
        "approval": {"explicit": False, "approved_content_sha256": None, "approved_at": None},
    }
    return ("---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n" + body).encode("utf-8")


def plan(root: Path = ROOT, *, manifest_path: Path | None = None, migration_at: str | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest_path = Path(manifest_path or root / "memory/migration/manifest.yaml")
    entries: list[dict[str, Any]] = []
    ids: dict[str, Path] = {}
    errors: list[str] = []
    for source, group in _source_records(root):
        raw = source.read_bytes()
        entry: dict[str, Any] = {"source": source.relative_to(root).as_posix(), "source_sha256": _sha(raw), "source_bytes": len(raw), "group": group}
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise MigrationError("record must be a JSON object")
            target, kind = _target_for(root, source, group, data)
            memory_id = data.get("id", source.stem)
            if memory_id in ids:
                raise MigrationError(f"duplicate memory id also found at {ids[memory_id].relative_to(root).as_posix()}")
            ids[memory_id] = source
            rendered = _note(root, source, data, group, target, "canonical" if kind == "canonical" else "archived", migration_at=migration_at) if kind != "proposal" else _proposal(root, source, data)
            entry.update(id=memory_id, target=target.relative_to(root).as_posix(), kind=kind, markdown_sha256=_sha(rendered), action="create")
        except (json.JSONDecodeError, UnicodeDecodeError, MigrationError, KeyError) as exc:
            entry.update(action="blocked", error=str(exc))
            errors.append(f"{source.relative_to(root)}: {exc}")
        entries.append(entry)
    return {"schema_version": MANIFEST_VERSION, "generated_at": _now(), "root": str(root), "manifest": str(manifest_path), "entries": entries, "errors": errors, "status": "invalid" if errors else "ok"}


def migrate(root: Path = ROOT, *, manifest_path: Path | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest_path = Path(manifest_path or root / "memory/migration/manifest.yaml")
    previous: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            loaded = _yaml_load(manifest_path.read_bytes(), manifest_path)
            previous = {item.get("source"): item for item in loaded.get("entries", []) if isinstance(item, dict)}
        except (OSError, MigrationError):
            previous = {}
    generated_at = _now()
    result = plan(root, manifest_path=manifest_path, migration_at=generated_at)
    result["generated_at"] = generated_at
    legacy_root = root / "memory/legacy-json"
    created: list[str] = []
    for entry in result["entries"]:
        source = root / entry["source"]
        legacy = legacy_root / entry["source"].removeprefix("memory/")
        _assert_safe(root, legacy)
        raw = source.read_bytes()
        if not legacy.exists():
            _atomic(legacy, raw)
        elif legacy.read_bytes() != raw:
            entry["action"] = "blocked"
            entry["error"] = "legacy evidence copy differs; refusing overwrite"
            continue
        if entry.get("action") != "create":
            continue
        target = root / entry["target"]
        prior = previous.get(entry["source"])
        if target.is_file() and prior and prior.get("source_sha256") == entry.get("source_sha256") and prior.get("markdown_sha256") == _sha(target.read_bytes()):
            entry["markdown_sha256"] = prior["markdown_sha256"]
            entry["action"] = "unchanged"
            entry["legacy"] = legacy.relative_to(root).as_posix()
            continue
        target_bytes = _proposal(root, source, json.loads(raw.decode("utf-8"))) if entry["kind"] == "proposal" else _note(root, source, json.loads(raw.decode("utf-8")), entry["group"], target, "canonical" if entry["kind"] == "canonical" else "archived", migration_at=result["generated_at"])
        if target.exists():
            if _sha(target.read_bytes()) != _sha(target_bytes):
                entry["action"] = "blocked"
                entry["error"] = "target exists and differs; refusing overwrite"
                continue
            entry["action"] = "unchanged"
        else:
            _atomic(target, target_bytes)
            created.append(entry["target"])
            entry["action"] = "created"
        entry["legacy"] = legacy.relative_to(root).as_posix()
    result["created_markdown"] = created
    result["status"] = "invalid" if any(item.get("action") == "blocked" for item in result["entries"]) else "migrated"
    _atomic(manifest_path, yaml.safe_dump(result, allow_unicode=True, sort_keys=False).encode("utf-8"))
    return result


def verify(manifest_path: Path, root: Path = ROOT) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest = _yaml_load(Path(manifest_path).read_bytes(), Path(manifest_path))
    errors: list[str] = []
    for entry in manifest.get("entries", []):
        source, legacy = root / entry["source"], root / entry.get("legacy", "memory/legacy-json/" + entry["source"].removeprefix("memory/"))
        if not source.is_file() or _sha(source.read_bytes()) != entry.get("source_sha256"):
            errors.append(f"{entry['source']}: source hash mismatch")
        if not legacy.is_file() or _sha(legacy.read_bytes()) != entry.get("source_sha256"):
            errors.append(f"{entry['source']}: legacy hash mismatch")
        if entry.get("target") and entry.get("action") in {"created", "unchanged"}:
            target = root / entry["target"]
            if not target.is_file() or _sha(target.read_bytes()) != entry.get("markdown_sha256"):
                errors.append(f"{entry['target']}: Markdown hash mismatch")
    return {"status": "invalid" if errors else "ok", "checked": len(manifest.get("entries", [])), "errors": errors}


def rollback(manifest_path: Path, root: Path = ROOT) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest = _yaml_load(Path(manifest_path).read_bytes(), Path(manifest_path))
    removed: list[str] = []
    skipped: list[str] = []
    for entry in manifest.get("entries", []):
        if entry.get("action") != "created" or not entry.get("target"):
            continue
        target = root / entry["target"]
        if target.is_file() and _sha(target.read_bytes()) == entry.get("markdown_sha256"):
            target.unlink()
            removed.append(entry["target"])
        elif target.exists():
            skipped.append(entry["target"])
    return {"status": "rolled_back", "removed": removed, "skipped": skipped, "legacy_preserved": True}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "migrate", "verify", "rollback"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    path = args.manifest or args.root / "memory/migration/manifest.yaml"
    try:
        result = plan(args.root, manifest_path=path) if args.command == "plan" else migrate(args.root, manifest_path=path) if args.command == "migrate" else verify(path, args.root) if args.command == "verify" else rollback(path, args.root)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "migrated", "rolled_back"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
