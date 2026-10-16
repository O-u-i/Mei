"""Manage hash-bound Mei review proposals and Obsidian review cards."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib
import yaml

from _common import ROOT, check_private_content, validate_schema
from checkpoint_continuity import _LockTimeout, _context_lock
from memory_markdown import (
    MemoryError as MarkdownMemoryError,
    _assert_no_reparse,
    parse_note,
    read_note,
    resolve_memory_root,
    write_note,
    _UniqueLoader,
)
from sync_obsidian import resolve_vault, sync_obsidian


SCHEMA_VERSION = "v0.2.0"
PROPOSAL_SCHEMA = "memory-proposal.schema.json"
MAX_PROPOSAL_BYTES = 262_144
MAX_CARD_BYTES = 131_072
PROPOSAL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
TARGET_TYPES = frozenset(("personal_markdown", "governed_json", "relation_pair"))
STATUSES = frozenset(("draft", "previewed", "stale", "approved", "applied", "rejected", "failed"))


class ProposalError(ValueError):
    """A proposal failed a boundary or requires another review step."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_sha(value: Any) -> str:
    if isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha(raw)


def _json_bytes(value: dict[str, Any]) -> bytes:
    check_private_content(value)
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_PROPOSAL_BYTES:
        raise ProposalError("proposal exceeds the byte cap")
    validate_schema(value, PROPOSAL_SCHEMA, ROOT)
    return raw


def _atomic_write(path: Path, raw: bytes, *, expected_sha256: str | None = None) -> str:
    _assert_no_reparse(path.parent)
    current = path.read_bytes() if path.exists() else None
    if expected_sha256 is not None and _sha(current or b"") != expected_sha256:
        raise ProposalError(f"{path}: external change requires reread")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return _sha(raw)


def _root(value: str | Path | None) -> Path:
    root = Path(value or ROOT).expanduser()
    _assert_no_reparse(root)
    root = root.resolve()
    if not root.is_dir():
        raise ProposalError(f"Mei root is missing: {root}")
    _assert_no_reparse(root)
    return root


def _proposal_dir(root: Path, *, create: bool = False) -> Path:
    path = root / "memory" / "proposals"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ProposalError(f"proposal store is missing: {path}")
    _assert_no_reparse(path)
    return path


def _proposal_path(root: Path, proposal_id: str) -> Path:
    if not isinstance(proposal_id, str) or not PROPOSAL_ID_PATTERN.fullmatch(proposal_id):
        raise ProposalError("proposal_id is invalid")
    return _safe_relative(_proposal_dir(root), proposal_id + ".md", "proposal")


