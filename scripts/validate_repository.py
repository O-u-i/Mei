"""Check Mei structure, records, registries, and personal skill pointers."""

from __future__ import annotations

import sys
import tomllib
import re
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from _common import ROOT, assert_unique_ids, check_private_content, read_json, source_digest, validate_schema, validate_trajectory_store, version
from normalize_trajectory import normalization_payload
from rebuild_registry import expected_registry


def check_local_paths(root: Path) -> None:
    path = root / "local" / "paths.toml"
    if not path.is_file():
        raise ValueError("local paths missing; run python scripts/init_local.py")
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    paths = config.get("paths", {})
    required = ("mei_root", "codex_home", "skills_root", "inbox")
    if any(not isinstance(paths.get(key), str) or not Path(paths[key]).is_absolute() for key in required):
        raise ValueError("local paths must be absolute")
    if Path(paths["mei_root"]).resolve() != root.resolve():
        raise ValueError("local mei_root differs from this checkout")
    if Path(paths["inbox"]).resolve() != (root / "trajectories" / "inbox").resolve():
        raise ValueError("local inbox differs from this checkout")
    if Path(paths["skills_root"]).resolve() != (Path(paths["codex_home"]) / "skills").resolve():
        raise ValueError("local skills_root differs from codex_home/skills")


def check_local_provenance(entry: dict, root: Path) -> None:
    for source in entry["provenance"]:
        path = Path(source["source"])
        if path.is_absolute():
            continue  # Machine-local source; its existence is checked during human review.
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
            raise ValueError(f"{entry['id']}: missing or unsafe provenance path: {source['source']}")


def check_episodic_references(entry: dict, root: Path) -> None:
    for reference in entry.get("references", []):
        target = (root / reference).resolve()
        episodic_root = (root / "memory" / "episodic").resolve()
        if not target.is_relative_to(episodic_root) or not target.is_file():
            raise ValueError(f"{entry['id']}: missing or unsafe episodic reference: {reference}")
        for link in re.findall(r"\]\((references/[^)]+)\)", target.read_text(encoding="utf-8")):
            detail = (target.parent / link).resolve()
            if not detail.is_relative_to(target.parent) or not detail.is_file():
                raise ValueError(f"{entry['id']}: missing or unsafe episodic detail: {link}")


def validate_repository(root: Path = ROOT) -> None:
    check_local_paths(root)
    current_version = version(root)
    if current_version != "v1.0.0":
        raise ValueError(f"unexpected Mei version: {current_version}")
    if f"Mei {current_version}" not in (root / "README.md").read_text(encoding="utf-8"):
        raise ValueError("README version differs from VERSION")
    for schema_path in (root / "schemas").glob("*.schema.json"):
        Draft202012Validator.check_schema(read_json(schema_path))
    trajectory_store = validate_trajectory_store(root)
    active_normalized = root / "trajectories" / "normalized"
    archived_normalized = root / "trajectories" / "archive" / "normalized"
    for path in sorted((*active_normalized.glob("*.json"), *archived_normalized.glob("*.json"))):
        normalized = read_json(path)
        source = trajectory_store.get(path.stem)
        if source is None:
            raise ValueError(f"{path}: normalized source trajectory missing")
        source_path, source_data = source
        if source_path.parent.name == "archive" and path.parent != archived_normalized:
            raise ValueError(f"{path}: finalized normalized record must be archived")
        if source_path.parent.name != "archive" and path.parent == archived_normalized:
            raise ValueError(f"{path}: unresolved normalized record cannot be archived")
        expected = normalization_payload(source_data, source_path.name, source_digest(source_path), source_path.parent.name)
        if normalized != expected:
            raise ValueError(f"{path}: normalized content or source hash mismatch")
    memory_entries = []
    for group, expected_status in (("candidates", "candidate"), ("canonical", "canonical")):
        pattern = "*.json" if group == "candidates" else "*/*.json"
        for path in sorted((root / "memory" / group).glob(pattern)):
            entry = read_json(path)
            validate_schema(entry, "memory.schema.json", root)
            check_private_content(entry)
            check_local_provenance(entry, root)
            check_episodic_references(entry, root)
            if entry["status"] != expected_status or entry["id"] != path.stem:
                raise ValueError(f"{path}: status or filename mismatch")
            if group == "canonical" and (entry["verified_at"] is None or entry["category"] != path.parent.name):
                raise ValueError(f"{path}: canonical verification or category mismatch")
            if group == "candidates" and entry["verified_at"] is not None:
                raise ValueError(f"{path}: candidate must not claim verification")
            memory_entries.append(entry)
    assert_unique_ids(memory_entries, "memory")
    for path in sorted((root / "memory" / "archive").glob("*.json")):
        entry = read_json(path)
        validate_schema(entry, "memory.schema.json", root)
        check_private_content(entry)
        check_local_provenance(entry, root)
        if entry["status"] != "archived" or entry["id"] != path.stem:
            raise ValueError(f"{path}: archived status or filename mismatch")
    memory_registry = yaml.safe_load((root / "registry" / "memory.yaml").read_text(encoding="utf-8"))
    if memory_registry != expected_registry(root):
        raise ValueError("memory registry is stale; run rebuild_registry.py")
    projects = yaml.safe_load((root / "registry" / "projects.yaml").read_text(encoding="utf-8"))
    if projects.get("version") != current_version or not isinstance(projects.get("projects"), list):
        raise ValueError("projects registry invalid")
    skill_registry = yaml.safe_load((root / "registry" / "skills.yaml").read_text(encoding="utf-8"))
    if skill_registry.get("version") != current_version:
        raise ValueError("skills registry version differs")
    skill_ids = set()
    for skill in skill_registry.get("skills", []):
        validate_schema(skill, "skill.schema.json", root)
        if skill["id"] in skill_ids:
            raise ValueError(f"duplicate skill id: {skill['id']}")
        skill_ids.add(skill["id"])
        if skill["status"] == "active":
            path = Path(skill["path"])
            if not path.is_file() or f"name: {skill['id']}" not in path.read_text(encoding="utf-8"):
                raise ValueError(f"active skill missing or name mismatch: {path}")


def main() -> int:
    try:
        validate_repository(ROOT)
        print("Mei repository valid")
        return 0
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"Mei repository invalid: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
