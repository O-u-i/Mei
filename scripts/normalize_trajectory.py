"""Normalize one validated inbox trajectory; preserve its source hash."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import ROOT, json_text, source_digest, validate_inbox


def normalization_payload(data: dict, source_name: str, digest: str) -> dict:
    return {
        "schema_version": data["schema_version"],
        "id": data["id"],
        "source_path": f"trajectories/inbox/{source_name}",
        "source_sha256": digest,
        "project": data["project"],
        "completed_at": data["completed_at"],
        "goal": data["goal"],
        "result": data["result"],
        "evaluation": data["evaluation"],
        "corrections": data["corrections"],
        "lessons": data["lessons"],
        "memory_candidates": data["memory_candidates"],
        "skill_candidates": data["skill_candidates"],
    }


def normalize(path: Path, root: Path = ROOT) -> tuple[Path, bool]:
    path = path.resolve()
    inbox = (root / "trajectories" / "inbox").resolve()
    if path.parent != inbox:
        raise ValueError("source must be a direct inbox JSON file")
    entries = validate_inbox(root)
    matches = [(source, data) for source, data in entries if source.resolve() == path]
    if len(matches) != 1:
        raise ValueError(f"source is not a valid inbox entry: {path}")
    _, data = matches[0]
    normalized = normalization_payload(data, path.name, source_digest(path))
    target = root / "trajectories" / "normalized" / path.name
    content = json_text(normalized)
    if target.exists():
        if target.read_text(encoding="utf-8") == content:
            return target, False
        raise ValueError(f"conflicting normalized file exists: {target}")
    target.write_text(content, encoding="utf-8")
    return target, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        target, changed = normalize(args.path, ROOT)
        print(f"{'created' if changed else 'unchanged'}: {target}")
        return 0
    except (ValueError, OSError) as exc:
        print(f"normalization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
