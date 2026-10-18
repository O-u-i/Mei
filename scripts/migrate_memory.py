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

from _common import ROOT, SECRET_PATTERNS, publish_new_file
from memory_markdown import (_assert_no_reparse, _relative, _split_note, CATEGORIES as NOTE_CATEGORIES,
                             parse_note, render_note, initialize_memory, rebuild_index, list_notes, resolve_memory_root)

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
    _assert_no_reparse(root)
    _assert_no_reparse(path)
    if not path.resolve(strict=False).is_relative_to(root.resolve()):
        raise MigrationError(f"path escapes migration root: {path}")


_UNSET = object()


def _atomic(path: Path, raw: bytes, *, expected_sha256: Any = _UNSET) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if expected_sha256 is not _UNSET:
            observed = _sha(path.read_bytes()) if path.exists() else None
            if observed != expected_sha256:
                raise MigrationError("migration target changed before atomic write")
        if expected_sha256 is None:
            publish_new_file(tmp, path)  # Never replace a concurrent file.
        else:
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
        return []
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


def _note(root: Path, source: Path, data: dict[str, Any], group: str, target: Path, status: str, *, migration_at: str | None = None, historical_archive: dict | None = None) -> bytes:
    memory_id = data.get("id", source.stem)
    category = data.get("category") if data.get("category") in CATEGORIES else "procedural"
    if group == "canonical" and data.get("category") not in CATEGORIES:
        raise MigrationError(f"{source}: canonical record category is required")
    related, typed = _relations(data)
    missing_provenance = not data.get("provenance") or not data.get("scope")
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
    if missing_provenance or historical_archive:
        frontmatter["review_required"] = True
    claim = data.get("claim", "")
    notes = data.get("notes", "")
    provenance_lines = [f"- {item['evidence_type']}: {item['source']} ({item['locator']})" for item in frontmatter["provenance"]]
    body = "## For future agent\n\n"
    body += f"This migrated {status} memory applies to {frontmatter['scope']}.\n\n"
    body += "## Claim\n\n" + str(claim).strip() + "\n\n"
    body += "## Evidence\n\n" + ("\n".join(provenance_lines) or "Original provenance unavailable; migration source is recorded in metadata. Review required.") + "\n"
    if notes:
        body += "\n## History\n\n" + str(notes).strip() + "\n"
    if historical_archive:
        evidence = "memory/legacy-json/" + historical_archive["source"].removeprefix("memory/")
        body += ("\n## Migration decision\n\n"
                 "One unchanged memory ID; the archived version is retained byte-for-byte as evidence, not merged.\n"
                 f"- Archived source: {historical_archive['source']}\n"
                 f"- Evidence: {evidence}\n"
                 f"- SHA-256: {historical_archive['sha256']}\n"
                 f"- Reviewed disposition: {historical_archive['reason']}\n"
                 "Currentness was not revalidated by migration; review_required remains true.\n")
    if frontmatter["conflicts"]:
        body += "\n## Conflicts\n\n" + "\n".join(f"- {item}" for item in frontmatter["conflicts"]) + "\n"
    return ("---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n" + body).encode("utf-8")


def _proposal(root: Path, source: Path, data: dict, *, migration_at: str) -> dict:
    from review_proposals import _content_sha, _render_card
    memory_id = data.get("id", source.stem)
    category = data.get("category", "procedural")
    if category not in CATEGORIES:
        raise MigrationError("candidate category is invalid")
    content = _note(root, source, data, "canonical", root / "memory/markdown" / category / f"{memory_id}.md",
                    "canonical", migration_at=migration_at).decode("utf-8")
    proposal = {"schema_version": "v0.2.0", "proposal_id": memory_id, "status": "draft",
                "target_type": "personal_markdown", "target_locator": f"memory/markdown/{category}/{memory_id}.md",
                "scope": data.get("scope") or "legacy candidate; review required",
                "source": {"kind": "legacy_json", "locator": source.relative_to(root).as_posix(), "sha256": _sha(source.read_bytes())},
                "reason": "Migrated unapproved candidate; provenance and conflicts require human review.",
                "suggested_content": content, "suggested_content_sha256": _content_sha(content),
                "target_base_sha256": None, "target_exists": False, "card_locator": f"review/{memory_id}.md",
                "card_sha256": None, "preview": None, "approval": None, "receipt": None,
                "history": [{"event": "migrated_candidate", "at": migration_at}],
                "created_at": migration_at, "updated_at": migration_at}
    proposal["card_sha256"] = _sha(_render_card(proposal).encode("utf-8"))
    return proposal


