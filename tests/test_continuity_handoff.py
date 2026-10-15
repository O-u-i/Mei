"""Synthetic checks for the handoff orchestration seam."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_continuity import load_context  # noqa: E402
from continuity_handoff import save_handoff  # noqa: E402
from memory_markdown import initialize_memory  # noqa: E402
from tests.test_continuity import ContinuityFixture  # noqa: E402


class HandoffTests(unittest.TestCase):
    def test_sync_then_draft_stays_out_of_memory_reader(self):
        root = ROOT / ".tmp" / "handoff-tests" / uuid.uuid4().hex
        root.mkdir(parents=True)
        fixture = ContinuityFixture(root)
        memory = fixture.mei / "memory" / "markdown"
        vault = root / "vault"
        vault.mkdir()
        (vault / ".obsidian").mkdir()
        initialize_memory(memory)
        (fixture.mei / "local").mkdir()
        (fixture.mei / "local" / "paths.toml").write_text(
            f"[paths]\nmei_root = '{fixture.mei}'\nmemory_root = '{memory}'\nobsidian_vault = '{vault}'\nobsidian_memory_dir = 'Mei Memory'\n",
            encoding="utf-8",
        )
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        project = dict(snapshot["project"], next_step="Resume after the saved handoff.")
        result = save_handoff(
            fixture.repo, fixture.context_id, snapshot, project,
            writer_session_id="handoff-test", mei_root=fixture.mei,
            memory_draft={
                "target_type": "personal_markdown",
                "target_locator": "memory/markdown/project/handoff-note.md",
                "content": "---\nschema_version: v0.1.0\nmemory_id: handoff-note\ncategory: project\nstatus: active\nscope: test\nsource: user_decision\n---\nSaved handoff.\n",
                "scope": "test",
                "source": "synthetic",
            },
        )
        self.assertEqual(result["status"], "handoff_saved")
        self.assertEqual(load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)["project"]["next_step"], "Resume after the saved handoff.")
        self.assertFalse((memory / "project" / "handoff-note.md").exists())
        self.assertTrue((fixture.mei / "memory" / "proposals").is_dir())


if __name__ == "__main__":
    unittest.main()
