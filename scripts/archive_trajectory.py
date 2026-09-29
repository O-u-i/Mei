"""Archive one reviewed trajectory and update its local source locators."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from _common import ROOT, json_text, read_json, source_digest, validate_trajectory
from normalize_trajectory import normalization_payload


def archive_normalized(path: Path, root: Path = ROOT) -> list[Path]:
    """Move one finalized normalized record while preserving its memory citations."""
    path = path.resolve()
    normalized_dir = (root / "trajectories" / "normalized").resolve()
    if path.parent != normalized_dir:
        raise ValueError("source must be one direct normalized JSON file")
    normalized = read_json(path)
    source = root / "trajectories" / "archive" / path.name
    review = root / "local" / "trajectory-reviews" / path.name
    destination = root / "trajectories" / "archive" / "normalized" / path.name
    if not source.is_file() or not review.is_file() or destination.exists():
        raise ValueError("archived source and review must exist; normalized destination must be unused")
    review_data = read_json(review)
    if review_data.get("id") != path.stem or review_data.get("source_path") != f"trajectories/archive/{path.name}":
        raise ValueError("review ID or archived source path mismatch")
    if normalized["id"] != path.stem or normalized["source_path"] != f"trajectories/archive/{path.name}":
        raise ValueError("normalized ID or archived source path mismatch")
    if normalized["source_sha256"] != source_digest(source):
        raise ValueError("normalized source hash mismatch")
    if review_data.get("source_sha256") != normalized["source_sha256"]:
        raise ValueError("review source hash mismatch")
    previous = f"trajectories/normalized/{path.name}"
    current = f"trajectories/archive/normalized/{path.name}"
    updates: list[tuple[Path, dict]] = []
    for group, pattern in (("candidates", "*.json"), ("canonical", "*/*.json"), ("archive", "*.json")):
        for record_path in sorted((root / "memory" / group).glob(pattern)):
            record = read_json(record_path)
            changed = False
            for entry in record["provenance"]:
                if entry["source"] == previous:
                    entry["source"] = current
                    changed = True
            if changed:
                updates.append((record_path, record))
    destination.parent.mkdir(parents=True, exist_ok=True)
    path.rename(destination)  # One explicitly named normalized file is moved.
    changed_paths = [destination]
    for record_path, record in updates:
        record_path.write_text(json_text(record), encoding="utf-8")
        changed_paths.append(record_path)
    return changed_paths


def archive(path: Path, decision: str, reason: str, root: Path = ROOT) -> list[Path]:
    path = path.resolve()
    store = (root / "trajectories").resolve()
    if path.parent not in {store / folder for folder in ("inbox", "processed", "rejected")}:
        raise ValueError("source must be one direct inbox, processed, or rejected JSON file")
    if decision not in {"promoted", "rejected"} or not reason.strip():
        raise ValueError("a promoted or rejected decision and review reason are required")
    item = validate_trajectory(path, root)
    destination = store / "archive" / path.name
    review = root / "local" / "trajectory-reviews" / path.name
    if destination.exists() or review.exists():
        raise ValueError("archive or review record already exists")

    aliases = {f"trajectories/{folder}/{path.name}" for folder in ("inbox", "processed", "rejected")}
    archived_path = f"trajectories/archive/{path.name}"
    normalized_source = f"trajectories/normalized/{path.name}"
    updates: list[tuple[Path, dict]] = []
    canonical_refs = 0
    for group, pattern in (("candidates", "*.json"), ("canonical", "*/*.json"), ("archive", "*.json")):
        for record_path in sorted((root / "memory" / group).glob(pattern)):
            record = read_json(record_path)
            changed = False
            for entry in record["provenance"]:
                if group == "canonical" and entry["source"] == normalized_source:
                    canonical_refs += 1
                if entry["source"] in aliases:
                    entry["source"] = archived_path
                    changed = True
                    if group == "canonical":
                        canonical_refs += 1
            if changed:
                updates.append((record_path, record))
    if decision == "promoted" and canonical_refs == 0:
        raise ValueError("promoted trajectory has no canonical memory provenance")
    if decision == "rejected" and canonical_refs:
        raise ValueError("rejected trajectory is cited by canonical memory")

    normalized_path = root / "trajectories" / "normalized" / path.name
    normalized = None
    digest = source_digest(path)
    if normalized_path.exists():
        normalized = read_json(normalized_path)
        if normalized["source_sha256"] != digest or normalized["id"] != item["id"]:
            raise ValueError("normalized source hash or ID mismatch")
        if normalized["source_path"] not in aliases:
            raise ValueError("normalized source path does not match the trajectory")

    review.parent.mkdir(parents=True, exist_ok=True)
    path.rename(destination)  # Exactly one named trajectory is moved per invocation.
    changed_paths = [destination]
    if normalized is not None:
        updated = normalization_payload(item, path.name, digest, "archive")
        normalized_path.write_text(json_text(updated), encoding="utf-8")
        changed_paths.append(normalized_path)
    for record_path, record in updates:
        record_path.write_text(json_text(record), encoding="utf-8")
        changed_paths.append(record_path)
    review.write_text(json_text({
        "id": item["id"],
        "decision": decision,
        "reason": reason.strip(),
        "reviewed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha256": digest,
        "source_path": archived_path,
    }), encoding="utf-8")
    changed_paths.append(review)
    if normalized is not None:
        changed_paths.extend(archive_normalized(normalized_path, root))
    return changed_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--decision", choices=("promoted", "rejected"))
    parser.add_argument("--reason")
    parser.add_argument("--normalized-only", action="store_true", help="backfill one already reviewed normalized record")
    args = parser.parse_args()
    try:
        if args.normalized_only:
            if args.decision or args.reason:
                raise ValueError("normalized-only does not accept a decision or reason")
            paths = archive_normalized(args.path)
            print("archived one normalized record; updated:")
        else:
            if not args.decision or not args.reason:
                raise ValueError("decision and review reason are required")
            paths = archive(args.path, args.decision, args.reason)
            print("archived one trajectory; updated:")
        for path in paths:
            print(path.relative_to(ROOT))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"archive failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
