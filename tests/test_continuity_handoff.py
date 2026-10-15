"""Synthetic checks for the handoff orchestration seam."""

from __future__ import annotations

import sys
import json
import subprocess
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
    def test_save_without_memory_draft_succeeds_at_cli_boundary(self):
        root = ROOT / ".tmp" / "handoff-tests" / uuid.uuid4().hex
        root.mkdir(parents=True)
        fixture = ContinuityFixture(root)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        snapshot_path, project_path = root / "snapshot.json", root / "payload.json"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        project = dict(snapshot["project"], next_step="Resume the explicitly saved task.")
        project_path.write_text(json.dumps(project), encoding="utf-8")
        command = [sys.executable, "-B", str(ROOT / "scripts" / "continuity_handoff.py"),
                   "--repo-root", str(fixture.repo), "--mei-root", str(fixture.mei),
                   "--context-id", fixture.context_id, "--snapshot", str(snapshot_path),
                   "--project", str(project_path), "--writer-session-id", "handoff-cli-test"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "handoff_saved")
        saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        self.assertEqual(saved["project"]["status"], "active")
        self.assertEqual(saved["project"]["next_step"], project["next_step"])
        snapshot_path.write_text(json.dumps(saved), encoding="utf-8")
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "handoff_noop")
        self.assertFalse((fixture.mei / "memory" / "proposals").exists())

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
