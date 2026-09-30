"""Read-only inventory and file comparison for an installed personal Codex skill."""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
from pathlib import Path

import yaml

from _common import ROOT


def inventory(directory: Path) -> dict[str, str]:
    if not (directory / "SKILL.md").is_file():
        raise ValueError(f"skill directory has no SKILL.md: {directory}")
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            relative = path.relative_to(directory).as_posix()
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def installed_skill(skill_id: str, root: Path = ROOT) -> Path:
    with (root / "local" / "paths.toml").open("rb") as stream:
        skills_root = Path(tomllib.load(stream)["paths"]["skills_root"]).resolve()
    registry = yaml.safe_load((root / "registry" / "skills.yaml").read_text(encoding="utf-8"))
    matches = [item for item in registry["skills"] if item["id"] == skill_id and item["status"] == "active"]
    if len(matches) != 1:
        raise ValueError(f"expected one active registry entry for {skill_id}")
    skill_file = Path(matches[0]["path"]).resolve()
    if skill_file.name != "SKILL.md" or not skill_file.parent.is_relative_to(skills_root):
        raise ValueError("active skill must point to SKILL.md inside personal skills_root")
    return skill_file.parent


def compare(installed: dict[str, str], proposed: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    added = sorted(proposed.keys() - installed.keys())
    removed = sorted(installed.keys() - proposed.keys())
    changed = sorted(path for path in installed.keys() & proposed.keys() if installed[path] != proposed[path])
    return added, removed, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_id", help="active id from registry/skills.yaml")
    parser.add_argument("--proposed-dir", type=Path, help="read-only comparison with a proposed full skill directory")
    args = parser.parse_args()
    try:
        installed_dir = installed_skill(args.skill_id)
        current = inventory(installed_dir)
        print(f"installed: {installed_dir}")
        print(f"files (excluding generated Python caches): {len(current)}")
        print(f"SKILL.md sha256: {current['SKILL.md']}")
        if args.proposed_dir is not None:
            proposed_dir = args.proposed_dir.resolve()
            proposed = inventory(proposed_dir)
            added, removed, changed = compare(current, proposed)
            print(f"proposed: {proposed_dir}")
            for label, paths in (("added", added), ("removed", removed), ("changed", changed)):
                print(f"{label}: {len(paths)}")
                for path in paths:
                    print(f"  {path}")
        return 0
    except (OSError, KeyError, ValueError, yaml.YAMLError) as exc:
        print(f"skill audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
