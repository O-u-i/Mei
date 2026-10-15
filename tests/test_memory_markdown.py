"""Focused tests for the Markdown personal-memory contract."""

from __future__ import annotations

import hashlib
import sys
import unittest
import uuid
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
        "schema_version: v0.1.0\n"
        f"memory_id: {memory_id}\n"
        f"category: {category}\n"
        "status: active\n"
        "scope: test scope\n"
        "source: user_decision\n"
        f"{relation_block}"
        "---\n"
        f"{body}"
    )


class MarkdownMemoryTests(unittest.TestCase):
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
