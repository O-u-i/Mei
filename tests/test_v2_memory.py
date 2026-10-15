"""Focused v2 Markdown, migration, and relation contract checks."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_markdown import MemoryError, initialize_memory, parse_note, write_note
from migrate_memory import migrate, rollback, verify
from relations import add, check


NOTE = """---
schema_version: v0.2.0
memory_id: {id}
category: project
status: active
scope: test
source: test
source_format: markdown
provenance:
  - source: test
    locator: test
    evidence_type: test_result
    verified_at: null
review_due_at: null
review_triggers: []
references: []
conflicts: []
related: []
relations: []
updated_at: '2026-10-15T00:00:00Z'
migrated_from: null
migrated_at: null
---
## For future agent

Test.
"""


class V2MemoryTests(unittest.TestCase):
    def test_duplicate_frontmatter_keys_are_rejected(self):
        with self.assertRaises(MemoryError):
            parse_note(NOTE.format(id="abc") + "\n", Path("note.md"))
            parse_note(NOTE.format(id="abc").replace("scope: test", "scope: test\nscope: duplicate"), Path("note.md"))

    def test_reciprocal_relation_and_json_migration_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory" / "markdown"
            initialize_memory(memory)
            write_note(memory, "project/one.md", NOTE.format(id="one"))
            write_note(memory, "project/two.md", NOTE.format(id="two"))
            self.assertEqual(add(memory, "one", "two")["status"], "added")
            self.assertEqual(check(memory)["status"], "ok")
            source = root / "memory" / "canonical" / "domain"
            source.mkdir(parents=True)
            (source / "legacy.md").with_suffix(".json").write_text(json.dumps({"id": "legacy", "category": "domain", "claim": "old", "scope": "test", "status": "canonical", "verified_at": "2026-10-15T00:00:00Z"}), encoding="utf-8")
            result = migrate(root)
            manifest = root / "memory" / "migration" / "manifest.yaml"
            self.assertEqual(verify(manifest, root)["status"], "ok")
            self.assertTrue((root / "memory" / "legacy-json" / "canonical" / "domain" / "legacy.json").is_file())
            self.assertEqual(rollback(manifest, root)["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
