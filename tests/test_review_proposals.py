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
    ProposalError,
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
    for name in ("synthetic", "new-evidence"):
        (root / name).write_text("Synthetic current evidence for proposal tests.\n", encoding="utf-8")
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
    def test_status_missing_store_is_empty_without_creating_paths(self):
        for memory_exists in (False, True):
            with self.subTest(memory_exists=memory_exists):
                root = fixture_root()
                if memory_exists:
                    (root / "memory").mkdir()
                before = sorted(root.rglob("*"))
                self.assertEqual(status_proposals(mei_root=root), {"status": "ok", "proposals": []})
                self.assertFalse((root / "memory" / "proposals").exists())
                self.assertEqual(sorted(root.rglob("*")), before)
                with self.assertRaises(ProposalError):
                    status_proposals("missing-proposal", mei_root=root)
                self.assertEqual(sorted(root.rglob("*")), before)

    def test_status_reads_existing_proposals_without_writes(self):
        root = fixture_root()
        _, vault = setup(root)
        proposal = draft_proposal(
            "personal_markdown", "memory/markdown/project/status-note.md", note("status-note", "Pending."),
            scope="synthetic", source="synthetic", mei_root=root, vault_root=vault,
        )
        paths = sorted(root.rglob("*"))
        before = {path: path.read_bytes() for path in paths if path.is_file()}
        result = status_proposals(mei_root=root)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual(result["proposals"][0]["proposal_id"], proposal["proposal_id"])
        self.assertEqual(result["proposals"][0]["status"], "draft")
        self.assertEqual(status_proposals(proposal["proposal_id"], mei_root=root), result)
        self.assertEqual(sorted(root.rglob("*")), paths)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_draft_creates_and_uses_store_after_empty_status(self):
        root = fixture_root()
        memory, vault = setup(root)
        self.assertEqual(status_proposals(mei_root=root), {"status": "ok", "proposals": []})
        directory = root / "memory" / "proposals"
        self.assertFalse(directory.exists())
        proposal = draft_proposal(
            "personal_markdown", "memory/markdown/project/status-note.md", note("status-note", "Approved."),
            scope="synthetic", source="synthetic", mei_root=root, vault_root=vault,
        )
        self.assertTrue((directory / f"{proposal['proposal_id']}.md").is_file())
        self.assertEqual(preview_proposal(proposal["proposal_id"], mei_root=root, vault_root=vault)["status"], "previewed")
        self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root, vault_root=vault)["status"], "applied")
        self.assertEqual(read_note(memory, "project/status-note.md")["body"], "Approved.\n")

    def test_status_does_not_hide_directory_permission_errors(self):
        root = fixture_root()
        (root / "memory" / "proposals").mkdir(parents=True)
        with patch.object(Path, "iterdir", side_effect=PermissionError("synthetic directory denial")):
            with self.assertRaises(PermissionError):
                status_proposals(mei_root=root)

    def test_status_does_not_hide_corrupt_records_or_invalid_store(self):
        root = fixture_root()
        directory = root / "memory" / "proposals"
        directory.mkdir(parents=True)
        (directory / "broken-proposal.md").write_text("Not proposal frontmatter.\n", encoding="utf-8")
        with self.assertRaises(ProposalError):
            status_proposals(mei_root=root)
        other = fixture_root()
        (other / "memory").mkdir()
        (other / "memory" / "proposals").write_text("Not a directory.\n", encoding="utf-8")
        with self.assertRaises((ProposalError, OSError)):
            status_proposals(mei_root=other)

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
