"""Save one reviewed continuity handoff and optionally create a draft proposal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _common import ROOT
from check_continuity import ContinuityError
from review_proposals import ProposalError, draft_proposal
from sync_continuity import sync_context


def save_handoff(
    repo_root: str | Path,
    context_id: str,
    snapshot: dict[str, Any],
    project: dict[str, Any],
    *,
    writer_session_id: str,
    mei_root: str | Path | None = None,
    reconcile: bool = False,
    memory_draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sync the selected handoff before creating an isolated memory proposal."""
    result = sync_context(
        repo_root, context_id, snapshot, project,
        writer_session_id=writer_session_id, mei_root=mei_root, reconcile=reconcile,
    )
    if result.get("status") not in {"synced", "noop"}:
        return {"status": "handoff_" + result.get("status", "failed"), "continuity": result}
    if not memory_draft:
        return {"status": "handoff_saved" if result["status"] == "synced" else "handoff_noop", "continuity": result}
    required = ("target_type", "target_locator", "content", "scope", "source")
    if any(key not in memory_draft for key in required):
        raise ProposalError("memory_draft requires target_type, target_locator, content, scope and source")
    proposal = draft_proposal(
        memory_draft["target_type"], memory_draft["target_locator"], memory_draft["content"],
        scope=memory_draft["scope"], source=memory_draft["source"],
        reason=memory_draft.get("reason", ""), relationships=memory_draft.get("relationships"),
        proposal_id=memory_draft.get("proposal_id"), mei_root=mei_root,
    )
    return {"status": "handoff_saved", "continuity": result, "proposal": proposal}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--mei-root", type=Path, default=ROOT)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--writer-session-id", required=True)
    parser.add_argument("--memory-draft", type=Path)
    parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        project = json.loads(args.project.read_text(encoding="utf-8"))
        memory_draft = json.loads(args.memory_draft.read_text(encoding="utf-8")) if args.memory_draft else None
        result = save_handoff(
            args.repo_root, args.context_id, snapshot, project,
            writer_session_id=args.writer_session_id, mei_root=args.mei_root,
            reconcile=args.reconcile, memory_draft=memory_draft,
        )
    except (ContinuityError, ProposalError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"handoff_saved", "handoff_noop"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
