"""Read and validate one Mei continuity context without writing state."""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker

try:  # Support both direct script execution and package imports.
    from ._common import ROOT, supports_registry_version
except ImportError:  # pragma: no cover - direct execution uses the script path.
    from _common import ROOT, supports_registry_version

SCHEMA_VERSION = "v0.1.0"
MAX_INDEX_BYTES = 262_144
MAX_INDEX_LINES = 2_048
MAX_INDEX_ENTRIES = 256
MAX_PROJECT_BYTES = 65_536
MAX_CHECKPOINTER_BYTES = 32_768
SOFT_INDEX_BYTES = 200_000
SOFT_INDEX_LINES = 1_600
SOFT_INDEX_ENTRIES = 200
SOFT_PROJECT_BYTES = 52_428
SOFT_CHECKPOINTER_BYTES = 26_214
REPO_ID_PATTERN = re.compile(r"^repo-[a-f0-9]{32}$")
CONTEXT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
FORBIDDEN_KEYS = {"prompt", "raw_prompt", "transcript", "raw_transcript", "tool_output", "memory_body"}
REPARSE_POINT = stat.FILE_ATTRIBUTE_REPARSE_POINT
_RETIRED_FIELDS = {
    "goal": "Completed task; transient state retired.",
    "decisions": [], "constraints": [], "blockers": [],
    "next_step": "No active task. Start a new context for new work.",
}


def _completion_id(repo_id: str, context_id: str) -> str:
    return "continuity-" + hashlib.sha256(f"{repo_id}\n{context_id}".encode("utf-8")).hexdigest()


class ContinuityError(ValueError):
    """A deterministic read failure at the continuity boundary."""


class _UniqueLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ContinuityError("continuity index: mapping keys must be strings")
        if key in mapping:
            raise ContinuityError(f"continuity index: duplicate key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _duplicate_key(pairs: list[tuple[str, Any]]) -> dict:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContinuityError(f"JSON: duplicate key {key!r}")
        value[key] = item
    return value


def _read_bounded(path: Path, max_bytes: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except OSError as exc:
        raise ContinuityError(f"{path}: cannot read continuity {label}: {exc}") from exc
    if len(raw) > max_bytes:
        raise ContinuityError(f"{path}: {label} exceeds {max_bytes} byte hard cap")
    return raw


def _read_json(path: Path, max_bytes: int) -> tuple[dict, bytes]:
    raw = _read_bounded(path, max_bytes, "record")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_key)
    except (UnicodeDecodeError, json.JSONDecodeError, ContinuityError) as exc:
        raise ContinuityError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ContinuityError(f"{path}: expected a JSON object")
    return value, raw


def _read_yaml(path: Path) -> tuple[dict, bytes]:
    raw = _read_bounded(path, MAX_INDEX_BYTES, "index")
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=_UniqueLoader)
    except (UnicodeDecodeError, yaml.YAMLError, ContinuityError) as exc:
        raise ContinuityError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ContinuityError(f"{path}: expected a YAML object")
    return value, raw


def _validate(value: dict, schema_name: str, source: Path) -> None:
    schema_path = ROOT / "schemas" / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuityError(f"{schema_name}: cannot load schema: {exc}") from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(map(str, error.path)) or "<root>"
        raise ContinuityError(f"{source}: {schema_name} {location}: {error.message}")


def _assert_private(value: Any, source: Path, path: str = "<root>") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in FORBIDDEN_KEYS:
                raise ContinuityError(f"{source}: forbidden raw-content field {path}.{key}")
            _assert_private(item, source, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_private(item, source, f"{path}[{index}]")
    elif isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                raise ContinuityError(f"{source}: possible secret or private key")


def _is_reparse_point(path: Path) -> bool:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ContinuityError(f"{path}: cannot inspect path: {exc}") from exc
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & REPARSE_POINT)