def _proposal_bytes(value: dict) -> bytes:
    from review_proposals import _frontmatter
    from _common import validate_schema, check_private_content
    validate_schema(value, "memory-proposal.schema.json")
    check_private_content(value)
    return ("---\n" + _frontmatter(value) + "\n---\n## Reason\n\nMigrated unapproved candidate.\n\n"
            "## Proposed Change\n\nSee suggested_content in metadata.\n\n## Diff\n\nPending preview.\n\n"
            "## Unresolved\n\nHuman review required.\n\n## Approval History\n\nMigrated, not approved.\n").encode("utf-8")


def _load_json(raw: bytes) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise MigrationError(f"duplicate JSON key: {key}")
            value[key] = item
        return value
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise MigrationError("record must be a JSON object")
    return value


def _root(root: Path) -> Path:
    root = Path(root).absolute()
    _assert_safe(root, root)
    if root.name == "markdown" and root.parent.name == "memory":
        raise MigrationError("--root expects the Mei checkout, not memory/markdown")
    if not root.is_dir():
        raise MigrationError("Mei migration root is missing")
    if resolve_memory_root(root) != (root / "memory/markdown").resolve():
        raise MigrationError("migration requires configured memory_root to be this checkout's memory/markdown; review the selected locator first")
    return root


def _manifest_path(root: Path, path: Path | None) -> Path:
    path = Path(path or root / "memory/migration/manifest.yaml").absolute()
    _assert_safe(root / "memory/migration", path)
    return path


def _entry_paths(root: Path, entry: dict) -> tuple[Path, Path, Path]:
    group = entry.get("group")
    locator = entry.get("source", "")
    prefix = "memory/markdown/" if group == "markdown_upgrade" else f"memory/{group}/"
    if group not in {"canonical", "candidates", "archive", "markdown_upgrade"} or not locator.startswith(prefix):
        raise MigrationError("manifest source is outside the migration allowlist")
    source = _relative(root, locator, "migration source")
    target = _relative(root, entry["target"], "migration target")
    if entry.get("kind") == "evidence":
        evidence = root / "memory/legacy-json" / locator.removeprefix("memory/")
        if group != "archive" or target != evidence or entry.get("created_by_manifest"):
            raise MigrationError("historical evidence target must preserve its original archive locator")
        _assert_safe(root, evidence)
        return source, target, evidence
    expected_target, _ = _target_for(root, source, group, {"id": entry["id"], "category": target.parent.name}) if group != "markdown_upgrade" else (source, "upgrade")
    if target != expected_target:
        raise MigrationError("manifest target is not deterministic for its source/id")
    if group == "markdown_upgrade":
        if target.parent.name not in CATEGORIES or target.name != entry["id"] + ".md":
            raise MigrationError("invalid Markdown upgrade target")
        evidence = root / "memory/migration/backups" / (entry["source_sha256"] + ".md")
    else:
        evidence = root / "memory/legacy-json" / locator.removeprefix("memory/")
    _assert_safe(root, evidence)
    return source, target, evidence


def _read_manifest(root: Path, path: Path) -> dict:
    _assert_safe(root / "memory/migration", path)
    value = _yaml_load(path.read_bytes(), path)
    if not isinstance(value, dict) or value.get("schema_version") != MANIFEST_VERSION or not isinstance(value.get("entries"), list):
        raise MigrationError("invalid migration manifest")
    if value.get("root") != str(root):
        raise MigrationError("manifest is bound to a different Mei checkout")
    for entry in value["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", entry["source_sha256"]):
            raise MigrationError("invalid manifest entry/hash")
        if entry.get("target"):
            _entry_paths(root, entry)
    return value


def _render_entry(root: Path, entry: dict, migration_at: str) -> tuple[bytes, dict | None]:
    source = _relative(root, entry["source"], "migration source")
    raw = source.read_bytes()
    if entry["group"] == "markdown_upgrade":
        return render_note(parse_note(raw.decode("utf-8"), source)).encode("utf-8"), None
    data = _load_json(raw)
    if entry["kind"] == "proposal":
        proposal = _proposal(root, source, data, migration_at=migration_at)
        return _proposal_bytes(proposal), proposal
    target = root / entry["target"]
    result = _note(root, source, data, entry["group"], target,
                   "canonical" if entry["kind"] == "canonical" else "archived", migration_at=migration_at,
                   historical_archive=entry.get("historical_archive"))
    parse_note(result.decode("utf-8"), target)
    return result, None


