"""Rebuild the canonical memory index from reviewed records."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from _common import ROOT, assert_unique_ids, check_private_content, read_json, validate_schema, version
from memory_markdown import list_notes, rebuild_index, resolve_memory_root


def expected_registry(root: Path = ROOT) -> dict:
    records = []
    memory_root = resolve_memory_root(root)
    for item in list_notes(memory_root, schema_root=root):
        if item["status"] != "active" or item.get("review_required"):
            continue
        records.append({"id": item["memory_id"], "category": item["category"], "scope": item["scope"], "path": (memory_root / item["path"]).relative_to(root.resolve()).as_posix()})
    assert_unique_ids(records, "memory")
    return {"version": version(root), "generated_by": "scripts/rebuild_registry.py", "records": records}


def rebuild(root: Path = ROOT) -> bool:
    rebuild_index(resolve_memory_root(root))
    target = root / "registry" / "memory.yaml"
    content = yaml.safe_dump(expected_registry(root), allow_unicode=True, sort_keys=False)
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return False
    target.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    try:
        changed = rebuild(ROOT)
        print("memory registry: " + ("updated" if changed else "unchanged"))
        return 0
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"registry rebuild failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