def _assert_no_reparse(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    if _is_reparse_point(current):
        raise ContinuityError(f"{path}: symlink or junction is not allowed")
    for part in absolute.parts[1:]:
        current = current / part
        if _is_reparse_point(current):
            raise ContinuityError(f"{path}: symlink or junction component is not allowed")


def _safe_relative_path(root: Path, locator: str, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise ContinuityError(f"{label}: path must be a non-empty string")
    normalized = locator.replace("\\", "/")
    if normalized.startswith("/") or ntpath.isabs(locator) or ntpath.splitdrive(locator)[0]:
        raise ContinuityError(f"{label}: absolute path is not allowed")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ContinuityError(f"{label}: path escape or ambiguous component")
    candidate = root.joinpath(*parts)
    _assert_no_reparse(root)
    _assert_no_reparse(candidate)
    try:
        if not candidate.resolve(strict=False).is_relative_to(root.resolve(strict=False)):
            raise ContinuityError(f"{label}: path escapes its allowed root")
    except ValueError as exc:
        raise ContinuityError(f"{label}: path escapes its allowed root") from exc
    return candidate


def _normalized_path(path: Path) -> str:
    return path.resolve(strict=False).as_posix().rstrip("/").casefold()


def _normalized_remote(remote: str) -> str:
    raw = remote.strip().replace("\\", "/")
    if "://" in raw:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").casefold()
        path = parsed.path.strip("/")
    elif re.fullmatch(r"(?:[^@/]+@)?[^:/]+:.+", raw):
        host, path = raw.rsplit(":", 1)
        host = host.rsplit("@", 1)[-1].casefold()
        path = path.strip("/")
    else:
        host, path = "", raw.strip("/")
    if path.casefold().endswith(".git"):
        path = path[:-4]
    return f"{host}/{path}".strip("/").casefold()


def _git(repo_root: Path, *args: str, check: bool = False) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        raise ContinuityError(f"repository is not readable by Git: {result.stderr.strip() or result.stdout.strip()}")
    if result.returncode:
        return None
    return result.stdout.strip() or None


def _git_remote(repo_root: Path) -> str | None:
    return _git(repo_root, "config", "--get", "remote.origin.url")


def repo_id_for(repo_root: str | Path, remote: str | None = None) -> str:
    """Return the deterministic identity for a normalized repository root."""

    root = Path(repo_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise ContinuityError(f"repository root does not exist: {root}")
    remote_value = remote if remote is not None else _git_remote(root)
    identity = f"root:{_normalized_path(root)}"
    if remote_value:
        identity += f"|remote:{_normalized_remote(remote_value)}"
    return "repo-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _check_context_id(context_id: str) -> None:
    if not isinstance(context_id, str) or not CONTEXT_ID_PATTERN.fullmatch(context_id):
        raise ContinuityError("context_id is invalid")


def _expected_paths(repo_id: str, context_id: str) -> tuple[str, str]:
    directory = f"memory/continuity/{repo_id}/{context_id}"
    return f"{directory}/project.json", f"{directory}/checkpointer.json"


def _soft_cap_warning(label: str, actual: int, soft: int, hard: int) -> str | None:
    if actual >= soft:
        return f"{label} is near its {hard} hard cap ({actual}/{hard})"
    return None


def _release_version(mei_root: Path) -> str | None:
    version_path = mei_root / "VERSION"
    try:
        value = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _live_repository(repo_root: Path) -> dict[str, Any]:
    top = _git(repo_root, "rev-parse", "--show-toplevel", check=True)
    if top is None or _normalized_path(Path(top)) != _normalized_path(repo_root):
        raise ContinuityError("repo_root is not the selected Git worktree root")
    return {
        "root": _normalized_path(repo_root),
        "branch": _git(repo_root, "branch", "--show-current"),
        "commit": _git(repo_root, "rev-parse", "HEAD"),
        "is_worktree": _git(repo_root, "rev-parse", "--is-inside-work-tree") == "true",
    }


def _stable_ref_status(repo_root: Path, refs: list[dict[str, str]]) -> tuple[list[dict[str, Any]], bool]:
    result: list[dict[str, Any]] = []
    stale = False
    for ref in refs:
        kind, value = ref["kind"], ref["value"]
        item: dict[str, Any] = {"kind": kind, "value": value, "verified": False, "stale": False}
        if kind == "branch":
            item["verified"] = _git(repo_root, "show-ref", "--verify", f"refs/heads/{value}") is not None
            item["stale"] = not item["verified"]
        elif kind == "file":
            path = _safe_relative_path(repo_root, value, "stable_refs.file")
            item["verified"] = path.is_file()
            item["stale"] = not item["verified"]
        else:
            item["verified"] = False
            item["verification"] = "requires external evidence"
        stale = stale or item["stale"]
        result.append(item)
    return result, stale


def load_context(
    repo_root: str | Path,
    context_id: str,
    *,
    mei_root: str | Path | None = None,
    remote: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """Load one selected context and return its status and live repo facts.

    This function is intentionally read-only. It never creates a missing file,
    updates a hash, marks reconciliation, or opens another context.
    """

    repo_root = Path(repo_root).expanduser().resolve(strict=False)
    mei_root_input = Path(mei_root or ROOT).expanduser()
    _assert_no_reparse(mei_root_input)
    mei_root = mei_root_input.resolve(strict=False)
    _check_context_id(context_id)
    if not repo_root.is_dir():
        raise ContinuityError(f"repository root does not exist: {repo_root}")
    if not mei_root.is_dir():
        raise ContinuityError(f"Mei root does not exist: {mei_root}")
    live_repository = _live_repository(repo_root)
    requested_repo_id = repo_id_for(repo_root, remote)

    index_path = mei_root / "registry" / "continuity.yaml"
    _assert_no_reparse(mei_root)
    _assert_no_reparse(index_path)
    if not index_path.is_file():
        raise ContinuityError(f"continuity index missing: {index_path}")
    index, index_bytes = _read_yaml(index_path)
    if len(index_bytes.splitlines()) > MAX_INDEX_LINES:
        raise ContinuityError(f"{index_path}: index exceeds {MAX_INDEX_LINES} line hard cap")
    _validate(index, "continuity-index.schema.json", index_path)
    _assert_private(index, index_path)
    if index["schema_version"] != SCHEMA_VERSION:
        raise ContinuityError(f"{index_path}: unknown continuity index schema")
    release = _release_version(mei_root)
    # Mei v2 changes ordinary memory storage, not the validated v0.1.0 continuity protocol.
    compatible_majors = (1,) if release is not None and release.startswith("v2.") else ()
    if release is not None and not supports_registry_version(index["version"], release, compatible_majors=compatible_majors):
        raise ContinuityError(f"{index_path}: index version is invalid or newer than VERSION")
    if len(index["entries"]) > MAX_INDEX_ENTRIES:
        raise ContinuityError(f"{index_path}: too many context entries")

    warnings: list[str] = []
    for label, actual, soft, hard in (
        ("continuity index bytes", len(index_bytes), SOFT_INDEX_BYTES, MAX_INDEX_BYTES),
        ("continuity index lines", len(index_bytes.splitlines()), SOFT_INDEX_LINES, MAX_INDEX_LINES),
        ("continuity index entries", len(index["entries"]), SOFT_INDEX_ENTRIES, MAX_INDEX_ENTRIES),
    ):
        warning = _soft_cap_warning(label, actual, soft, hard)
        if warning:
            warnings.append(warning)

    keys = [(entry["repo_id"], entry["context_id"]) for entry in index["entries"]]
    if len(keys) != len(set(keys)):
        raise ContinuityError(f"{index_path}: duplicate repo/context entry")
    for candidate in index["entries"]:
        expected_project, expected_checkpointer = _expected_paths(candidate["repo_id"], candidate["context_id"])
        if (
            candidate["project_path"].replace("\\", "/") != expected_project
            or candidate["checkpointer_path"].replace("\\", "/") != expected_checkpointer
        ):
            raise ContinuityError(f"{index_path}: continuity index entry path is not deterministic")

    selected = [
        entry
        for entry in index["entries"]
        if entry["repo_id"] == requested_repo_id and entry["context_id"] == context_id
    ]
    if not selected:
        raise ContinuityError("selected repo/context is not present in continuity index")
    if len(selected) != 1:
        raise ContinuityError("selected repo/context is ambiguous")
    entry = selected[0]
    expected_project, expected_checkpointer = _expected_paths(requested_repo_id, context_id)
    project_path = _safe_relative_path(mei_root, entry["project_path"], "project_path")
    checkpointer_path = _safe_relative_path(mei_root, entry["checkpointer_path"], "checkpointer_path")
    if not project_path.is_file() or not checkpointer_path.is_file():
        raise ContinuityError("selected continuity project/checkpointer file is missing")
    project, project_bytes = _read_json(project_path, MAX_PROJECT_BYTES)
    checkpointer, checkpointer_bytes = _read_json(checkpointer_path, MAX_CHECKPOINTER_BYTES)
    for label, actual, soft, hard in (
        ("project record bytes", len(project_bytes), SOFT_PROJECT_BYTES, MAX_PROJECT_BYTES),
        ("checkpointer record bytes", len(checkpointer_bytes), SOFT_CHECKPOINTER_BYTES, MAX_CHECKPOINTER_BYTES),
    ):
        warning = _soft_cap_warning(label, actual, soft, hard)
        if warning:
            warnings.append(warning)
    _validate(project, "continuity-project.schema.json", project_path)
    _validate(checkpointer, "continuity-checkpointer.schema.json", checkpointer_path)
    _assert_private(project, project_path)
    _assert_private(checkpointer, checkpointer_path)
    if project["repo_id"] != requested_repo_id or checkpointer["repo_id"] != requested_repo_id:
        raise ContinuityError("continuity record repo_id does not match selected repository")
    if project["context_id"] != context_id or checkpointer["context_id"] != context_id:
        raise ContinuityError("continuity record context_id does not match selected context")
    if checkpointer["synced_generation"] > checkpointer["generation"]:
        raise ContinuityError("checkpointer synced_generation exceeds generation")

    project_sha256 = hashlib.sha256(project_bytes).hexdigest()
    index_sha256 = hashlib.sha256(index_bytes).hexdigest()
    diagnostics: list[str] = []
    pair_needs_reconcile = False
    if checkpointer["project_sha256"] != project_sha256:
        diagnostics.append("project hash differs from checkpointer")
        pair_needs_reconcile = True
    if checkpointer["index_sha256"] != index_sha256:
        diagnostics.append("index hash differs from checkpointer")
        pair_needs_reconcile = True
    actual_lines, actual_bytes = len(index_bytes.splitlines()), len(index_bytes)
    if checkpointer["index_lines"] != actual_lines or checkpointer["index_bytes"] != actual_bytes:
        diagnostics.append("index size differs from checkpointer")
        pair_needs_reconcile = True
    if checkpointer["needs_reconcile"]:
        diagnostics.append("checkpointer requires reconciliation")
        pair_needs_reconcile = True

    completion = checkpointer.get("completion")
    if project["status"] == "completed" and (checkpointer["dirty"] or checkpointer["synced_generation"] != checkpointer["generation"]):
        diagnostics.append("completed context bookkeeping is not synchronized")
        pair_needs_reconcile = True
    if project["status"] != entry["status"]:
        # A terminal overlay keeps the shared locator/index hash unchanged.
        # An interrupted completion must remain readable for explicit recovery.
        if project["status"] != "completed" or (completion is None and checkpointer["project_sha256"] == project_sha256):
            raise ContinuityError("continuity index status differs from project status")
    if completion is not None and project["status"] != "completed":
        raise ContinuityError("completion receipt requires a completed project")
    if completion is not None:
        if completion["trajectory_id"] != _completion_id(requested_repo_id, context_id):
            raise ContinuityError("completion trajectory identity differs from selected repo/context")
        if completion["source_generation"] >= checkpointer["generation"]:
            raise ContinuityError("completion source generation must precede the checkpoint")
        if completion["retired"] and any(project[key] != value for key, value in _RETIRED_FIELDS.items()):
            raise ContinuityError("retired completion still contains transient project state")

    stable_refs, stale_refs = _stable_ref_status(repo_root, project["stable_refs"])
    if stale_refs:
        diagnostics.append("one or more stable references are stale")
    if checkpointer.get("last_conflict") is not None:
        status = "conflict"
    elif pair_needs_reconcile:
        status = "needs_reconcile"
    elif project["status"] == "completed":
        status = "retired" if completion and completion["retired"] else "completed"
    elif checkpointer["dirty"]:
        status = "dirty"
    elif stale_refs:
        status = "stale"
    else:
        status = "clean"
    result = {
        "status": status,
        "host": host,
        "repo_id": requested_repo_id,
        "context_id": context_id,
        "index": index,
        "project": project,
        "checkpointer": checkpointer,
        "project_sha256": project_sha256,
        "index_sha256": index_sha256,
        "index_bytes": actual_bytes,
        "index_lines": actual_lines,
        "needs_reconcile": pair_needs_reconcile,
        "stable_refs": stable_refs,
        "live_repository": live_repository,
        "diagnostics": diagnostics,
        "warnings": warnings,
    }
    if project["status"] == "completed":
        result["required_action"] = (
            "explicit_reconcile" if status in {"needs_reconcile", "conflict"}
            else "none" if status == "retired"
            else "export_and_retire" if completion
            else "review_completion"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--mei-root", type=Path, default=ROOT)
    parser.add_argument("--remote")
    parser.add_argument("--host")
    args = parser.parse_args(argv)
    try:
        result = load_context(args.repo_root, args.context_id, mei_root=args.mei_root, remote=args.remote, host=args.host)
        from continuity_adapters import handoff_status
        result["handoff"] = handoff_status(args.repo_root.resolve(), result, mei_root=args.mei_root.resolve())
    except (ContinuityError, OSError) as exc:
        print(f"continuity check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
