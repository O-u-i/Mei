"""Phase 5 behavior tests at the four completion/Curator seams."""

from __future__ import annotations

import json
import sys
import subprocess
import unittest
import uuid
import copy
import os
import hashlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_continuity import ContinuityError, load_context  # noqa: E402
from tests.test_continuity import (  # noqa: E402
    ContinuityFixture, fixture_project, process_diagnostic, run_mklink_junction, write_json,
)


def completion_fixture():
    # Retain synthetic artifacts under the ignored test root; never batch-delete.
    root = ROOT / ".tmp" / "completion-tests" / uuid.uuid4().hex
    root.mkdir(parents=True)
    return ContinuityFixture(root)


def reviewed_trajectory():
    return json.loads((ROOT / "evals/cases/synthetic-task.json").read_text(encoding="utf-8"))


class CompletionTests(unittest.TestCase):
    def complete(self, fixture, trajectory=None):
        from complete_continuity import completion_checkpoint

        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        return completion_checkpoint(
            fixture.repo, fixture.context_id, snapshot, trajectory or reviewed_trajectory(),
            privacy_reviewed=True, writer_session_id="completion-one", mei_root=fixture.mei,
        )

    def test_completion_checkpoint_is_visible_to_a_fresh_reader_without_export(self):
        from complete_continuity import completion_checkpoint

        fixture = completion_fixture()
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        result = completion_checkpoint(
            fixture.repo, fixture.context_id, snapshot, reviewed_trajectory(),
            privacy_reviewed=True, writer_session_id="completion-one", mei_root=fixture.mei,
        )
        saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["project"]["status"], "completed")
        self.assertEqual(saved["required_action"], "export_and_retire")
        self.assertEqual(saved["checkpointer"]["completion"]["source_project_sha256"], snapshot["project_sha256"])
        self.assertFalse(saved["checkpointer"]["completion"]["retired"])
        self.assertEqual(saved["index_sha256"], snapshot["index_sha256"])
        self.assertEqual(list((fixture.mei / "trajectories/inbox").glob("*.json")), [])

    def test_export_is_schema_valid_immutable_and_replay_cannot_rotate_its_id(self):
        from complete_continuity import export_completed_trajectory
        from _common import validate_trajectory

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        first = export_completed_trajectory(
            fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei,
        )
        source = fixture.mei / first["source_path"]
        before = source.read_bytes()
        replay = export_completed_trajectory(
            fixture.repo, fixture.context_id, snapshot,
            dict(completed["trajectory"], id="another-task-id"), mei_root=fixture.mei,
        )

        self.assertEqual(first["status"], "exported")
        self.assertEqual(replay["status"], "already_exported")
        self.assertEqual(replay["source_path"], first["source_path"])
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(validate_trajectory(source)["id"], completed["completion"]["trajectory_id"])
        self.assertEqual(len(list((fixture.mei / "trajectories/inbox").glob("*.json"))), 1)
        self.assertTrue(any("source_sha256=" in item for item in validate_trajectory(source)["result"]["evidence"]))

    def test_new_session_can_retire_transient_state_and_preserve_stable_pointers(self):
        from complete_continuity import export_completed_trajectory
        from continuity_adapters import read_gui_context
        from sync_continuity import sync_context

        fixture = completion_fixture()
        (fixture.repo / "result.txt").write_text("Synthetic verified result.", encoding="utf-8")
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        refs = [{"kind": "file", "value": "result.txt"}]
        sync_context(
            fixture.repo, fixture.context_id, snapshot,
            dict(snapshot["project"], stable_refs=refs, blockers=["Transient blocker."], decisions=["Task-local decision."]),
            writer_session_id="sync-one", mei_root=fixture.mei,
        )
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
        code = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);"
            "from continuity_adapters import read_tui_context;"
            "from complete_continuity import retire_completed_context;"
            "s=read_tui_context(sys.argv[2],sys.argv[3],mei_root=sys.argv[4]);"
            "print(json.dumps(retire_completed_context(sys.argv[2],sys.argv[3],s,"
            "writer_session_id='new-session',mei_root=sys.argv[4])))"
        )
        child = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-c", code, str(ROOT / "scripts"), str(fixture.repo), fixture.context_id, str(fixture.mei)],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertEqual(json.loads(child.stdout)["status"], "retired")
        saved = read_gui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        self.assertEqual(saved["status"], "retired")
        self.assertEqual(saved["required_action"], "none")
        self.assertEqual(saved["project"]["stable_refs"], refs)
        self.assertEqual(saved["project"]["decisions"], [])
        self.assertEqual(saved["project"]["blockers"], [])
        self.assertNotIn("Task-local", json.dumps(saved["project"]))
        self.assertTrue(saved["checkpointer"]["completion"]["retired"])

    def test_curator_bridge_preserves_provenance_and_never_writes_review_owned_records(self):
        from complete_continuity import curator_handoff, export_completed_trajectory, retire_completed_context

        fixture = completion_fixture()
        protected = [
            fixture.mei / "memory/canonical/domain/sentinel.json",
            fixture.mei / "memory/candidates/sentinel.json",
            fixture.mei / "registry/memory.yaml",
        ]
        for path in protected:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic protected sentinel.", encoding="utf-8")
        before = {path: path.read_bytes() for path in protected}
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        exported = export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
        retire_completed_context(fixture.repo, fixture.context_id, snapshot, writer_session_id="retire-one", mei_root=fixture.mei)
        state_files = [fixture.context_dir / "project.json", fixture.context_dir / "checkpointer.json", fixture.mei / exported["source_path"]]
        state_before = {path: path.read_bytes() for path in state_files}
        handoff = curator_handoff(fixture.repo, fixture.context_id, mei_root=fixture.mei)

        self.assertEqual(handoff["status"], "curator_handoff")
        self.assertEqual(handoff["source_path"], exported["source_path"])
        self.assertEqual(handoff["source_sha256"], completed["completion"]["trajectory_sha256"])
        self.assertEqual(handoff["review_required"], ["privacy", "provenance", "duplicates", "conflicts", "currentness", "explicit_promotion"])
        self.assertFalse(handoff["promotion_performed"])
        self.assertEqual({path: path.read_bytes() for path in protected}, before)
        self.assertEqual({path: path.read_bytes() for path in state_files}, state_before)
        self.assertEqual(list((fixture.mei / "trajectories/normalized").glob("*.json")), [])

    def test_retired_context_cannot_be_reopened_by_checkpoint_sync_or_lifecycle(self):
        from complete_continuity import export_completed_trajectory, retire_completed_context
        from checkpoint_continuity import checkpoint_context
        from sync_continuity import handle_lifecycle_event, sync_context

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
        retire_completed_context(fixture.repo, fixture.context_id, snapshot, writer_session_id="retire-one", mei_root=fixture.mei)
        saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        with self.assertRaisesRegex(ContinuityError, "completed"):
            checkpoint_context(
                fixture.repo, fixture.context_id, dict(saved["project"], goal="New task in retired context."),
                base_generation=saved["checkpointer"]["generation"], base_project_sha256=saved["project_sha256"],
                base_index_sha256=saved["index_sha256"], writer_session_id="late-writer", mei_root=fixture.mei,
            )
        with self.assertRaisesRegex(ContinuityError, "completed"):
            sync_context(fixture.repo, fixture.context_id, saved, dict(saved["project"]), writer_session_id="late-sync", mei_root=fixture.mei)
        for event in ("user_prompt_submit", "precompact_manual", "precompact_auto", "precompact_unknown"):
            result = handle_lifecycle_event(fixture.repo, fixture.context_id, event, mei_root=fixture.mei)
            self.assertTrue(result["allow"])
            self.assertFalse(result["recorded"])
            self.assertEqual(result["reason"], "completed_context")
        self.assertEqual(load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)["checkpointer"], saved["checkpointer"])

    def test_completion_requires_reviewed_private_schema_valid_completed_evidence(self):
        from complete_continuity import completion_checkpoint

        fixture = completion_fixture()
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        cases = [(reviewed_trajectory(), False), (reviewed_trajectory(), "true")]
        secret = dict(reviewed_trajectory(), goal="api_key=synthetic-private-value")
        oversized = dict(reviewed_trajectory(), goal="x" * 2001)
        raw = dict(reviewed_trajectory(), transcript="Raw content is forbidden.")
        partial = copy.deepcopy(reviewed_trajectory())
        partial["result"]["status"] = "partial"
        bad_date = dict(reviewed_trajectory(), completed_at="not-a-date")
        cases.extend((value, True) for value in (secret, oversized, raw, partial, bad_date))
        for value, reviewed in cases:
            with self.subTest(reviewed=reviewed, fields=list(value)):
                with self.assertRaises(ContinuityError):
                    completion_checkpoint(fixture.repo, fixture.context_id, snapshot, value, privacy_reviewed=reviewed, writer_session_id="review-test", mei_root=fixture.mei)
                self.assertEqual(load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)["project_sha256"], snapshot["project_sha256"])
        self.assertEqual(list((fixture.mei / "trajectories/inbox").glob("*.json")), [])

    def test_stale_or_wrong_context_snapshots_cannot_complete_export_or_retire(self):
        from complete_continuity import completion_checkpoint, export_completed_trajectory, retire_completed_context
        from sync_continuity import handle_lifecycle_event

        fixture = completion_fixture()
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        handle_lifecycle_event(fixture.repo, fixture.context_id, "user_prompt_submit", mei_root=fixture.mei)
        rejected = completion_checkpoint(fixture.repo, fixture.context_id, snapshot, reviewed_trajectory(), privacy_reviewed=True, writer_session_id="stale", mei_root=fixture.mei)
        self.assertEqual(rejected["status"], "conflict")
        completed = self.complete(fixture)
        fresh = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        for operation in (
            lambda: export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei),
            lambda: retire_completed_context(fixture.repo, fixture.context_id, snapshot, writer_session_id="stale", mei_root=fixture.mei),
        ):
            self.assertEqual(operation()["status"], "conflict")
        wrong = dict(fresh, context_id="another-context")
        with self.assertRaisesRegex(ContinuityError, "identity"):
            retire_completed_context(fixture.repo, fixture.context_id, wrong, writer_session_id="wrong-context", mei_root=fixture.mei)

    def test_retirement_waits_for_export_and_conflicting_content_is_preserved_for_review(self):
        from complete_continuity import curator_handoff, export_completed_trajectory, retire_completed_context

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        pending = retire_completed_context(fixture.repo, fixture.context_id, snapshot, writer_session_id="too-early", mei_root=fixture.mei)
        self.assertEqual(pending["required_action"], "export_and_retire")
        self.assertEqual(load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)["project_sha256"], snapshot["project_sha256"])
        altered = dict(completed["trajectory"], evaluation="Different unreviewed evaluation.")
        rejected = export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, altered, mei_root=fixture.mei)
        self.assertEqual(rejected["status"], "conflict")
        first = export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
        source = fixture.mei / first["source_path"]
        source.write_text(json.dumps(altered), encoding="utf-8")  # Simulate an external conflicting edit.
        before = source.read_bytes()
        self.assertEqual(curator_handoff(fixture.repo, fixture.context_id, mei_root=fixture.mei)["status"], "conflict")
        self.assertEqual(retire_completed_context(fixture.repo, fixture.context_id, snapshot, writer_session_id="retire", mei_root=fixture.mei)["status"], "conflict")
        self.assertEqual(source.read_bytes(), before)

    def test_archive_replay_and_concurrent_exports_still_have_one_source(self):
        from complete_continuity import curator_handoff, export_completed_trajectory

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        def export(_):
            return export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(export, range(3)))
        self.assertEqual(sorted(item["status"] for item in results), ["already_exported", "already_exported", "exported"])
        source = fixture.mei / results[0]["source_path"]
        archived = fixture.mei / "trajectories/archive" / source.name
        archived.parent.mkdir(parents=True)
        source.rename(archived)  # One explicitly named synthetic source, not a directory move.
        replay = export(0)
        self.assertEqual(replay["status"], "already_exported")
        self.assertEqual(replay["source_path"], "trajectories/archive/" + source.name)
        self.assertEqual(list(source.parent.glob("*.json")), [])
        self.assertEqual(curator_handoff(fixture.repo, fixture.context_id, mei_root=fixture.mei)["source_path"], replay["source_path"])

    def test_dirty_completed_bookkeeping_requires_reconciliation_before_retirement(self):
        from complete_continuity import export_completed_trajectory, retire_completed_context

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
        path = fixture.context_dir / "checkpointer.json"
        bookkeeping = json.loads(path.read_text(encoding="utf-8"))
        bookkeeping["dirty"] = True  # Simulate external state corruption, not a lifecycle API.
        path.write_text(json.dumps(bookkeeping), encoding="utf-8")
        fresh = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        result = retire_completed_context(fixture.repo, fixture.context_id, fresh, writer_session_id="retire", mei_root=fixture.mei)
        self.assertEqual(result["status"], "needs_reconcile")
        self.assertEqual(result["required_action"], "explicit_reconcile")

    def test_interrupted_completion_and_retirement_are_visible_and_explicitly_recoverable(self):
        from complete_continuity import completion_checkpoint, export_completed_trajectory, retire_completed_context

        fixture = completion_fixture()
        initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        real_replace = os.replace
        def fail_checkpointer(source, target):
            if Path(target).name == "checkpointer.json":
                raise OSError("Synthetic failure between pair replacements.")
            return real_replace(source, target)
        with patch("os.replace", side_effect=fail_checkpointer):
            interrupted = self.complete(fixture)
        self.assertEqual(interrupted["status"], "needs_reconcile")
        fresh = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        self.assertEqual(fresh["status"], "needs_reconcile")
        blocked = completion_checkpoint(fixture.repo, fixture.context_id, fresh, reviewed_trajectory(), privacy_reviewed=True, writer_session_id="recover", mei_root=fixture.mei)
        self.assertEqual(blocked["required_action"], "explicit_reconcile")
        recovered = completion_checkpoint(fixture.repo, fixture.context_id, fresh, reviewed_trajectory(), privacy_reviewed=True, writer_session_id="recover", mei_root=fixture.mei, reconcile=True)
        self.assertEqual(recovered["completion"]["source_project_sha256"], initial["project_sha256"])
        fresh = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        export_completed_trajectory(fixture.repo, fixture.context_id, fresh, recovered["trajectory"], mei_root=fixture.mei)
        with patch("os.replace", side_effect=fail_checkpointer):
            interrupted = retire_completed_context(fixture.repo, fixture.context_id, fresh, writer_session_id="retire", mei_root=fixture.mei)
        self.assertEqual(interrupted["status"], "needs_reconcile")
        fresh = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        completion_checkpoint(fixture.repo, fixture.context_id, fresh, recovered["trajectory"], privacy_reviewed=True, writer_session_id="recover", mei_root=fixture.mei, reconcile=True)
        fresh = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        self.assertEqual(retire_completed_context(fixture.repo, fixture.context_id, fresh, writer_session_id="retire", mei_root=fixture.mei)["status"], "retired")

    def test_completion_receipt_cannot_forge_task_identity_generation_or_retirement(self):
        from complete_continuity import retire_completed_context

        for field, value in (("trajectory_id", "continuity-" + "f" * 64), ("source_generation", 99), ("retired", True)):
            with self.subTest(field=field):
                fixture = completion_fixture()
                self.complete(fixture)
                snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
                path = fixture.context_dir / "checkpointer.json"
                bookkeeping = copy.deepcopy(snapshot["checkpointer"])
                bookkeeping["completion"][field] = value
                path.write_text(json.dumps(bookkeeping), encoding="utf-8")
                with self.assertRaises(ContinuityError):
                    retire_completed_context(fixture.repo, fixture.context_id, snapshot, writer_session_id="retire", mei_root=fixture.mei)

    def test_cli_exercises_completion_export_retirement_and_read_only_curator_handoff(self):
        fixture = completion_fixture()
        snapshot_path = fixture.root / "fresh-snapshot.json"
        trajectory_path = fixture.root / "reviewed-trajectory.json"
        trajectory_path.write_text(json.dumps(reviewed_trajectory()), encoding="utf-8")
        for operation in ("checkpoint", "export", "retire", "curator"):
            snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            command = [
                sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/complete_continuity.py"), operation,
                "--repo-root", str(fixture.repo), "--context-id", fixture.context_id,
                "--mei-root", str(fixture.mei),
            ]
            if operation != "curator":
                command.extend(["--snapshot", str(snapshot_path)])
            if operation in ("checkpoint", "export"):
                command.extend(["--trajectory", str(trajectory_path)])
            if operation in ("checkpoint", "retire"):
                command.extend(["--writer-session-id", "cli-completion"])
            if operation == "checkpoint":
                command.append("--privacy-reviewed")
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], {"checkpoint": "completed", "export": "exported", "retire": "retired", "curator": "curator_handoff"}[operation])
        self.assertEqual(load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)["status"], "retired")

    def test_completion_does_not_invalidate_an_unrelated_context_in_the_shared_index(self):
        fixture = completion_fixture()
        other_dir = fixture.context_dir.parent / "other"
        other_project_raw = write_json(other_dir / "project.json", fixture_project(fixture.repo_id, "other"))
        index_path = fixture.mei / "registry/continuity.yaml"
        index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
        other_entry = dict(index["entries"][0], context_id="other")
        other_entry["project_path"] = f"memory/continuity/{fixture.repo_id}/other/project.json"
        other_entry["checkpointer_path"] = f"memory/continuity/{fixture.repo_id}/other/checkpointer.json"
        index["entries"].append(other_entry)
        raw = yaml.safe_dump(index).encode("utf-8")
        index_path.write_bytes(raw)
        bookkeeping = json.loads((fixture.context_dir / "checkpointer.json").read_text(encoding="utf-8"))
        bookkeeping.update(index_sha256=hashlib.sha256(raw).hexdigest(), index_bytes=len(raw), index_lines=len(raw.splitlines()))
        write_json(fixture.context_dir / "checkpointer.json", bookkeeping)
        write_json(other_dir / "checkpointer.json", dict(bookkeeping, context_id="other", project_sha256=hashlib.sha256(other_project_raw).hexdigest()))
        before = load_context(fixture.repo, "other", mei_root=fixture.mei)
        self.complete(fixture)
        after = load_context(fixture.repo, "other", mei_root=fixture.mei)
        self.assertEqual(after["status"], "clean")
        self.assertEqual(after["project_sha256"], before["project_sha256"])
        self.assertEqual(after["checkpointer"], before["checkpointer"])

    def test_export_refuses_a_junction_or_symlinked_inbox_before_writing(self):
        from complete_continuity import export_completed_trajectory

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        outside = fixture.root / "outside-inbox"
        outside.mkdir()
        link = fixture.mei / "trajectories/inbox"
        link.parent.mkdir()
        if os.name == "nt":
            created = run_mklink_junction(link, outside)
            self.assertEqual(created.returncode, 0, process_diagnostic(created))
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ContinuityError, "symlink|junction"):
            export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
        self.assertEqual(list(outside.iterdir()), [])

    def test_export_rejects_windows_reparse_attributes_before_publication(self):
        from complete_continuity import export_completed_trajectory

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        inbox = fixture.mei / "trajectories/inbox"
        inbox.mkdir(parents=True)
        original_lstat = Path.lstat

        def reparse_inbox(path, *args, **kwargs):
            metadata = original_lstat(path, *args, **kwargs)
            if path == inbox:
                return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400)
            return metadata

        with patch.object(Path, "lstat", reparse_inbox), patch("complete_continuity.publish_new_file") as publish:
            with self.assertRaisesRegex(ContinuityError, "junction"):
                export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
            publish.assert_not_called()
        self.assertEqual(list(inbox.iterdir()), [])

    def test_failed_atomic_publication_can_be_retried_without_a_partial_source(self):
        from complete_continuity import curator_handoff, export_completed_trajectory

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        with patch("complete_continuity.publish_new_file", side_effect=OSError("Synthetic publication failure.")):
            with self.assertRaises(OSError):
                export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)
        self.assertEqual(curator_handoff(fixture.repo, fixture.context_id, mei_root=fixture.mei)["required_action"], "export_and_retire")
        self.assertEqual(export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)["status"], "exported")

    def test_index_drift_cannot_authorize_an_unreceipted_terminal_overlay(self):
        from complete_continuity import retire_completed_context

        fixture = completion_fixture()
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        raw = write_json(fixture.context_dir / "project.json", dict(snapshot["project"], status="completed"))
        bookkeeping = dict(snapshot["checkpointer"], project_sha256=hashlib.sha256(raw).hexdigest(), index_sha256="e" * 64)
        write_json(fixture.context_dir / "checkpointer.json", bookkeeping)
        with self.assertRaisesRegex(ContinuityError, "index status"):
            retire_completed_context(fixture.repo, fixture.context_id, snapshot, writer_session_id="retire", mei_root=fixture.mei)

    def test_equivalent_json_key_order_does_not_create_a_false_export_conflict(self):
        from complete_continuity import export_completed_trajectory

        fixture = completion_fixture()
        completed = self.complete(fixture)
        snapshot = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        reordered = json.loads(json.dumps(completed["trajectory"], sort_keys=True))
        result = export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, reordered, mei_root=fixture.mei)
        self.assertEqual(result["status"], "exported")
        self.assertEqual(export_completed_trajectory(fixture.repo, fixture.context_id, snapshot, completed["trajectory"], mei_root=fixture.mei)["status"], "already_exported")


if __name__ == "__main__":
    unittest.main()
