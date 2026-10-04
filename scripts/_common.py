"""Shared deterministic validation for Mei records."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def version(root: Path = ROOT) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").strip()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def validate_schema(value: dict, schema_name: str, root: Path = ROOT) -> None:
    schema = read_json(root / "schemas" / schema_name)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda e: str(e.path))
    if errors:
        first = errors[0]
        location = ".".join(map(str, first.path)) or "<root>"
        raise ValueError(f"{schema_name} {location}: {first.message}")


def check_private_content(value: dict) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    for pattern in SECRET_PATTERNS:
        if pattern.search(serialized):
            raise ValueError("possible secret or private key in record")


def validate_trajectory(path: Path, root: Path = ROOT) -> dict:
    value = read_json(path)
    validate_schema(value, "trajectory.schema.json", root)
    if path.stem != value["id"]:
        raise ValueError(f"{path}: filename must match trajectory id")
    check_private_content(value)
    return value


def validate_inbox(root: Path = ROOT) -> list[tuple[Path, dict]]:
    entries = []
    for path in sorted((root / "trajectories" / "inbox").glob("*.json")):
        value = validate_trajectory(path, root)
        entries.append((path, value))
    assert_unique_ids((value for _, value in entries), "trajectory")
    return entries


def validate_trajectory_store(root: Path = ROOT) -> dict[str, tuple[Path, dict]]:
    entries: list[tuple[Path, dict]] = []
    for state in ("inbox", "processed", "rejected", "archive"):
        for path in sorted((root / "trajectories" / state).glob("*.json")):
            entries.append((path, validate_trajectory(path, root)))
    assert_unique_ids((value for _, value in entries), "trajectory")
    return {value["id"]: (path, value) for path, value in entries}


def assert_unique_ids(values: Iterable[dict], kind: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value["id"] in seen:
            raise ValueError(f"duplicate {kind} id: {value['id']}")
        seen.add(value["id"])


def source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
