"""Synthetic checks for the isolated proposal and Obsidian review lifecycle."""

from __future__ import annotations

import hashlib
import sys
import uuid
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_markdown import initialize_memory, read_note, write_note  # noqa: E402
from review_proposals import (  # noqa: E402
    apply_proposal,
    draft_proposal,
    preview_proposal,
    reject_proposal,
    status_proposals,
)


def fixture_root() -> Path:
    root = ROOT / ".tmp" / "proposal-tests" / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def note(memory_id: str, body: str) -> str:
    return (
        "---\n"
        "schema_version: v0.2.0\n"
        f"memory_id: {memory_id}\n"
        "category: project\n"
        "status: active\n"
        "scope: synthetic project\n"
        "source: user_decision\nsource_format: markdown\nprovenance:\n  - source: user_decision\n    locator: test\n    evidence_type: user_decision\n    verified_at: null\nreview_due_at: null\nreview_triggers: []\nreferences: []\nconflicts: []\nrelated: []\nrelations: []\nupdated_at: '2026-10-15T00:00:00Z'\nmigrated_from: null\nmigrated_at: null\n"
        "---\n"
        f"{body}\n"
    )


def setup(root: Path):
    memory = root / "memory" / "markdown"
    vault = root / "vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    initialize_memory(memory)
    (root / "local").mkdir()
    (root / "local" / "paths.toml").write_text(
        f"[paths]\nmei_root = '{root}'\nmemory_root = '{memory}'\nobsidian_vault = '{vault}'\nobsidian_memory_dir = 'Mei Memory'\n",
        encoding="utf-8",
    )
    return memory, vault


class ProposalTests(unittest.TestCase):
    def test_preview_requires_explicit_apply_and_applies_idempotently(self):
        root = fixture_root()
        memory, vault = setup(root)
        proposal = draft_proposal(
            "personal_markdown", "memory/markdown/project/proposal-note.md", note("proposal-note", "Drafted."),
            scope="synthetic", source={"kind": "user_decision", "locator": "synthetic"}, mei_root=root, vault_root=vault,
        )
        self.assertEqual(proposal["status"], "drafted")
        refreshed = draft_proposal(
            "personal_markdown", "memory/markdown/project/proposal-note.md", note("proposal-note", "Drafted again."),
            scope="synthetic", source={"kind": "current_file", "locator": "new-evidence"}, mei_root=root, vault_root=vault,
        )
        self.assertEqual(refreshed["proposal_id"], proposal["proposal_id"])
        self.assertEqual(status_proposals(mei_root=root)["proposals"][0]["status"], "draft")
        preview = preview_proposal(refreshed["proposal_id"], mei_root=root, vault_root=vault)
        self.assertEqual(preview["status"], "previewed")
        self.assertEqual(apply_proposal(proposal["proposal_id"], mei_root=root, vault_root=vault)["status"], "authorization_required")
        applied = apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root, vault_root=vault)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(read_note(memory, "project/proposal-note.md")["body"], "Drafted again.\n")
        self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root, vault_root=vault)["status"], "already_applied")

    def test_card_edit_invalidates_preview_without_touching_target(self):
        root = fixture_root()
        memory, vault = setup(root)
        first = write_note(memory, "project/proposal-note.md", note("proposal-note", "Existing."))
        proposal = draft_proposal(
            "personal_markdown", "memory/markdown/project/proposal-note.md", note("proposal-note", "Changed."),
            scope="synthetic", source="synthetic", mei_root=root, vault_root=vault,
        )
        preview_proposal(proposal["proposal_id"], mei_root=root, vault_root=vault)
        card = vault / "Mei Memory" / "review" / f"{proposal['proposal_id']}.md"
        card.write_text(card.read_text(encoding="utf-8").replace("Changed.", "Edited."), encoding="utf-8")
        self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root, vault_root=vault)["status"], "repreview_required")
        changed = preview_proposal(proposal["proposal_id"], mei_root=root, vault_root=vault)
        self.assertEqual(changed["status"], "previewed")
        self.assertNotEqual(changed["suggested_content_sha256"], proposal["suggested_content_sha256"])
        self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root, vault_root=vault)["status"], "applied")
        self.assertNotEqual(read_note(memory, "project/proposal-note.md")["sha256"], first["sha256"])

    def test_target_change_is_stale_and_governed_apply_stays_with_curator(self):
        root = fixture_root()
        memory, vault = setup(root)
        proposal = draft_proposal(
            "personal_markdown", "memory/markdown/project/proposal-note.md", note("proposal-note", "Drafted."),
            scope="synthetic", source="synthetic", mei_root=root, vault_root=vault,
        )
        preview_proposal(proposal["proposal_id"], mei_root=root, vault_root=vault)
        write_note(memory, "project/proposal-note.md", note("proposal-note", "Someone else."))
        self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root, vault_root=vault)["status"], "stale")

        governed = draft_proposal(
            "governed_json", "memory/candidates/domain/proposal.json", {"id": "proposal", "claim": "Synthetic"},
            scope="synthetic", source="synthetic", mei_root=root, vault_root=vault,
        )
        preview_proposal(governed["proposal_id"], mei_root=root, vault_root=vault)
        result = apply_proposal(governed["proposal_id"], confirm=True, mei_root=root, vault_root=vault)
        self.assertEqual(result["status"], "requires_curator")
        self.assertFalse((root / "memory" / "candidates" / "domain" / "proposal.json").exists())

    def test_reject_records_reason_and_preserves_card(self):
        root = fixture_root()
        _, vault = setup(root)
        proposal = draft_proposal(
            "personal_markdown", "memory/markdown/project/proposal-note.md", note("proposal-note", "Drafted."),
            scope="synthetic", source="synthetic", mei_root=root, vault_root=vault,
        )
        card = vault / "Mei Memory" / "review" / f"{proposal['proposal_id']}.md"
        before = card.read_bytes()
        result = reject_proposal(proposal["proposal_id"], "Source is not current.", mei_root=root, vault_root=vault)
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(card.is_file())
        self.assertNotEqual(card.read_bytes(), before)

    def test_failed_writer_can_retry_without_duplicate_or_overwrite(self):
        import review_proposals

        root = fixture_root()
        memory, vault = setup(root)
        proposal = draft_proposal(
            "personal_markdown", "memory/markdown/project/proposal-note.md", note("proposal-note", "Retry."),
            scope="synthetic", source="synthetic", mei_root=root, vault_root=vault,
        )
        preview_proposal(proposal["proposal_id"], mei_root=root, vault_root=vault)
        with patch.object(review_proposals, "write_note", side_effect=OSError("synthetic interruption")):
            failed = apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root, vault_root=vault)
        self.assertEqual(failed["status"], "failed")
        retried = apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root, vault_root=vault)
        self.assertEqual(retried["status"], "applied")
        self.assertEqual(read_note(memory, "project/proposal-note.md")["body"], "Retry.\n")


if __name__ == "__main__":
    unittest.main()
