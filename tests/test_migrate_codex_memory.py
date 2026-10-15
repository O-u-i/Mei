"""Synthetic tests for the Codex host-memory migration seam."""

from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_codex_memory import migrate, verify  # noqa: E402


def fixture():
    root = ROOT / ".tmp" / "pytest" / f"migrate-{uuid.uuid4().hex}"
    source = root / "codex" / "memories"
    (source / "rollout_summaries").mkdir(parents=True)
    (source / "skills" / "demo").mkdir(parents=True)
    (source / ".git").mkdir()
    (source / "MEMORY.md").write_text("# Memory\n\nDurable note.\n", encoding="utf-8")
    (source / "memory_summary.md").write_text("Summary.\n", encoding="utf-8")
    (source / "rollout_summaries" / "one.md").write_text("Episode.\n", encoding="utf-8")
    (source / "rollout_summaries" / "leak.md").write_text("api_key: leaked-value\n", encoding="utf-8")
    (source / "skills" / "demo" / "SKILL.md").write_text("name: demo\n", encoding="utf-8")
    (source / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    (source / "auth.json").write_text("{}\n", encoding="utf-8")
    mei = root / "mei"
    (mei / "local").mkdir(parents=True)
    return root, source, mei


def snapshot(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


class MigrationTests(unittest.TestCase):
    def test_new_snapshot_preserves_prior_bytes_and_reports_privacy_blocks(self):
        root, source, mei = fixture()
        first = mei / "local/first.json"
        second = mei / "local/second.json"
        migrate({"gui": source}, mei, first, snapshot_id="first")
        old = (mei / "memory/markdown/archive/codex-gui/first/MEMORY.md").read_bytes()
        (source / "MEMORY.md").write_text("New source.\n", encoding="utf-8")
        migrate({"gui": source}, mei, second, snapshot_id="second")
        self.assertEqual((mei / "memory/markdown/archive/codex-gui/first/MEMORY.md").read_bytes(), old)
        report = verify(second, mei)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["unresolved"][0]["path"], "rollout_summaries/leak.md")
        with self.assertRaises(FileExistsError):
            migrate({"gui": source}, mei, second, snapshot_id="second")

    def test_manifest_path_escape_is_rejected_before_reading_target(self):
        from memory_markdown import MemoryError

        root, source, mei = fixture()
        path = mei / "local/manifest.json"
        migrate({"gui": source}, mei, path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        copied = next(entry for entry in manifest["hosts"]["gui"]["entries"] if entry["action"] == "copied")
        copied["target"] = "../outside.md"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(MemoryError, "unsafe"):
            verify(path, mei)

    def test_copies_eligible_files_only_and_leaves_source_untouched(self):
        root, source, mei = fixture()
        before = snapshot(source)
        result = migrate({"tui": source}, mei, mei / "local" / "manifest.json")
        self.assertEqual(result["totals"]["copied"], 3)
        self.assertEqual(result["totals"]["blocked"], 1)
        target = mei / "memory" / "markdown" / "archive" / "codex-tui"
        self.assertEqual(sorted(snapshot(target)), ["MEMORY.md", "memory_summary.md", "rollout_summaries/one.md"])
        self.assertEqual(snapshot(source), before)
        self.assertEqual(verify(mei / "local" / "manifest.json", mei)["status"], "ok")

    def test_rerun_is_idempotent_and_differing_target_is_a_conflict(self):
        root, source, mei = fixture()
        manifest = mei / "local" / "manifest.json"
        migrate({"tui": source}, mei, manifest)
        self.assertEqual(migrate({"tui": source}, mei, manifest)["totals"]["unchanged"], 3)
        target = mei / "memory" / "markdown" / "archive" / "codex-tui" / "MEMORY.md"
        target.write_text("edited\n", encoding="utf-8")
        result = migrate({"tui": source}, mei, manifest)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(target.read_text(encoding="utf-8"), "edited\n")

    def test_verify_detects_target_tampering(self):
        root, source, mei = fixture()
        manifest = mei / "local" / "manifest.json"
        migrate({"gui": source}, mei, manifest)
        (mei / "memory" / "markdown" / "archive" / "codex-gui" / "memory_summary.md").write_text("x\n", encoding="utf-8")
        result = verify(manifest, mei)
        self.assertEqual(result["status"], "invalid")
        entries = json.loads(manifest.read_text(encoding="utf-8"))["hosts"]["gui"]["entries"]
        classes = {entry["path"]: entry["class"] for entry in entries}
        self.assertEqual(classes["auth.json"], "runtime_excluded")
        self.assertEqual(classes["skills/demo/SKILL.md"], "executable_skill")


if __name__ == "__main__":
    unittest.main()
