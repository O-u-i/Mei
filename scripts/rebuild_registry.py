"""Rebuild the canonical memory index from reviewed records."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from _common import ROOT, check_private_content, read_json, validate_schema, version


def expected_registry(root: Path = ROOT) -> dict:
    records = []
    seen: set[str] = set()
    base = root / "memory" / "canonical"
    for path in sorted(base.glob("*/*.json")):
        item = read_json(path)
        validate_schema(item, "memory.schema.json", root)
        check_private_content(item)
        if item["status"] != "canonical" or item["verified_at"] is None:
            raise ValueError(f"{path}: canonical record must be verified")
        if item["category"] != path.parent.name or item["id"] != path.stem:
            raise ValueError(f"{path}: category or filename mismatch")
        if item["id"] in seen:
            raise ValueError(f"duplicate memory id: {item['id']}")
        seen.add(item["id"])
        records.append({"id": item["id"], "category": item["category"], "scope": item["scope"], "path": path.relative_to(root).as_posix()})
    return {"version": version(root), "generated_by": "scripts/rebuild_registry.py", "records": records}


def rebuild(root: Path = ROOT) -> bool:
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
