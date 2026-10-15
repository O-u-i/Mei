"""Synthetic acceptance tests for the Obsidian projection boundary."""

from __future__ import annotations

import sys
import unittest
import uuid
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
        "schema_version: v0.1.0\n"
        f"memory_id: {memory_id}\n"
        "category: project\n"
        "status: active\n"
        "scope: test project\n"
        "source: test\n"
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


if __name__ == "__main__":
    unittest.main()