def _dispositions(root: Path, path: Path | None, expected_sha256: str | None = None) -> tuple[dict, dict | None]:
    """Read explicit, pinned same-claim version decisions; never infer approval."""
    if path is None:
        if expected_sha256 is not None:
            raise MigrationError("approved disposition hash requires --dispositions")
        return {}, None
    path = Path(path).absolute()
    _assert_safe(root, path)
    raw = path.read_bytes()
    digest = _sha(raw)
    if expected_sha256 is not None and digest != expected_sha256:
        raise MigrationError("reviewed dispositions changed; approval hash mismatch")
    if len(raw) > 131072:
        raise MigrationError("dispositions exceed the byte cap")
    value = _load_json(raw)
    if set(value) != {"schema_version", "pairs"} or value["schema_version"] != "v1.0.0" or not isinstance(value["pairs"], list) or not value["pairs"]:
        raise MigrationError("invalid dispositions document")
    pairs = {}
    for pair in value["pairs"]:
        if not isinstance(pair, dict) or set(pair) != {"id", "canonical", "archive", "disposition", "reason"}:
            raise MigrationError("invalid historical version decision")
        memory_id = pair["id"]
        if not isinstance(memory_id, str) or not MEMORY_ID.fullmatch(memory_id) or memory_id in pairs:
            raise MigrationError("invalid or duplicate disposition id")
        if pair["disposition"] != "canonical_with_archive_evidence_review_required" or not isinstance(pair["reason"], str) or not 1 <= len(pair["reason"].strip()) <= 2000:
            raise MigrationError("unsupported disposition or missing reason")
        records = []
        for group in ("canonical", "archive"):
            pin = pair[group]
            if not isinstance(pin, dict) or set(pin) != {"source", "sha256"} or not isinstance(pin["source"], str) or not pin["source"].startswith(f"memory/{group}/") or not isinstance(pin["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", pin["sha256"]):
                raise MigrationError("invalid disposition source pin")
            source = _relative(root, pin["source"], "disposition source")
            source_raw = source.read_bytes()
            if source.suffix != ".json" or _sha(source_raw) != pin["sha256"]:
                raise MigrationError(f"{pin['source']}: disposition source drift")
            data = _load_json(source_raw)
            if data.get("id") != memory_id:
                raise MigrationError("disposition cannot change a source ID")
            _target_for(root, source, group, data)
            records.append(data)
        first, second = records
        if first.get("status") != "canonical" or second.get("status") != "archived":
            raise MigrationError("disposition requires a canonical/archive historical pair")
        if any(first.get(key) != second.get(key) for key in ("claim", "scope", "category", "conflicts")) or _relations(first) != _relations(second):
            raise MigrationError("disposition requires identical claims, scope, category, conflicts and relations; review divergent facts separately")
        pairs[memory_id] = pair
    return pairs, {"source": path.relative_to(root).as_posix(), "sha256": digest}


def _history_pin(pair: dict) -> dict:
    return dict(pair["archive"], reason=pair["reason"])


def plan(root: Path = ROOT, *, manifest_path: Path | None = None, migration_at: str | None = None, dispositions_path: Path | None = None) -> dict:
    root = _root(root)
    manifest_path = _manifest_path(root, manifest_path)
    pairs, disposition_receipt = _dispositions(root, dispositions_path)
    prior = _read_manifest(root, manifest_path) if manifest_path.exists() else None
    if prior and prior.get("dispositions") != disposition_receipt:
        raise MigrationError("manifest disposition binding changed; refusing automatic adoption")
    stamp = migration_at or (prior["generated_at"] if prior else _now())
    entries, errors, ids = [], [], {}
    sources = _source_records(root)
    if pairs:
        approved_sources = {pair[group]["source"] for pair in pairs.values() for group in ("canonical", "archive")}
        if {path.relative_to(root).as_posix() for path, _ in sources} != approved_sources:
            raise MigrationError("legacy source inventory differs from reviewed dispositions")
    for category in sorted(CATEGORIES):
        for path in sorted((root / "memory/markdown" / category).glob("*.md")):
            _assert_safe(root, path)
            raw = path.read_text(encoding="utf-8")
            metadata, _ = _split_note(raw, path)
            if metadata.get("schema_version") != SCHEMA_VERSION:
                if pairs:
                    raise MigrationError("Markdown upgrades require separate review from historical-version dispositions")
                sources.append((path, "markdown_upgrade"))
            else:
                parsed = parse_note(raw, path)
                ids[parsed["memory_id"]] = path
    for source, group in sources:
        raw = source.read_bytes()
        entry = {"source": source.relative_to(root).as_posix(), "source_sha256": _sha(raw),
                 "source_bytes": len(raw), "group": group}
        try:
            if len(raw) > 131072:
                raise MigrationError("legacy record exceeds the byte cap")
            if any(pattern.search(raw.decode("utf-8")) for pattern in SECRET_PATTERNS):
                raise MigrationError("possible secret in migration source")
            if group == "markdown_upgrade":
                data = parse_note(raw.decode("utf-8"), source)
                memory_id, target, kind = data["memory_id"], source, "upgrade"
            else:
                data = _load_json(raw)
                memory_id = data.get("id", source.stem)
                target, kind = _target_for(root, source, group, data)
            pair = pairs.get(memory_id)
            if pair and group in {"canonical", "archive"} and entry["source_sha256"] != pair[group]["sha256"]:
                raise MigrationError("source drifted from reviewed pins during preflight")
            if pair and group == "archive" and pair["archive"]["source"] == entry["source"]:
                target = root / "memory/legacy-json" / entry["source"].removeprefix("memory/")
                _assert_safe(root, target)
                if target.exists() and target.read_bytes() != raw:
                    raise MigrationError("legacy evidence differs; refusing overwrite")
                entry.update(id=memory_id, target=target.relative_to(root).as_posix(), kind="evidence",
                             action="unchanged" if target.exists() else "preserve", created_by_manifest=False)
                entries.append(entry)
                continue
            if pair and group == "canonical" and pair["canonical"]["source"] == entry["source"]:
                entry["historical_archive"] = _history_pin(pair)
                evidence = root / "memory/legacy-json" / entry["source"].removeprefix("memory/")
                _assert_safe(root, evidence)
                if evidence.exists() and evidence.read_bytes() != raw:
                    raise MigrationError("legacy evidence differs; refusing overwrite")
            # An unchanged manifest-owned target is the same migrated record, not a new duplicate.
            prior_entry = next((item for item in (prior or {}).get("entries", []) if item["source"] == entry["source"]), None)
            if memory_id in ids and not (prior_entry and ids[memory_id] == target):
                raise MigrationError(f"duplicate memory id: {memory_id}")
            ids[memory_id] = target
            entry.update(id=memory_id, target=target.relative_to(root).as_posix(), kind=kind, action="upgrade" if kind == "upgrade" else "create")
            rendered, proposal = _render_entry(root, entry, stamp)
            entry["markdown_sha256"] = _sha(rendered)
            if target.exists() and group != "markdown_upgrade":
                if not prior_entry or _sha(target.read_bytes()) != prior_entry.get("markdown_sha256") or _sha(raw) != prior_entry["source_sha256"]:
                    raise MigrationError("target/source changed or pre-exists without this manifest; refusing overwrite")
                if entry["markdown_sha256"] != prior_entry.get("markdown_sha256"):
                    raise MigrationError("reviewed rendering changed; refusing automatic adoption")
                entry.update(action="unchanged", markdown_sha256=prior_entry["markdown_sha256"],
                             created_by_manifest=prior_entry.get("created_by_manifest", prior_entry.get("action") == "created"))
            if proposal is not None:
                from review_proposals import _render_card
                card = _relative(root / "memory/markdown", proposal["card_locator"], "candidate review card")
                if card.exists() and _sha(card.read_bytes()) != proposal["card_sha256"]:
                    raise MigrationError("candidate review card was edited; refusing overwrite")
                entry["card_missing"] = not card.exists()
        except (ValueError, OSError, KeyError) as exc:
            entry.update(action="blocked", error=str(exc))
            errors.append(f"{entry['source']}: {exc}")
        entries.append(entry)
    # Retain ownership and upgrade receipts across repeated runs and after rollback.
    present = {entry["source"] for entry in entries}
    for item in (prior or {}).get("entries", []):
        if item["source"] not in present and item["group"] == "markdown_upgrade":
            target = _relative(root, item["target"], "upgrade target")
            if not target.is_file() or _sha(target.read_bytes()) != item["markdown_sha256"]:
                error = f"{item['target']}: upgraded note changed; refusing automatic adoption"
                errors.append(error)
                entries.append(dict(item, action="blocked", error=error))
            else:
                entries.append(dict(item, action="unchanged"))
    # Validate the resulting canonical graph before writing any file.
    if not errors:
        from relations import _check_notes
        combined = {note["memory_id"]: note for note in list_notes(root / "memory/markdown")} if (root / "memory/markdown").is_dir() else {}
        for entry in entries:
            if entry["kind"] in {"canonical", "archived"} and entry["action"] != "unchanged":
                content, _ = _render_entry(root, entry, stamp)
                combined[entry["id"]] = parse_note(content.decode("utf-8"), root / entry["target"])
        graph = _check_notes(list(combined.values()))
        errors.extend(str(item) for item in graph["errors"])
    return {"schema_version": MANIFEST_VERSION, "generated_at": stamp, "root": str(root), "manifest": str(manifest_path), "dispositions": disposition_receipt,
            "entries": entries, "errors": errors, "status": "invalid" if errors else "ok"}


def migrate(root: Path = ROOT, *, manifest_path: Path | None = None, dispositions_path: Path | None = None, approved_dispositions_sha256: str | None = None) -> dict:
    from checkpoint_continuity import _context_lock
    root = _root(root)
    manifest_path = _manifest_path(root, manifest_path)
    if dispositions_path is not None and approved_dispositions_sha256 is None:
        raise MigrationError("migration requires the separately approved --approved-dispositions-sha256")
    _dispositions(root, dispositions_path, approved_dispositions_sha256)
    with _context_lock(root, 5.0):
        manifest_hash = _sha(manifest_path.read_bytes()) if manifest_path.exists() else None
        def persist() -> None:
            nonlocal manifest_hash
            raw = yaml.safe_dump(result, allow_unicode=True, sort_keys=False).encode("utf-8")
            _atomic(manifest_path, raw, expected_sha256=manifest_hash)
            manifest_hash = _sha(raw)
        result = plan(root, manifest_path=manifest_path, dispositions_path=dispositions_path)
        if result["dispositions"] and result["dispositions"]["sha256"] != approved_dispositions_sha256:
            raise MigrationError("dispositions changed after approval check")
        if result["status"] != "ok":
            return result  # No evidence copies, notes or manifest are written on invalid preflight.
        initialize_memory(root / "memory/markdown")
        for entry in result["entries"]:
            if entry["action"] != "unchanged":
                entry["created_by_manifest"] = entry["kind"] not in {"upgrade", "evidence"}
        result["status"] = "in_progress"
        persist()
        for entry in result["entries"]:
            if entry["action"] == "unchanged":
                if entry["kind"] == "proposal" and entry.get("card_missing"):
                    from review_proposals import _read_proposal, _render_card
                    proposal = _read_proposal(root / entry["target"], root)
                    card = _relative(root / "memory/markdown", proposal["card_locator"], "candidate review card")
                    _atomic(card, _render_card(proposal).encode("utf-8"), expected_sha256=None)
                    entry["card_missing"] = False
                continue
            source, target, evidence = _entry_paths(root, entry)
            raw = source.read_bytes()
            if _sha(raw) != entry["source_sha256"]:
                raise MigrationError("source changed after migration preflight")
            if evidence.exists() and evidence.read_bytes() != raw:
                raise MigrationError("legacy evidence/backup differs; refusing overwrite")
            if not evidence.exists():
                _atomic(evidence, raw, expected_sha256=None)
            if entry["kind"] == "evidence":
                entry["action"] = "preserved"
                persist()
                continue
            rendered, proposal = _render_entry(root, entry, result["generated_at"])
            if _sha(rendered) != entry["markdown_sha256"]:
                raise MigrationError("migration rendering changed after preflight")
            if entry["kind"] != "upgrade" and target.exists():
                raise MigrationError("target appeared after preflight; refusing overwrite")
            if entry["kind"] == "upgrade" and target.read_bytes() != raw:
                raise MigrationError("Markdown changed after preflight; refusing overwrite")
            _atomic(target, rendered, expected_sha256=entry["source_sha256"] if entry["kind"] == "upgrade" else None)
            entry["action"] = "upgraded" if entry["kind"] == "upgrade" else "created"
            if proposal is not None:
                from review_proposals import _render_card
                card = _relative(root / "memory/markdown", proposal["card_locator"], "candidate review card")
                if card.exists() and card.read_bytes() != _render_card(proposal).encode("utf-8"):
                    raise MigrationError("candidate review card already exists and differs")
                if not card.exists():
                    _atomic(card, _render_card(proposal).encode("utf-8"), expected_sha256=None)
                entry["card_missing"] = False
            persist()
        rebuild_index(root / "memory/markdown")
        result["status"] = "migrated"
        persist()
        verification = verify(manifest_path, root)
        report = root / "memory/migration/reports" / ("verify-" + _sha(manifest_path.read_bytes())[:16] + ".json")
        _atomic(report, json.dumps(verification, ensure_ascii=False, indent=2).encode("utf-8"))
        return result if verification["status"] == "ok" else dict(result, status="invalid", errors=verification["errors"])


def verify(manifest_path: Path, root: Path = ROOT) -> dict:
    root = _root(root)
    manifest = _read_manifest(root, _manifest_path(root, manifest_path))
    errors = list(manifest.get("errors", []))
    try:
        binding = manifest.get("dispositions")
        pairs, _ = _dispositions(root, _relative(root, binding["source"], "reviewed dispositions") if binding else None, binding["sha256"] if binding else None)
        if pairs and {entry["source"] for entry in manifest["entries"]} != {pair[group]["source"] for pair in pairs.values() for group in ("canonical", "archive")}:
            raise MigrationError("manifest source inventory differs from reviewed dispositions")
        for pair in pairs.values():
            for group in ("canonical", "archive"):
                matches = [entry for entry in manifest["entries"] if entry["source"] == pair[group]["source"]]
                if len(matches) != 1 or matches[0]["source_sha256"] != pair[group]["sha256"] or matches[0]["id"] != pair["id"]:
                    raise MigrationError("manifest omitted or changed a reviewed historical version")
        for entry in manifest["entries"]:
            pair = pairs.get(entry.get("id"))
            if entry.get("kind") == "evidence" and (not pair or entry["source"] != pair["archive"]["source"]):
                raise MigrationError("unreviewed evidence-only disposition")
            expected = _history_pin(pair) if pair and entry["source"] == pair["canonical"]["source"] else None
            if entry.get("historical_archive") != expected or (expected and entry["kind"] != "canonical") or (pair and entry["source"] == pair["archive"]["source"] and entry["kind"] != "evidence"):
                raise MigrationError("manifest disposition differs from reviewed decision")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        errors.append(f"reviewed dispositions: {exc}")
    for entry in manifest["entries"]:
        if entry.get("action") == "blocked":
            errors.append(entry.get("error", "blocked migration entry"))
            continue
        source, target, evidence = _entry_paths(root, entry)
        if not evidence.is_file() or _sha(evidence.read_bytes()) != entry["source_sha256"]:
            errors.append(f"{entry['source']}: legacy/backup hash mismatch")
        if entry["group"] != "markdown_upgrade" and (not source.is_file() or _sha(source.read_bytes()) != entry["source_sha256"]):
            errors.append(f"{entry['source']}: source hash mismatch")
        if entry["kind"] == "evidence":
            continue
        if entry.get("action") == "rolled_back":
            if target.exists(): errors.append(f"{entry['target']}: rolled-back target reappeared")
            continue
        try:
            if entry["group"] == "markdown_upgrade":
                expected = render_note(parse_note(evidence.read_text(encoding="utf-8"), source)).encode("utf-8")
            else:
                expected, _ = _render_entry(root, entry, manifest["generated_at"])
            if _sha(expected) != entry["markdown_sha256"]:
                errors.append(f"{entry['target']}: source fields do not match the migration receipt")
        except (ValueError, OSError, KeyError) as exc:
            errors.append(f"{entry['source']}: source reconstruction failed ({type(exc).__name__})")
        if not target.is_file() or _sha(target.read_bytes()) != entry["markdown_sha256"]:
            errors.append(f"{entry['target']}: Markdown hash mismatch")
        elif entry["kind"] == "proposal":
            from review_proposals import _read_proposal
            proposal = _read_proposal(target, root)
            card = _relative(root / "memory/markdown", proposal["card_locator"], "candidate review card")
            if not card.is_file() or _sha(card.read_bytes()) != proposal["card_sha256"]:
                errors.append(f"{proposal['card_locator']}: candidate review card hash mismatch")
        else:
            parse_note(target.read_text(encoding="utf-8"), target)
    from relations import check
    graph = check(root / "memory/markdown")
    errors.extend(str(item) for item in graph["errors"])
    return {"status": "invalid" if errors else "ok", "checked": len(manifest["entries"]), "errors": errors}


def rollback(manifest_path: Path, root: Path = ROOT, *, target: str | None = None) -> dict:
    from checkpoint_continuity import _context_lock
    root = _root(root)
    manifest_path = _manifest_path(root, manifest_path)
    with _context_lock(root, 5.0):
        manifest = _read_manifest(root, manifest_path)
        manifest_hash = _sha(manifest_path.read_bytes())
        entries = [item for item in manifest["entries"] if item.get("created_by_manifest") and item.get("action") != "rolled_back"]
        if target is not None:
            entries = [item for item in entries if item["target"] == target]
            if not entries: raise MigrationError("target is not a manifest-created Markdown file")
        if len(entries) > 1:
            raise MigrationError("batch deletion is disabled; select one manifest-created --target per invocation")
        if not entries:
            return {"status": "rolled_back", "removed": [], "legacy_preserved": True}
        entry = entries[0]
        _, path, _ = _entry_paths(root, entry)
        if path.exists() and _sha(path.read_bytes()) != entry["markdown_sha256"]:
            return {"status": "protected", "removed": [], "skipped": [entry["target"]], "legacy_preserved": True}
        if entry["kind"] != "proposal" and path.exists():
            from relations import _check_notes
            notes = list_notes(root / "memory/markdown")
            before_errors = _check_notes(notes)["errors"]
            after_errors = _check_notes([item for item in notes if item["memory_id"] != entry["id"]])["errors"]
            if any(error not in before_errors for error in after_errors):
                return {"status": "protected", "removed": [], "skipped": [entry["target"]],
                        "reason": "remove reciprocal relations through an approved proposal before rollback", "legacy_preserved": True}
        if path.exists(): path.unlink()  # Exactly one validated, explicitly selected file.
        entry["action"] = "rolled_back"
        _atomic(manifest_path, yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False).encode("utf-8"), expected_sha256=manifest_hash)
        rebuild_index(root / "memory/markdown")
        return {"status": "rolled_back", "removed": [entry["target"]], "legacy_preserved": True}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "migrate", "verify", "rollback"))
    parser.add_argument("--root", type=Path, default=ROOT, help="Mei checkout root, NOT memory/markdown")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--target", help="One manifest-created Markdown path for rollback")
    parser.add_argument("--dispositions", type=Path, help="Explicit pinned historical-version decisions; plan is preview only")
    parser.add_argument("--approved-dispositions-sha256", help="Exact separately approved decision-file hash; never inferred from its contents")
    parser.add_argument("--maintainer", action="store_true", help="Confirm explicitly selected maintainer workflow; not an ACL grant")
    args = parser.parse_args()
    try:
        if args.command in {"migrate", "rollback"} and not args.maintainer:
            raise MigrationError("explicit --maintainer confirmation is required; filesystem permissions still apply")
        path = args.manifest or args.root / "memory/migration/manifest.yaml"
        if args.approved_dispositions_sha256 and args.command != "migrate":
            raise MigrationError("approval hash is only used by migrate")
        if args.dispositions and args.command not in {"plan", "migrate"}:
            raise MigrationError("verify/rollback use the manifest's retained disposition binding")
        result = (plan(args.root, manifest_path=path, dispositions_path=args.dispositions) if args.command == "plan" else
                  migrate(args.root, manifest_path=path, dispositions_path=args.dispositions, approved_dispositions_sha256=args.approved_dispositions_sha256) if args.command == "migrate" else
                  verify(path, args.root) if args.command == "verify" else rollback(path, args.root, target=args.target))
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "migrated", "rolled_back"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
