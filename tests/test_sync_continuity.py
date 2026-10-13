"""Behavior tests for explicit continuity sync and compact bookkeeping."""

from __future__ import annotations

import sys
import unittest
import uuid
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_continuity import load_context  # noqa: E402
from tests.test_continuity import ContinuityFixture  # noqa: E402


@contextmanager
def sync_fixture_root():
    root = ROOT / ".tmp" / "sync-tests" / uuid.uuid4().hex
    root.mkdir(parents=True)
    yield root


class SyncTests(unittest.TestCase):
    def test_reviewed_payload_is_saved_clean_with_a_change_report(self):
        from sync_continuity import sync_context

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            reviewed = dict(snapshot["project"], next_step="Resume from the verified sync.")

            result = sync_context(
                fixture.repo,
                fixture.context_id,
                snapshot,
                reviewed,
                writer_session_id="sync-one",
                mei_root=fixture.mei,
            )
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result["status"], "synced")
            self.assertEqual(saved["status"], "clean")
            self.assertEqual(saved["project"]["next_step"], "Resume from the verified sync.")
            self.assertEqual(saved["checkpointer"]["generation"], 1)
            self.assertEqual(saved["checkpointer"]["synced_generation"], 1)
            self.assertFalse(saved["checkpointer"]["dirty"])
            self.assertEqual(result["changes"]["updated"], ["next_step"])
            self.assertEqual(result["changes"]["added"], [])
            self.assertEqual(result["changes"]["archived"], [])
            self.assertIn("goal", result["changes"]["skipped"])
            self.assertTrue(result["validation"]["ok"])
            self.assertEqual(result["sizes"]["project_bytes"], len((fixture.context_dir / "project.json").read_bytes()))
            self.assertEqual(result["sizes"]["index_bytes"], len((fixture.mei / "registry" / "continuity.yaml").read_bytes()))

    def test_identical_reviewed_payload_is_a_noop(self):
        from sync_continuity import sync_context

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            before_project = (fixture.context_dir / "project.json").read_bytes()
            before_checkpointer = (fixture.context_dir / "checkpointer.json").read_bytes()

            result = sync_context(
                fixture.repo,
                fixture.context_id,
                snapshot,
                dict(snapshot["project"]),
                writer_session_id="sync-one",
                mei_root=fixture.mei,
            )
            after = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result["status"], "noop")
            self.assertEqual(result["generation"], snapshot["checkpointer"]["generation"])
            self.assertEqual(after["status"], snapshot["status"])
            self.assertEqual((fixture.context_dir / "project.json").read_bytes(), before_project)
            self.assertEqual((fixture.context_dir / "checkpointer.json").read_bytes(), before_checkpointer)

    def test_auto_compact_allows_and_records_reconcile_debt(self):
        from sync_continuity import handle_lifecycle_event

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            before_project = (fixture.context_dir / "project.json").read_bytes()
            before_index = (fixture.mei / "registry" / "continuity.yaml").read_bytes()

            result = handle_lifecycle_event(
                fixture.repo,
                fixture.context_id,
                "precompact_auto",
                mei_root=fixture.mei,
                writer_session_id="hook-one",
            )
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertTrue(result["allow"])
            self.assertEqual(result["event"], "precompact_auto")
            self.assertTrue(saved["needs_reconcile"])
            self.assertEqual(saved["status"], "needs_reconcile")
            self.assertEqual((fixture.context_dir / "project.json").read_bytes(), before_project)
            self.assertEqual((fixture.mei / "registry" / "continuity.yaml").read_bytes(), before_index)

    def test_manual_compact_blocks_dirty_context(self):
        from checkpoint_continuity import checkpoint_context
        from sync_continuity import handle_lifecycle_event

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            checkpoint_context(
                fixture.repo,
                fixture.context_id,
                dict(snapshot["project"], next_step="Dirty before compact."),
                mei_root=fixture.mei,
                base_generation=0,
                base_project_sha256=snapshot["project_sha256"],
                base_index_sha256=snapshot["index_sha256"],
                writer_session_id="writer-one",
            )

            result = handle_lifecycle_event(
                fixture.repo,
                fixture.context_id,
                "precompact_manual",
                mei_root=fixture.mei,
                writer_session_id="hook-one",
            )

            self.assertFalse(result["allow"])
            self.assertEqual(result["reason"], "sync_required")
            self.assertNotIn("prompt", result)

    def test_manual_compact_allows_clean_context_and_unknown_is_fail_open(self):
        from sync_continuity import handle_lifecycle_event

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            manual = handle_lifecycle_event(
                fixture.repo, fixture.context_id, "precompact_manual",
                mei_root=fixture.mei, writer_session_id="hook-one",
            )
            unknown = handle_lifecycle_event(
                fixture.repo, fixture.context_id, "precompact_unknown",
                mei_root=fixture.mei, writer_session_id="hook-one",
            )

            self.assertTrue(manual["allow"])
            self.assertEqual(manual["reason"], "clean")
            self.assertTrue(unknown["allow"])
            self.assertTrue(load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)["needs_reconcile"])

    def test_prompt_submit_invalidates_a_fresh_sync_snapshot(self):
        from sync_continuity import handle_lifecycle_event, sync_context

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            handle_lifecycle_event(
                fixture.repo, fixture.context_id, "user_prompt_submit",
                mei_root=fixture.mei, writer_session_id="hook-one",
            )

            rejected = sync_context(
                fixture.repo, fixture.context_id, snapshot,
                dict(snapshot["project"], next_step="Stale sync must fail."),
                writer_session_id="sync-one", mei_root=fixture.mei,
            )
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(rejected["status"], "conflict")
            self.assertTrue(saved["checkpointer"]["dirty"])
            self.assertEqual(saved["project"]["next_step"], snapshot["project"]["next_step"])

    def test_cli_sync_can_be_reloaded_in_a_separate_process(self):
        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            snapshot_path = root / "snapshot.json"
            project_path = root / "reviewed.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            project_path.write_text(
                json.dumps(dict(snapshot["project"], next_step="Cold start after sync.")),
                encoding="utf-8",
            )
            sync = subprocess.run(
                [
                    sys.executable, "-B", str(ROOT / "scripts" / "sync_continuity.py"),
                    "--repo-root", str(fixture.repo), "--context-id", fixture.context_id,
                    "--mei-root", str(fixture.mei), "--snapshot", str(snapshot_path),
                    "--project", str(project_path), "--writer-session-id", "cli-sync",
                ],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            check = subprocess.run(
                [
                    sys.executable, "-B", str(ROOT / "scripts" / "check_continuity.py"),
                    "--repo-root", str(fixture.repo), "--context-id", fixture.context_id,
                    "--mei-root", str(fixture.mei),
                ],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )

            self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertEqual(json.loads(check.stdout)["status"], "clean")

    def test_stale_project_hash_is_rejected_without_mutation(self):
        from sync_continuity import sync_context

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            before_project = (fixture.context_dir / "project.json").read_bytes()
            stale = dict(snapshot)
            stale["project_sha256"] = "f" * 64

            result = sync_context(
                fixture.repo, fixture.context_id, stale,
                dict(snapshot["project"], next_step="Must not save."),
                writer_session_id="sync-one", mei_root=fixture.mei,
            )

            self.assertEqual(result["status"], "conflict")
            self.assertEqual((fixture.context_dir / "project.json").read_bytes(), before_project)

    def test_stale_generation_and_index_hash_are_rejected_without_mutation(self):
        from sync_continuity import sync_context

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            before = {
                "project": (fixture.context_dir / "project.json").read_bytes(),
                "checkpointer": (fixture.context_dir / "checkpointer.json").read_bytes(),
            }
            for field, value in (("checkpointer", {**snapshot["checkpointer"], "generation": 9}), ("index_sha256", "e" * 64)):
                stale = dict(snapshot)
                if field == "checkpointer":
                    stale[field] = value
                else:
                    stale[field] = value
                result = sync_context(
                    fixture.repo, fixture.context_id, stale,
                    dict(snapshot["project"], next_step="Must not save."),
                    writer_session_id="sync-one", mei_root=fixture.mei,
                )
                self.assertEqual(result["status"], "conflict")
                self.assertEqual((fixture.context_dir / "project.json").read_bytes(), before["project"])
                self.assertEqual((fixture.context_dir / "checkpointer.json").read_bytes(), before["checkpointer"])

    def test_sync_marks_an_existing_dirty_checkpoint_clean_without_advancing_generation(self):
        from checkpoint_continuity import checkpoint_context
        from sync_continuity import sync_context

        with sync_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            checkpoint_context(
                fixture.repo, fixture.context_id,
                dict(initial["project"], next_step="Already reviewed."),
                mei_root=fixture.mei,
                base_generation=initial["checkpointer"]["generation"],
                base_project_sha256=initial["project_sha256"],
                base_index_sha256=initial["index_sha256"],
                writer_session_id="writer-one",
            )
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            result = sync_context(
                fixture.repo, fixture.context_id, snapshot, dict(snapshot["project"]),
                writer_session_id="sync-one", mei_root=fixture.mei,
            )
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result["status"], "synced")
            self.assertEqual(saved["checkpointer"]["generation"], snapshot["checkpointer"]["generation"])
            self.assertEqual(saved["checkpointer"]["synced_generation"], saved["checkpointer"]["generation"])
            self.assertEqual(saved["status"], "clean")

    def test_lifecycle_event_has_no_prompt_parameter_or_persisted_prompt(self):
        import inspect
        from sync_continuity import handle_lifecycle_event

        self.assertNotIn("prompt", inspect.signature(handle_lifecycle_event).parameters)


if __name__ == "__main__":
    unittest.main()
