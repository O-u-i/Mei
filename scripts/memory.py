"""Unified Markdown memory CLI.

This is the stable command surface for runtime memory.  The implementation
stays in the existing bounded Markdown writer and proposal handler so older
imports keep working while callers move to ``python scripts/memory.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from memory_markdown import (
    MemoryError,
    doctor,
    initialize_memory,
    read_memory,
    resolve_memory_root,
    write_note,
)


def search_memory(root: str | Path, query: str, *, schema_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Return active notes whose id, metadata, or Markdown body matches query."""
    if not isinstance(query, str) or not query.strip():
        raise MemoryError("search query must be non-empty")
    needle = query.casefold().strip()
    notes = read_memory(root, schema_root=schema_root)["notes"]
    matches: list[dict[str, Any]] = []
    for note in notes:
        haystack = "\n".join(
            str(note.get(key, ""))
            for key in ("memory_id", "category", "scope", "source", "body")
        ).casefold()
        if needle in haystack:
            matches.append({key: value for key, value in note.items() if key != "body"} | {"body": note["body"]})
    return matches


# Small public aliases keep the unified module useful as a library as well as
# a CLI, without duplicating the Markdown writer.
def read(root: str | Path, memory_ids: set[str] | None = None, *, schema_root: str | Path | None = None) -> dict[str, Any]:
    return read_memory(root, memory_ids, schema_root=schema_root)


def search(root: str | Path, query: str, *, schema_root: str | Path | None = None) -> list[dict[str, Any]]:
    return search_memory(root, query, schema_root=schema_root)


def write(root: str | Path, locator: str, content: str, *, expected_sha256: str | None = None, schema_root: str | Path | None = None) -> dict[str, Any]:
    return write_note(root, locator, content, expected_sha256=expected_sha256, schema_root=schema_root)


def _relation_module() -> Any:
    """Load the optional relation implementation without importing it eagerly."""
    try:
        import relations  # type: ignore
    except ImportError as exc:
        raise MemoryError("relation commands are unavailable") from exc
    return relations


def _relation_command(args: argparse.Namespace) -> dict[str, Any]:
    module = _relation_module()
    root = args.root or resolve_memory_root()
    action = args.relation_action
    if action == "check":
        fn = getattr(module, "check", None) or getattr(module, "check_relations", None)
        if fn is None:
            raise MemoryError("relation checker is unavailable")
        return fn(root)
    if action == "repair":
        fn = getattr(module, "repair", None) or getattr(module, "repair_relations", None) or getattr(module, "repair_proposal", None)
        if fn is None:
            raise MemoryError("relation repair is unavailable")
        try:
            return fn(root, proposal=args.proposal)
        except TypeError:
            return fn(root)
    if not args.source or not args.target:
        raise MemoryError(f"relations {action} requires SOURCE and TARGET")
    fn = getattr(module, action, None)
    if fn is None:
        raise MemoryError(f"relation operation is unavailable: {action}")
    kwargs: dict[str, Any] = {"root": root}
    if args.relation_type:
        kwargs["relation_type"] = args.relation_type
    try:
        return fn(args.source, args.target, **kwargs)
    except TypeError:
        # Accept the common ``fn(root, source, target, ...)`` module shape.
        return fn(root, args.source, args.target, **({"relation_type": args.relation_type} if args.relation_type else {}))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, help="Markdown vault root; defaults to local/paths.toml")
    common.add_argument("--schema-root", type=Path)
    common.add_argument("--capability", choices=("P1", "P2", "P3"), default="P1")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common])
    doctor_parser = sub.add_parser("doctor", parents=[common])

    read_parser = sub.add_parser("read", parents=[common])
    read_parser.add_argument("--id", action="append", dest="ids")

    search_parser = sub.add_parser("search", parents=[common])
    search_parser.add_argument("query", nargs="?")
    search_parser.add_argument("--query", dest="query_flag")

    write_parser = sub.add_parser("write", parents=[common])
    write_parser.add_argument("--path", required=True)
    write_parser.add_argument("--content-file", required=True, type=Path)
    write_parser.add_argument("--expected-sha256")
    write_parser.add_argument("--reviewed-personal", action="store_true")

    propose = sub.add_parser("propose", parents=[common])
    propose.add_argument("--proposal-id")
    propose.add_argument("--target-type", default="personal_markdown", choices=("personal_markdown", "governed_json"))
    propose.add_argument("--target", required=True)
    propose.add_argument("--content-file", required=True, type=Path)
    propose.add_argument("--scope", required=True)
    propose.add_argument("--source", required=True)
    propose.add_argument("--source-kind", default="declared")
    propose.add_argument("--source-sha256")
    propose.add_argument("--reason", default="")

    apply = sub.add_parser("apply", parents=[common])
    apply.add_argument("proposal_id", nargs="?")
    apply.add_argument("--proposal-id", dest="proposal_id_flag")
    apply.add_argument("--confirm", action="store_true")

    relation = sub.add_parser("relations", parents=[common])
    relation.add_argument("relation_action", choices=("add", "remove", "check", "repair"))
    relation.add_argument("source", nargs="?")
    relation.add_argument("target", nargs="?")
    relation.add_argument("--type", dest="relation_type")
    relation.add_argument("--proposal", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.root or resolve_memory_root()
        schema_root = args.schema_root
        if args.command == "init":
            result: dict[str, Any] = {"status": "initialized", "created": initialize_memory(root)}
        elif args.command == "doctor":
            result = doctor(root, schema_root=schema_root)
        elif args.command == "read":
            result = read_memory(root, set(args.ids or []), schema_root=schema_root)
            result["notes"] = [{key: value for key, value in note.items() if key != "sha256"} for note in result["notes"]]
        elif args.command == "search":
            query = args.query_flag or args.query
            result = {"status": "ok", "query": query, "notes": search_memory(root, query or "", schema_root=schema_root)}
        elif args.command == "write":
            if args.capability == "P1":
                raise MemoryError("P2 capability is required for direct low-risk note creation")
            if not args.reviewed_personal:
                raise MemoryError("explicit --reviewed-personal confirmation is required")
            result = write_note(root, args.path, args.content_file.read_text(encoding="utf-8"), expected_sha256=args.expected_sha256, schema_root=schema_root)
        elif args.command == "propose":
            from review_proposals import draft_proposal

            content = args.content_file.read_text(encoding="utf-8")
            source = {"kind": args.source_kind, "locator": args.source}
            if args.source_sha256:
                source["sha256"] = args.source_sha256
            result = draft_proposal(args.target_type, args.target, content, scope=args.scope, source=source, reason=args.reason, proposal_id=args.proposal_id, mei_root=root)
        elif args.command == "apply":
            if args.capability != "P3":
                raise MemoryError("P3 capability is required for approved apply")
            from review_proposals import apply_proposal

            proposal_id = args.proposal_id_flag or args.proposal_id
            if not proposal_id:
                raise MemoryError("apply requires a proposal id")
            result = apply_proposal(proposal_id, confirm=args.confirm, mei_root=root)
        else:
            result = _relation_command(args)
    except (MemoryError, OSError, UnicodeError, ValueError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"error", "invalid"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