def _safe_relative(root: Path, locator: str, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise ProposalError(f"{label} must be a non-empty relative path")
    normalized = locator.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ProposalError(f"{label} must be relative")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ProposalError(f"{label} contains an unsafe path")
    candidate = root.joinpath(*parts)
    _assert_no_reparse(candidate)
    try:
        if not candidate.resolve(strict=False).is_relative_to(root.resolve()):
            raise ProposalError(f"{label} escapes its root")
    except ValueError as exc:
        raise ProposalError(f"{label} escapes its root") from exc
    return candidate


def _normalize_target(target_type: str, target_locator: str) -> str:
    if target_type not in TARGET_TYPES:
        raise ProposalError("unsupported proposal target_type")
    if target_type == "relation_pair":
        if target_locator != "memory/markdown":
            raise ProposalError("relation transactions must target memory/markdown")
        return target_locator
    locator = target_locator.replace("\\", "/")
    prefix = "memory/markdown/" if target_type == "personal_markdown" else "memory/candidates/"
    if not locator.startswith(prefix) or locator.endswith("/"):
        raise ProposalError(f"{target_type} targets must stay under {prefix}")
    relative = locator[len(prefix) :]
    parts = relative.split("/")
    if len(parts) != 2 or any(part in ("", ".", "..") for part in parts):
        raise ProposalError("target must be <category>/<stable-id>.")
    if target_type == "personal_markdown" and (not relative.endswith(".md") or parts[0] not in {"profile", "preferences", "domain", "procedural", "project", "feedback"}):
        raise ProposalError("personal target must be a Markdown memory note")
    if target_type == "governed_json" and (not relative.endswith(".json") or parts[0] not in {"profile", "preferences", "domain", "procedural"}):
        raise ProposalError("governed target must be a candidate JSON record")
    _safe_relative(ROOT, locator, "target_locator")
    return locator


def _target_path(root: Path, target_type: str, locator: str) -> tuple[Path, str]:
    locator = _normalize_target(target_type, locator)
    if target_type == "relation_pair":
        return resolve_memory_root(root), ""
    prefix = "memory/markdown/" if target_type == "personal_markdown" else "memory/candidates/"
    relative = locator[len(prefix) :]
    if target_type == "personal_markdown":
        memory_root = resolve_memory_root(root).resolve()
        path = _safe_relative(memory_root, relative, "personal target")
        return path, relative
    return _safe_relative(root, locator, "governed target"), locator


def _source_value(source: dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(source, str):
        source = {"kind": "declared", "locator": source}
    if not isinstance(source, dict):
        raise ProposalError("source must be an object or locator string")
    kind = source.get("kind", "declared")
    locator = source.get("locator")
    if not isinstance(kind, str) or not kind or not isinstance(locator, str) or not locator:
        raise ProposalError("source requires kind and locator")
    result = {"kind": kind, "locator": locator}
    if source.get("sha256") is not None:
        if not isinstance(source["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"]):
            raise ProposalError("source sha256 is invalid")
        result["sha256"] = source["sha256"]
    return result


def _source_status(root: Path, source: dict[str, Any]) -> str:
    if source["kind"] in {"user_decision", "human_review"}:
        return "declared"
    locator = source["locator"]
    path = Path(locator)
    if not path.is_absolute():
        try:
            path = _safe_relative(root, locator, "proposal evidence")
        except (ProposalError, MarkdownMemoryError):
            return "unreadable"
    try:
        _assert_no_reparse(path)
        if not path.resolve().is_relative_to(root.resolve()):
            return "external_review_required"
        if not path.is_file():
            return "missing"
        if path.stat().st_size > 1048576:
            return "over_cap"
        observed = _sha(path.read_bytes())
        expected = source.get("sha256")
        return ("verified" if expected == observed else "changed") if expected else "available_unhashed"
    except (OSError, MarkdownMemoryError):
        return "unreadable"


def _card_path(vault: Path, namespace_path: Path, proposal_id: str, *, create: bool = False) -> Path:
    review = namespace_path / "review"
    if create:
        review.mkdir(parents=True, exist_ok=True)
    prefix = namespace_path.relative_to(vault).as_posix()
    return _safe_relative(vault, f"{prefix + '/' if prefix != '.' else ''}review/{proposal_id}.md", "review card")


def _fence(content: str) -> str:
    longest = max((len(item) for item in re.findall(r"`+", content)), default=0)
    return "`" * max(3, longest + 1)


def _frontmatter(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip()


def _render_card(proposal: dict[str, Any]) -> str:
    value = proposal["suggested_content"]
    if isinstance(value, str):
        language, content = "markdown", value.rstrip("\n")
    else:
        language, content = "json", json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    fence = _fence(content)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "review_card": True,
        "proposal_id": proposal["proposal_id"],
        "status": proposal["status"],
        "target_type": proposal["target_type"],
        "target_locator": proposal["target_locator"],
        "scope": proposal["scope"],
        "suggested_content_sha256": proposal["suggested_content_sha256"],
        "target_base_sha256": proposal["target_base_sha256"],
        "source_locator": proposal["source"]["locator"],
    }
    if proposal["source"].get("sha256"):
        metadata["source_sha256"] = proposal["source"]["sha256"]
    if proposal.get("relationships"):
        metadata["relationships"] = proposal["relationships"]
    reason = proposal.get("reason") or ""
    relationships_line = "- Relationships: " + ", ".join(proposal["relationships"]) + "\n" if proposal.get("relationships") else ""
    return (
        f"---\n{_frontmatter(metadata)}\n---\n"
        f"# Mei Review Proposal {proposal['proposal_id']}\n\n"
        "## Suggested Content\n"
        f"{fence}{language}\n{content}\n{fence}\n\n"
        "## Evidence\n"
        f"- Scope: {proposal['scope']}\n"
        f"- Source: {proposal['source']['locator']}\n"
        f"{relationships_line}"
        f"- Reason: {reason}\n\n"
        "## Review\n"
        "- [ ] I reviewed this exact suggested version.\n"
        "- [ ] Apply only after an explicit `review_proposals.py apply` command.\n\n"
        "Decision: pending\n"
    )


def _split_card(raw: str, source: Path) -> tuple[dict[str, Any], str]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if len(raw.encode("utf-8")) > MAX_CARD_BYTES:
        raise ProposalError(f"{source}: review card exceeds the byte cap")
    if not raw.startswith("---\n"):
        raise ProposalError(f"{source}: review card frontmatter is missing")
    marker = raw.find("\n---\n", 4)
    if marker < 0:
        raise ProposalError(f"{source}: review card frontmatter is malformed")
    try:
        frontmatter = yaml.load(raw[4:marker], Loader=_UniqueLoader)
    except (yaml.YAMLError, MarkdownMemoryError) as exc:
        raise ProposalError(f"{source}: invalid review card frontmatter") from exc
    if not isinstance(frontmatter, dict) or frontmatter.get("review_card") is not True:
        raise ProposalError(f"{source}: review card marker is invalid")
    return frontmatter, raw[marker + len("\n---\n") :]


def _card_content(raw: str, source: Path) -> str | dict[str, Any]:
    _, body = _split_card(raw, source)
    match = re.search(r"(?ms)^## Suggested Content\s*\n(.*?)(?=^## |\Z)", body)
    if not match:
        raise ProposalError(f"{source}: suggested content section is missing")
    section = match.group(1).strip()
    fence = re.match(r"(?s)^(`{3,}|~{3,})(?:[^\n]*)\n(.*?)\n\1\s*$", section)
    if not fence:
        raise ProposalError(f"{source}: suggested content fence is invalid")
    language = section[len(fence.group(1)) : section.find("\n")].strip().casefold()
    content = fence.group(2)
    if language == "json":
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProposalError(f"{source}: governed JSON content is invalid") from exc
        if not isinstance(value, dict):
            raise ProposalError(f"{source}: governed JSON content must be an object")
        return value
    return content + ("\n" if content else "")


def _read_proposal(path: Path, root: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_PROPOSAL_BYTES:
            raise ProposalError("proposal exceeds the byte cap")
        text = raw.decode("utf-8")
        if path.suffix.casefold() == ".md":
            if not text.startswith("---\n") or "\n---\n" not in text[4:]:
                raise ProposalError("proposal frontmatter is missing")
            marker = text.find("\n---\n", 4)
            value = yaml.load(text[4:marker], Loader=_UniqueLoader)
        else:
            value = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ProposalError(f"{path}: invalid proposal") from exc
    if not isinstance(value, dict):
        raise ProposalError(f"{path}: proposal must be an object")
    validate_schema(value, PROPOSAL_SCHEMA, ROOT)
    check_private_content(value)
    if _content_sha(value["suggested_content"]) != value["suggested_content_sha256"]:
        raise ProposalError("proposal suggested content hash mismatch")
    return value


def _write_proposal(path: Path, value: dict[str, Any], root: Path, *, expected_sha256: str | None = None) -> str:
    if path.suffix.casefold() == ".md":
        body = "## Reason\n\n" + str(value.get("reason", "")).strip() + "\n\n"
        body += "## Proposed Change\n\n" + str(value.get("target_locator", "")) + "\n\n"
        body += "## Diff\n\n" + str((value.get("preview") or {}).get("diff", "Pending preview.")) + "\n\n"
        body += "## Unresolved\n\n" + "\n".join(f"- {item}" for item in (value.get("preview") or {}).get("unresolved", [])) + "\n\n"
        body += "## Approval History\n\n" + "\n".join(f"- {item.get('event')}: {item.get('at')}" for item in value.get("history", [])) + "\n"
        raw = ("---\n" + _frontmatter(value) + "\n---\n" + body).encode("utf-8")
        check_private_content(value)
        validate_schema(value, PROPOSAL_SCHEMA, ROOT)
    else:
        raw = _json_bytes(value)
    if len(raw) > MAX_PROPOSAL_BYTES:
        raise ProposalError("proposal exceeds the byte cap")
    return _atomic_write(path, raw, expected_sha256=expected_sha256)


def _proposal_id(target_locator: str, source: dict[str, Any], scope: str) -> str:
    # Keep one unsubmitted draft per target and scope; changing evidence updates
    # that draft instead of creating one record per agent turn.
    seed = "\n".join((target_locator, scope))
    return "proposal-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _event(proposal: dict[str, Any], name: str, **fields: Any) -> None:
    entry = {"event": name, "at": _now()}
    entry.update({key: value for key, value in fields.items() if value is not None})
    proposal.setdefault("history", []).append(entry)
    proposal["history"] = proposal["history"][-50:]


def _proposal_result(proposal: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    result = {
        "status": status or proposal["status"],
        "proposal_id": proposal["proposal_id"],
        "target_type": proposal["target_type"],
        "target_locator": proposal["target_locator"],
        "scope": proposal["scope"],
        "suggested_content_sha256": proposal["suggested_content_sha256"],
        "target_base_sha256": proposal["target_base_sha256"],
        "card_locator": proposal["card_locator"],
    }
    if proposal.get("preview"):
        result["preview"] = proposal["preview"]
    if proposal.get("receipt"):
        result["receipt"] = proposal["receipt"]
    return result


def _refresh_card(proposal: dict[str, Any], card_path: Path, raw: bytes, root: Path) -> bool:
    card_hash = _sha(raw)
    if card_hash == proposal.get("card_sha256"):
        return False
    try:
        edited = _card_content(raw.decode("utf-8"), card_path)
    except (UnicodeError, ProposalError) as exc:
        raise ProposalError(str(exc)) from exc
    if _content_sha(edited) != proposal["suggested_content_sha256"]:
        proposal["suggested_content"] = edited
        proposal["suggested_content_sha256"] = _content_sha(edited)
        proposal["status"] = "draft"
        proposal["preview"] = None
        proposal["approval"] = None
        _event(proposal, "card_edited", suggested_content_sha256=proposal["suggested_content_sha256"])
    proposal["updated_at"] = _now()
    return True


def _update_card_metadata(card_path: Path, proposal: dict[str, Any], *, expected_sha256: str | None = None) -> str:
    raw = card_path.read_bytes()
    frontmatter, body = _split_card(raw.decode("utf-8"), card_path)
    frontmatter.update({
        "status": proposal["status"],
        "suggested_content_sha256": proposal["suggested_content_sha256"],
        "target_base_sha256": proposal["target_base_sha256"],
    })
    updated = f"---\n{_frontmatter(frontmatter)}\n---\n{body}".encode("utf-8")
    return _atomic_write(card_path, updated, expected_sha256=expected_sha256 or _sha(raw))


def _target_snapshot(root: Path, proposal: dict[str, Any]) -> tuple[Path, bytes | None, str | None]:
    path, _ = _target_path(root, proposal["target_type"], proposal["target_locator"])
    if proposal["target_type"] == "relation_pair":
        from relations import snapshot
        raw = snapshot(path, proposal["suggested_content"])
        return path, raw, _sha(raw)
    raw = path.read_bytes() if path.is_file() else None
    return path, raw, _sha(raw) if raw is not None else None


def _format_content(value: Any) -> str:
    if isinstance(value, str):
        return value.rstrip("\n") + "\n"
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _preview_payload(root: Path, proposal: dict[str, Any], current_raw: bytes | None, current_sha: str | None) -> dict[str, Any]:
    current_text = current_raw.decode("utf-8") if current_raw is not None else ""
    proposed_text = _format_content(proposal["suggested_content"])
    diff = "".join(difflib.unified_diff(
        current_text.splitlines(keepends=True), proposed_text.splitlines(keepends=True),
        fromfile=proposal["target_locator"] if current_raw is not None else "/dev/null",
        tofile=proposal["target_locator"],
    ))
    if len(diff) > 32_000:
        diff = diff[:32_000] + "\n[diff truncated]\n"
    source_status = _source_status(root, proposal["source"])
    unresolved = []
    if source_status not in {"verified", "available_unhashed", "declared"}:
        unresolved.append("source_unavailable")
    if current_sha != proposal["target_base_sha256"]:
        unresolved.append("target_changed")
    return {
        "target_current_sha256": current_sha,
        "target_matches_base": current_sha == proposal["target_base_sha256"],
        "source_status": source_status,
        "unresolved": unresolved,
        "diff": diff,
        "reviewed_content_sha256": proposal["suggested_content_sha256"],
    }


def _duplicate_ids(root: Path, proposal: dict[str, Any]) -> list[str]:
    duplicates = []
    for path in sorted(_proposal_dir(root).glob("*.md")):
        if path.stem == proposal["proposal_id"]:
            continue
        try:
            other = _read_proposal(path, root)
        except (ProposalError, OSError, ValueError):
            continue
        if other["target_locator"] == proposal["target_locator"] or other["suggested_content_sha256"] == proposal["suggested_content_sha256"]:
            duplicates.append(other["proposal_id"])
    return duplicates


def _render_review_index(root: Path, vault: Path, namespace_path: Path) -> str:
    review = namespace_path / "review"
    review.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(_proposal_dir(root).glob("*.md")):
        try:
            proposal = _read_proposal(path, root)
        except (ProposalError, OSError, ValueError):
            continue
        rows.append(proposal)
    rows.sort(key=lambda value: (value["status"], value["proposal_id"]))
    lines = [
        "---", "mei_generated: true", "review_workbench: true", "---", "# Mei Review Workbench", "",
        "Generated proposal cards are review inputs. A checkbox alone never authorizes an apply.", "",
    ]
    if not rows:
        lines.append("No review proposals.")
    else:
        for proposal in rows:
            lines.append(f"- [[review/{proposal['proposal_id']}]] `{proposal['status']}` {proposal['target_locator']}")
    content = "\n".join(lines) + "\n"
    index_path = review / "index.md"
    if index_path.exists():
        current = index_path.read_bytes()
        if current != content.encode("utf-8") and b"mei_generated: true" not in current:
            return "manual_drift"
    _atomic_write(index_path, content.encode("utf-8"), expected_sha256=_sha(index_path.read_bytes()) if index_path.exists() else None)
    return "updated"


def _open_vault(root: Path, vault_root: str | Path | None, namespace: str | None) -> tuple[Path, Path, str]:
    if vault_root is None:
        # Review cards are ordinary Markdown; no Obsidian app/settings are needed.
        vault_root = resolve_memory_root(root)
        namespace = "."
    return resolve_vault(vault_root, namespace, config_path=root / "local" / "paths.toml")


def draft_proposal(
    target_type: str,
    target_locator: str,
    suggested_content: str | dict[str, Any],
    *,
    scope: str,
    source: dict[str, Any] | str,
    reason: str = "",
    relationships: list[str] | None = None,
    proposal_id: str | None = None,
    mei_root: str | Path | None = None,
    vault_root: str | Path | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    root = _root(mei_root)
    source_value = _source_value(source)
    locator = _normalize_target(target_type, target_locator)
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 240:
        raise ProposalError("scope must be a non-empty bounded string")
    if target_type == "personal_markdown" and not isinstance(suggested_content, str):
        raise ProposalError("personal Markdown content must be a string")
    if target_type == "governed_json" and not isinstance(suggested_content, dict):
        raise ProposalError("governed JSON content must be an object")
    check_private_content({"suggested_content": suggested_content, "scope": scope, "reason": reason})
    target_path, memory_locator = _target_path(root, target_type, locator)
    if target_type == "personal_markdown":
        try:
            parse_note(suggested_content, target_path)
        except MarkdownMemoryError as exc:
            raise ProposalError(str(exc)) from exc
    else:
        try:
            json.dumps(suggested_content, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ProposalError("governed JSON content is not serializable") from exc
    current_raw = target_path.read_bytes() if target_path.is_file() else None
    if target_type == "relation_pair":
        from relations import snapshot
        current_raw = snapshot(resolve_memory_root(root), suggested_content)
    current_sha = _sha(current_raw) if current_raw is not None else None
    pid = proposal_id or _proposal_id(locator, source_value, scope)
    if not PROPOSAL_ID_PATTERN.fullmatch(pid):
        raise ProposalError("proposal_id is invalid")
    directory = _proposal_dir(root, create=True)
    path = directory / f"{pid}.md"
    with _context_lock(directory, 5.0):
        existing = _read_proposal(path, root) if path.exists() else None
        vault, namespace_path, namespace_name = _open_vault(root, vault_root, namespace)
        card_path = _card_path(vault, namespace_path, pid, create=True)
        if existing and existing["status"] in {"approved", "applied", "rejected"}:
            result = _proposal_result(existing, status="protected")
            result["required_action"] = "explicit_new_proposal"
            return result
        if existing and card_path.exists() and existing.get("card_sha256") and _sha(card_path.read_bytes()) != existing["card_sha256"]:
            result = _proposal_result(existing, status="card_drifted")
            result["required_action"] = "preview_edited_card"
            return result
        now = _now()
        proposal = existing or {
            "schema_version": SCHEMA_VERSION,
            "proposal_id": pid,
            "status": "draft",
            "target_type": target_type,
            "target_locator": locator,
            "scope": scope,
            "source": source_value,
            "reason": reason,
            "relationships": relationships or [],
            "suggested_content": suggested_content,
            "suggested_content_sha256": _content_sha(suggested_content),
            "target_base_sha256": current_sha,
            "target_exists": current_raw is not None,
            "card_locator": f"{namespace_name + '/' if namespace_name not in {'', '.'} else ''}review/{pid}.md",
            "card_sha256": None,
            "preview": None,
            "approval": None,
            "receipt": None,
            "history": [],
            "created_at": now,
            "updated_at": now,
        }
        proposal.update({
            "status": "draft", "target_type": target_type, "target_locator": locator,
            "scope": scope, "source": source_value, "reason": reason,
            "relationships": relationships or [], "suggested_content": suggested_content,
            "suggested_content_sha256": _content_sha(suggested_content),
            "target_base_sha256": current_sha, "target_exists": current_raw is not None,
            "updated_at": now, "preview": None, "approval": None,
        })
        _event(proposal, "drafted", suggested_content_sha256=proposal["suggested_content_sha256"])
        if card_path.exists():
            card_hash = _sha(card_path.read_bytes())
            if existing and card_hash != existing.get("card_sha256"):
                raise ProposalError("review card changed; preview it before updating")
        card_hash = _atomic_write(card_path, _render_card(proposal).encode("utf-8"), expected_sha256=_sha(card_path.read_bytes()) if card_path.exists() else None)
        proposal["card_sha256"] = card_hash
        _write_proposal(path, proposal, root)
        index_status = _render_review_index(root, vault, namespace_path)
    result = _proposal_result(proposal)
    result.update({"status": "drafted", "index_status": index_status, "required_action": "preview"})
    return result


def preview_proposal(proposal_id: str, *, mei_root: str | Path | None = None, vault_root: str | Path | None = None, namespace: str | None = None) -> dict[str, Any]:
    root = _root(mei_root)
    path = _proposal_path(root, proposal_id)
    with _context_lock(_proposal_dir(root), 5.0):
        proposal = _read_proposal(path, root)
        vault, namespace_path, _ = _open_vault(root, vault_root, namespace)
        card_path = _card_path(vault, namespace_path, proposal_id)
        raw = card_path.read_bytes()
        before_card = _sha(raw)
        _refresh_card(proposal, card_path, raw, root)
        target_path, current_raw, current_sha = _target_snapshot(root, proposal)
        preview = _preview_payload(root, proposal, current_raw, current_sha)
        proposal["preview"] = preview
        proposal["status"] = "previewed"
        proposal["updated_at"] = _now()
        _event(proposal, "previewed", target_current_sha256=current_sha)
        _update_card_metadata(card_path, proposal, expected_sha256=before_card)
        proposal["card_sha256"] = _sha(card_path.read_bytes())
        _write_proposal(path, proposal, root)
        _render_review_index(root, vault, namespace_path)
    result = _proposal_result(proposal)
    result.update({"status": "previewed", "target_path": str(target_path), "required_action": "explicit_apply_or_reject"})
    return result


def status_proposals(proposal_id: str | None = None, *, mei_root: str | Path | None = None) -> dict[str, Any]:
    root = _root(mei_root)
    directory = root / "memory" / "proposals"
    _assert_no_reparse(directory)
    try:
        directory.stat()
    except FileNotFoundError:
        if proposal_id is None:
            return {"status": "ok", "proposals": []}
    directory = _proposal_dir(root)
    # Unlike glob, iterdir propagates directory permission errors.
    paths = [_proposal_path(root, proposal_id)] if proposal_id else sorted(
        path for path in directory.iterdir() if path.match("*.md")
    )
    entries = []
    for path in paths:
        proposal = _read_proposal(path, root)
        _, _, current_sha = _target_snapshot(root, proposal)
        item = _proposal_result(proposal)
        item["target_current_sha256"] = current_sha
        item["target_matches_base"] = current_sha == proposal["target_base_sha256"]
        item["source_status"] = _source_status(root, proposal["source"])
        item["duplicate_proposals"] = _duplicate_ids(root, proposal)
        entries.append(item)
    return {"status": "ok", "proposals": entries}


def validate_proposal_store(root: str | Path = ROOT) -> int:
    """Validate local proposal records without treating them as memory."""
    root_path = _root(root)
    directory = root_path / "memory" / "proposals"
    if not directory.exists():
        return 0
    if not directory.is_dir():
        raise ProposalError("memory/proposals is not a directory")
    ids: set[str] = set()
    for path in sorted(directory.glob("*.md")):
        proposal = _read_proposal(path, root_path)
        if path.stem != proposal["proposal_id"] or proposal["proposal_id"] in ids:
            raise ProposalError(f"invalid or duplicate proposal id: {path}")
        ids.add(proposal["proposal_id"])
        _normalize_target(proposal["target_type"], proposal["target_locator"])
    return len(ids)


def check_review_workbench(
    mei_root: str | Path | None = None,
    vault_root: str | Path | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Check review cards without changing proposal or vault files."""
    root = _root(mei_root)
    try:
        vault, namespace_path, namespace_name = _open_vault(root, vault_root, namespace)
        errors: list[str] = []
        count = 0
        proposal_dir = root / "memory" / "proposals"
        for path in sorted(proposal_dir.glob("*.md")) if proposal_dir.is_dir() else []:
            proposal = _read_proposal(path, root)
            count += 1
            card = _card_path(vault, namespace_path, proposal["proposal_id"])
            if not card.is_file():
                errors.append(f"{proposal['proposal_id']}: review card is missing")
                continue
            raw = card.read_bytes()
            if _sha(raw) != proposal.get("card_sha256"):
                errors.append(f"{proposal['proposal_id']}: review card drifted")
            try:
                _split_card(raw.decode("utf-8"), card)
            except (ProposalError, UnicodeError) as exc:
                errors.append(str(exc))
        index = namespace_path / "review" / "index.md"
        if index.is_file() and b"review_workbench: true" not in index.read_bytes():
            errors.append("review/index.md: generated marker is missing")
        return {"status": "invalid" if errors else "ok", "namespace": namespace_name, "cards": count, "errors": errors}
    except (ProposalError, OSError, UnicodeError, ValueError) as exc:
        return {"status": "invalid", "errors": [str(exc)]}


def apply_proposal(proposal_id: str, *, confirm: bool = False, mei_root: str | Path | None = None, vault_root: str | Path | None = None, namespace: str | None = None) -> dict[str, Any]:
    root = _root(mei_root)
    path = _proposal_path(root, proposal_id)
    with _context_lock(_proposal_dir(root), 5.0):
        proposal = _read_proposal(path, root)
        if proposal["status"] == "applied":
            return _proposal_result(proposal, status="already_applied")
        if proposal["status"] == "rejected":
            return _proposal_result(proposal, status="rejected")
        vault, namespace_path, _ = _open_vault(root, vault_root, namespace)
        card_path = _card_path(vault, namespace_path, proposal_id)
        card_raw = card_path.read_bytes()
        if _sha(card_raw) != proposal.get("card_sha256"):
            return {**_proposal_result(proposal, status="repreview_required"), "required_action": "preview_edited_card"}
        if proposal["target_type"] == "governed_json":
            return {**_proposal_result(proposal, status="requires_curator"), "required_action": "mei-curator", "promoted": False}
        if not confirm:
            return {**_proposal_result(proposal, status="authorization_required"), "required_action": "repeat with explicit --confirm"}
        if proposal["status"] not in {"previewed", "approved", "failed"} or not proposal.get("preview") or proposal["preview"].get("reviewed_content_sha256") != proposal["suggested_content_sha256"]:
            return {**_proposal_result(proposal, status="preview_required"), "required_action": "preview"}
        target_path, current_raw, current_sha = _target_snapshot(root, proposal)
        desired_sha = proposal["suggested_content_sha256"]
        if proposal["target_type"] == "relation_pair":
            from relations import snapshot
            desired_sha = _sha(snapshot(resolve_memory_root(root), proposal["suggested_content"], desired=True))
        if proposal["status"] in {"approved", "failed"} and current_sha == desired_sha and current_sha != proposal["target_base_sha256"]:
            approval = proposal.get("approval") or {}
            if not approval.get("explicit") or approval.get("approved_content_sha256") != proposal["suggested_content_sha256"]:
                raise ProposalError("recovery requires the exact previously approved content hash")
            proposal["status"] = "applied"
            proposal["receipt"] = {
                "applied_at": _now(), "target_sha256": current_sha,
                "writer": "relations.apply_bundle" if proposal["target_type"] == "relation_pair" else "memory_markdown.write_note", "write_status": "recovered",
            }
            proposal["updated_at"] = _now()
            _event(proposal, "applied_recovered", target_sha256=current_sha)
            _write_proposal(path, proposal, root)
            _render_review_index(root, vault, namespace_path)
            return {**_proposal_result(proposal, status="already_applied"), "receipt": proposal["receipt"]}
        if current_sha != proposal["target_base_sha256"]:
            proposal["status"] = "stale"
            proposal["updated_at"] = _now()
            _event(proposal, "stale_target", target_current_sha256=current_sha)
            _write_proposal(path, proposal, root)
            return {**_proposal_result(proposal, status="stale"), "required_action": "reread_and_redraft"}
        proposal["status"] = "approved"
        proposal["approval"] = proposal.get("approval") or {
            "approved_content_sha256": proposal["suggested_content_sha256"], "approved_at": _now(), "explicit": True,
        }
        proposal["updated_at"] = _now()
        _event(proposal, "explicit_apply_requested", approved_content_sha256=proposal["suggested_content_sha256"])
        _write_proposal(path, proposal, root)
        try:
            if not proposal["approval"].get("explicit") or proposal["approval"].get("approved_content_sha256") != _content_sha(proposal["suggested_content"]):
                raise ProposalError("approved content hash mismatch")
            if proposal["preview"].get("unresolved"):
                raise ProposalError("resolve preview diagnostics before applying")
            if _source_status(root, proposal["source"]) not in {"verified", "available_unhashed", "declared"}:
                raise ProposalError("proposal evidence changed or is unavailable; reread and preview again")
            _, memory_locator = _target_path(root, proposal["target_type"], proposal["target_locator"])
            if proposal["target_type"] == "relation_pair":
                from relations import apply_bundle
                write_result = apply_bundle(resolve_memory_root(root), proposal["suggested_content"],
                                            expected_sha256=proposal["target_base_sha256"],
                                            approved_content_sha256=proposal["approval"]["approved_content_sha256"])
            else:
                write_result = write_note(
                    resolve_memory_root(root), memory_locator, proposal["suggested_content"],
                    expected_sha256=proposal["target_base_sha256"],
                    approved_content_sha256=proposal["approval"]["approved_content_sha256"],
                )
        except (MarkdownMemoryError, OSError, ProposalError) as exc:
            proposal["status"] = "failed"
            proposal["updated_at"] = _now()
            _event(proposal, "apply_failed", reason=type(exc).__name__)
            _write_proposal(path, proposal, root)
            return {**_proposal_result(proposal, status="failed"), "required_action": "inspect_failure_and_retry", "error": str(exc)}
        observed = _target_snapshot(root, proposal)[2]
        if observed != desired_sha:
            proposal["status"] = "failed"
            _event(proposal, "apply_readback_failed")
            _write_proposal(path, proposal, root)
            return {**_proposal_result(proposal, status="failed"), "required_action": "inspect_target_readback"}
        proposal["status"] = "applied"
        proposal["receipt"] = {
            "applied_at": _now(), "target_sha256": observed,
            "writer": "relations.apply_bundle" if proposal["target_type"] == "relation_pair" else "memory_markdown.write_note", "write_status": write_result.get("status"),
        }
        proposal["updated_at"] = _now()
        _event(proposal, "applied", target_sha256=observed)
        _write_proposal(path, proposal, root)
        try:
            projection = sync_obsidian(vault, f"proposal-{proposal_id}", memory_root=resolve_memory_root(root), namespace=namespace, config_path=root / "local" / "paths.toml")
        except (OSError, MarkdownMemoryError, ValueError) as exc:
            projection = {"status": "projection_stale", "reason": type(exc).__name__}
        _render_review_index(root, vault, namespace_path)
    result = _proposal_result(proposal, status="applied")
    result.update({"receipt": proposal["receipt"], "projection": projection})
    return result


def reject_proposal(proposal_id: str, reason: str, *, mei_root: str | Path | None = None, vault_root: str | Path | None = None, namespace: str | None = None) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ProposalError("a bounded rejection reason is required")
    root = _root(mei_root)
    path = _proposal_path(root, proposal_id)
    with _context_lock(_proposal_dir(root), 5.0):
        proposal = _read_proposal(path, root)
        if proposal["status"] == "applied":
            return {**_proposal_result(proposal, status="protected"), "required_action": "archive_or_new_proposal"}
        proposal["status"] = "rejected"
        proposal["updated_at"] = _now()
        proposal["approval"] = None
        _event(proposal, "rejected", reason=reason)
        _write_proposal(path, proposal, root)
        vault, namespace_path, _ = _open_vault(root, vault_root, namespace)
        card_path = _card_path(vault, namespace_path, proposal_id)
        if card_path.exists():
            before = _sha(card_path.read_bytes())
            _update_card_metadata(card_path, proposal, expected_sha256=before)
            proposal["card_sha256"] = _sha(card_path.read_bytes())
            _write_proposal(path, proposal, root)
        _render_review_index(root, vault, namespace_path)
    return {**_proposal_result(proposal, status="rejected"), "reason_recorded": True}


def _read_content(path: Path, target_type: str) -> str | dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if target_type in {"governed_json", "relation_pair"}:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ProposalError("governed content file must contain a JSON object")
        return value
    return raw


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("draft", "preview", "status", "apply", "reject"))
    parser.add_argument("--mei-root", type=Path)
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--namespace")
    parser.add_argument("--proposal-id")
    parser.add_argument("--target-type", choices=sorted(TARGET_TYPES))
    parser.add_argument("--target")
    parser.add_argument("--content-file", type=Path)
    parser.add_argument("--scope")
    parser.add_argument("--source")
    parser.add_argument("--source-kind", default="declared")
    parser.add_argument("--source-sha256")
    parser.add_argument("--reason", default="")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "draft":
            if not all((args.target_type, args.target, args.content_file, args.scope, args.source)):
                raise ProposalError("draft requires --target-type, --target, --content-file, --scope and --source")
            content = _read_content(args.content_file, args.target_type)
            source = {"kind": args.source_kind, "locator": args.source}
            if args.source_sha256:
                source["sha256"] = args.source_sha256
            result = draft_proposal(args.target_type, args.target, content, scope=args.scope, source=source,
                                    reason=args.reason, proposal_id=args.proposal_id, mei_root=args.mei_root,
                                    vault_root=args.vault, namespace=args.namespace)
        elif args.command == "preview":
            result = preview_proposal(args.proposal_id or "", mei_root=args.mei_root, vault_root=args.vault, namespace=args.namespace)
        elif args.command == "status":
            result = status_proposals(args.proposal_id, mei_root=args.mei_root)
        elif args.command == "apply":
            result = apply_proposal(args.proposal_id or "", confirm=args.confirm, mei_root=args.mei_root, vault_root=args.vault, namespace=args.namespace)
        else:
            result = reject_proposal(args.proposal_id or "", args.reason, mei_root=args.mei_root, vault_root=args.vault, namespace=args.namespace)
    except (ProposalError, MarkdownMemoryError, OSError, UnicodeError, ValueError, KeyError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"drafted", "previewed", "ok", "applied", "already_applied", "rejected"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
