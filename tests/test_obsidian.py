"""Synthetic acceptance tests for the Obsidian projection boundary."""

from __future__ import annotations

import sys
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_markdown import initialize_memory, write_note  # noqa: E402
from sync_obsidian import MemoryError, check_vault, resolve_vault, sync_obsidian  # noqa: E402


def fixture_root() -> Path:
    root = ROOT / ".tmp" / "pytest" / f"obsidian-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    return root


def note(memory_id: str, body: str) -> str:
    return (
        "---\n"
        "schema_version: v0.2.0\n"
        f"memory_id: {memory_id}\n"
        "category: project\n"
        "status: active\n"
        "scope: test project\n"
        "source: test\nsource_format: markdown\nprovenance:\n  - source: test\n    locator: test\n    evidence_type: test_result\n    verified_at: null\nreview_due_at: null\nreview_triggers: []\nreferences: []\nconflicts: []\nrelated: []\nrelations: []\nupdated_at: '2026-10-15T00:00:00Z'\nmigrated_from: null\nmigrated_at: null\n"
        "---\n"
        f"{body}"
    )


class ObsidianTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.memory = self.root / "memory"
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / ".obsidian").mkdir()
        initialize_memory(self.memory)
        write_note(self.memory, "project/mei-project.md", note("mei-project", "A project memory."))
        (self.memory / "MEMORY.md").write_text("# Mei Memory\n\n- [[mei-project]]\n", encoding="utf-8")

    def test_sync_is_idempotent_and_preserves_obsidian_settings(self):
        first = sync_obsidian(self.vault, 1, memory_root=self.memory)
        second = sync_obsidian(self.vault, 1, memory_root=self.memory)

        self.assertEqual(first["status"], "synced")
        self.assertEqual(first["added"], ["mei-project", "index.md"])
        self.assertEqual(second["status"], "synced")
        self.assertIn("mei-project", second["unchanged"])
        self.assertIn("index.md", second["unchanged"])
        self.assertTrue((self.vault / ".obsidian").is_dir())
        self.assertEqual(check_vault(self.vault, memory_root=self.memory)["status"], "ok")

    def test_manual_projection_edit_is_reported_as_drift_and_preserved(self):
        sync_obsidian(self.vault, 1, memory_root=self.memory)
        page = self.vault / "Mei Memory" / "views" / "mei-project.md"
        page.write_text(page.read_text(encoding="utf-8") + "\nManual review.\n", encoding="utf-8")
        before = page.read_bytes()

        result = sync_obsidian(self.vault, 2, memory_root=self.memory)

        self.assertEqual(result["status"], "drifted")
        self.assertIn("mei-project", result["drifted"])
        self.assertEqual(page.read_bytes(), before)

    def test_namespace_cannot_escape_or_use_obsidian_settings(self):
        with self.assertRaisesRegex(MemoryError, r"outside \.obsidian"):
            resolve_vault(self.vault, ".obsidian")
        with self.assertRaisesRegex(MemoryError, "unsafe"):
            resolve_vault(self.vault, "../outside")

    def test_inbox_secret_is_invalid_without_writing_memory(self):
        sync_obsidian(self.vault, 1, memory_root=self.memory)
        inbox = self.vault / "Mei Memory" / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "capture.md").write_text(
            "---\nschema_version: v0.1.0\nmemory_id: capture-note\ncategory: project\nstatus: active\nscope: capture\nsource: inbox\n---\napi_key: leaked-value\n",
            encoding="utf-8",
        )

        result = check_vault(self.vault, memory_root=self.memory)

        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("secret" in error for error in result["errors"]))
        self.assertFalse((self.memory / "project" / "capture-note.md").exists())

    def test_external_change_between_plan_and_write_is_preserved(self):
        import sync_obsidian as bridge

        sync_obsidian(self.vault, 1, memory_root=self.memory)
        page = self.vault / "Mei Memory/views/mei-project.md"
        original_plan = bridge._plan
        def change_after_plan(*args):
            plan = original_plan(*args)
            page.write_text("External sync edit.", encoding="utf-8")
            return plan
        with patch("sync_obsidian._plan", side_effect=change_after_plan):
            result = sync_obsidian(self.vault, 2, memory_root=self.memory)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(page.read_text(encoding="utf-8"), "External sync edit.")

    def test_source_change_between_plan_and_write_is_rejected(self):
        import sync_obsidian as bridge

        original_plan = bridge._plan
        def change_after_plan(*args):
            plan = original_plan(*args)
            path = self.memory / "project/mei-project.md"
            path.write_text(path.read_text(encoding="utf-8") + "\nChanged externally.\n", encoding="utf-8")
            return plan
        with patch("sync_obsidian._plan", side_effect=change_after_plan):
            result = sync_obsidian(self.vault, 2, memory_root=self.memory)
        self.assertEqual(result["status"], "stale_source")
        self.assertFalse((self.vault / "Mei Memory/views/mei-project.md").exists())

    def test_reviewed_capture_and_encoded_uri_are_not_governed_promotion(self):
        from urllib.parse import parse_qs, urlsplit
        from sync_obsidian import import_inbox, note_uri

        sync_obsidian(self.vault, 1, memory_root=self.memory)
        inbox = self.vault / "Mei Memory/inbox/capture.md"
        inbox.write_text(note("capture-note", "Personal capture with [[mei-project]]."), encoding="utf-8")
        original = inbox.read_bytes()
        with self.assertRaisesRegex(MemoryError, "review"):
            import_inbox(self.vault, "capture.md", memory_root=self.memory)
        result = import_inbox(self.vault, "capture.md", memory_root=self.memory, reviewed_personal=True)
        self.assertEqual(result["doctor"]["status"], "ok")
        self.assertEqual(inbox.read_bytes(), original)
        sync_obsidian(self.vault, 2, memory_root=self.memory)
        uri = note_uri(self.vault, "views/capture-note.md")
        self.assertEqual(parse_qs(urlsplit(uri).query)["file"], ["Mei Memory/views/capture-note.md"])
        with self.assertRaises(MemoryError):
            note_uri(self.vault, "../outside.md")
        inbox.write_text(note("governed-note", "Review.").replace("status: active", "status: active\nreview_required: true"), encoding="utf-8")
        report = import_inbox(self.vault, "capture.md", memory_root=self.memory, reviewed_personal=True)
        self.assertEqual(report["status"], "review_required")
        self.assertFalse((self.memory / "project/governed-note.md").exists())


if __name__ == "__main__":
    unittest.main()
