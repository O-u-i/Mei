"""Focused tests for the Markdown personal-memory contract."""

from __future__ import annotations

import hashlib
import sys
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_markdown import MemoryError, doctor, initialize_memory, read_memory, write_note  # noqa: E402


def fixture_root() -> Path:
    root = ROOT / ".tmp" / "pytest" / f"markdown-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    return root


def note(memory_id: str, category: str, body: str, *, relationships: list[str] | None = None) -> str:
    relation_block = ""
    if relationships:
        relation_block = "relationships:\n" + "".join(f"  - {item}\n" for item in relationships)
    return (
        "---\n"
        "schema_version: v0.2.0\n"
        f"memory_id: {memory_id}\n"
        f"category: {category}\n"
        "status: active\n"
        "scope: test scope\n"
        "source: user_decision\n"
        "source_format: markdown\n"
        "provenance:\n  - source: user_decision\n    locator: test\n    evidence_type: user_decision\n    verified_at: null\n"
        "review_due_at: null\nreview_triggers: []\nreferences: []\nconflicts: []\nrelated: []\nrelations: []\nupdated_at: '2026-10-15T00:00:00Z'\nmigrated_from: null\nmigrated_at: null\n"
        f"{relation_block}"
        "---\n"
        f"{body}"
    )


class MarkdownMemoryTests(unittest.TestCase):
    def test_write_is_discoverable_and_invalid_graph_preserves_both_files(self):
        root = fixture_root()
        initialize_memory(root)
        result = write_note(root, "project/small-note.md", note("small-note", "project", "Safe."))
        self.assertEqual(result["doctor"]["status"], "ok")
        self.assertEqual(read_memory(root)["index"]["ids"], ["small-note"])
        before = (root / "MEMORY.md").read_bytes()
        with self.assertRaisesRegex(MemoryError, "link target"):
            write_note(root, "project/broken-note.md", note("broken-note", "project", "[[missing-note]]"))
        self.assertFalse((root / "project/broken-note.md").exists())
        self.assertEqual((root / "MEMORY.md").read_bytes(), before)

    def test_over_cap_and_duplicate_ids_do_not_write_a_note(self):
        root = fixture_root()
        initialize_memory(root)
        write_note(root, "project/sample-note.md", note("sample-note", "project", "Safe."))
        with self.assertRaisesRegex(MemoryError, "duplicate"):
            write_note(root, "domain/sample-note.md", note("sample-note", "domain", "Duplicate."))
        original = (root / "project/sample-note.md").read_bytes()
        with patch("memory_markdown.MAX_INDEX_BYTES", 10):
            with self.assertRaisesRegex(MemoryError, "hard cap"):
                write_note(root, "project/other-note.md", note("other-note", "project", "Safe."))
        self.assertFalse((root / "project/other-note.md").exists())
        self.assertEqual((root / "project/sample-note.md").read_bytes(), original)

    def test_archive_leaves_evidence_but_removes_active_recall(self):
        root = fixture_root()
        initialize_memory(root)
        first = write_note(root, "project/sample-note.md", note("sample-note", "project", "Safe."))
        retired = note("sample-note", "project", "Retired.").replace("status: active", "status: archived")
        write_note(root, "project/sample-note.md", retired, expected_sha256=first["sha256"])
        self.assertEqual(read_memory(root)["notes"], [])
        self.assertEqual(read_memory(root)["index"]["ids"], [])
        self.assertTrue((root / "project/sample-note.md").is_file())

    def test_index_failure_restores_the_selected_note(self):
        import memory_markdown

        root = fixture_root()
        initialize_memory(root)
        original = memory_markdown._atomic_write
        def fail_index(path, raw):
            if path.name == "MEMORY.md":
                raise OSError("synthetic index failure")
            original(path, raw)
        with patch("memory_markdown._atomic_write", side_effect=fail_index):
            with self.assertRaises(OSError):
                write_note(root, "project/sample-note.md", note("sample-note", "project", "Safe."))
        self.assertFalse((root / "project/sample-note.md").exists())
        self.assertEqual(doctor(root)["status"], "ok")

    def test_configured_root_is_shared_and_governed_review_is_not_a_personal_write(self):
        from memory_markdown import resolve_memory_root

        mei = fixture_root()
        memory = mei / "memory/markdown/personal"
        (mei / "local").mkdir()
        (mei / "local/paths.toml").write_text(f"[paths]\nmemory_root = '{memory}'\n", encoding="utf-8")
        self.assertEqual(resolve_memory_root(mei), memory.resolve())
        initialize_memory(memory)
        content = note("review-note", "project", "Needs a Curator.").replace("status: active", "status: active\nreview_required: true")
        with self.assertRaisesRegex(MemoryError, "governed review"):
            write_note(memory, "project/review-note.md", content)

    def test_initialize_read_and_doctor(self):
        root = fixture_root()
        initialize_memory(root)
        write_note(root, "domain/architecture-note.md", note("architecture-note", "domain", "Architecture details."))
        write_note(root, "preferences/editor-choice.md", note("editor-choice", "preferences", "Prefer small diffs.", relationships=["architecture-note"]))
        (root / "MEMORY.md").write_text(
            "# Mei Memory\n\n- [[architecture-note]]\n- [[editor-choice]]\n",
            encoding="utf-8",
        )

        result = doctor(root)

        self.assertEqual(result["status"], "ok")
        memory = read_memory(root)
        self.assertEqual([item["memory_id"] for item in memory["notes"]], ["architecture-note", "editor-choice"])

    def test_secret_and_path_mismatch_are_rejected(self):
        root = fixture_root()
        initialize_memory(root)
        with self.assertRaisesRegex(MemoryError, "secret"):
            write_note(root, "profile/private-note.md", note("private-note", "profile", "password: leaked-value"))
        with self.assertRaisesRegex(MemoryError, "does not match"):
            write_note(root, "profile/wrong-name.md", note("profile-note", "profile", "safe"))

    def test_writer_rejects_stale_hash_without_touching_note(self):
        root = fixture_root()
        initialize_memory(root)
        path = root / "project" / "sample-project.md"
        content = note("sample-project", "project", "Initial.")
        write_note(root, "project/sample-project.md", content)
        original = path.read_bytes()
        with self.assertRaisesRegex(MemoryError, "stale note hash"):
            write_note(root, "project/sample-project.md", note("sample-project", "project", "Changed."), expected_sha256="0" * 64)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(hashlib.sha256(original).hexdigest(), read_memory(root)["notes"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
