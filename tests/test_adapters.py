"""Behavior tests for the Phase 4 GUI/TUI continuity read adapters."""

from __future__ import annotations

import sys
import hashlib
import json
import os
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_continuity import ContinuityError  # noqa: E402
from tests.test_continuity import ContinuityFixture, fixture_root, write_json  # noqa: E402


class TestHostEvents(unittest.TestCase):
    """The agreed host seam is the native JSON stdin/stdout command contract."""

    def make_fixture(self):
        root = ROOT / ".tmp" / "host-tests" / uuid.uuid4().hex
        root.mkdir(parents=True)
        return ContinuityFixture(root)

    def host_event(self, fixture, event, *, selected_home=None, observed_home=None, **metadata):
        envelope = dict(
            cwd=str(fixture.repo), session_id="selected-tui", hook_event_name=event,
            transcript_path=str(fixture.root / "must-not-read.jsonl"),
        )
        envelope.update(metadata)
        command = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/continuity_adapters.py"),
             "--repo-root", str(fixture.repo), "--mei-root", str(fixture.mei),
             "--context-id", fixture.context_id, "--host", "tui", "--writer-thread-id", "selected-tui"]
        if selected_home:
            command.extend(["--codex-home", str(selected_home)])
        env = os.environ.copy()
        if observed_home:
            env["CODEX_HOME"] = str(observed_home)
        return subprocess.run(
            command, input=json.dumps(envelope), capture_output=True, text=True, encoding="utf-8", timeout=20, env=env,
        )

    def test_native_startup_returns_selected_semantic_context_without_a_prompted_read_or_write(self):
        from continuity_adapters import read_tui_context

        fixture = self.make_fixture()
        before = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        child = self.host_event(fixture, "SessionStart", source="startup")
        self.assertEqual(child.returncode, 0, child.stderr)
        wire = json.loads(child.stdout)
        self.assertTrue(wire["continue"])
        self.assertEqual(wire["hookSpecificOutput"]["hookEventName"], "SessionStart")
        recovered = json.loads(wire["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(recovered["project"]["goal"], "Read one shared continuity context.")
        self.assertEqual(recovered["project"]["next_step"], "Run the selected validation checks.")
        self.assertEqual(recovered["context_id"], "shared")
        self.assertEqual(recovered["generation"], 0)
        self.assertNotIn("transcript_path", child.stdout)
        self.assertEqual(read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei), before)

    def test_native_prompt_marks_only_dirty_and_manual_compact_waits_for_explicit_sync(self):
        from continuity_adapters import read_tui_context
        from sync_continuity import sync_context

        fixture = self.make_fixture()
        before = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        prompt = self.host_event(fixture, "UserPromptSubmit", prompt="Synthetic input must never be retained.")
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        dirty = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        self.assertEqual(dirty["status"], "dirty")
        self.assertEqual(dirty["project"], before["project"])
        self.assertNotIn("Synthetic input", json.dumps(dirty))
        gate = self.host_event(fixture, "PreCompact", trigger="manual")
        # The native hook protocol consumes a valid JSON control response only
        # when the command exits successfully; continue:false is the gate.
        self.assertEqual(gate.returncode, 0)
        self.assertFalse(json.loads(gate.stdout)["continue"])
        sync_context(fixture.repo, fixture.context_id, dirty, dirty["project"], writer_session_id="selected-tui", mei_root=fixture.mei)
        clean = self.host_event(fixture, "PreCompact", trigger="manual")
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertTrue(json.loads(clean.stdout)["continue"])

    def test_native_automatic_compact_fails_open_on_workspace_mismatch_without_a_write(self):
        from continuity_adapters import read_tui_context

        fixture = self.make_fixture()
        before = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        child = self.host_event(fixture, "PreCompact", trigger="auto", cwd=str(fixture.root))
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue(json.loads(child.stdout)["continue"])
        self.assertEqual(read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei), before)

    def test_native_manual_compact_failure_returns_success_with_continue_false(self):
        from continuity_adapters import read_tui_context

        fixture = self.make_fixture()
        before = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        child = self.host_event(fixture, "PreCompact", trigger="manual", cwd=str(fixture.root))
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertFalse(json.loads(child.stdout)["continue"])
        self.assertEqual(read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei), before)

    def test_native_compact_records_reconcile_and_restores_it_at_the_post_compact_event(self):
        from continuity_adapters import read_tui_context

        fixture = self.make_fixture()
        child = self.host_event(fixture, "PreCompact", trigger="auto")
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue(json.loads(child.stdout)["continue"])
        debt = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        self.assertEqual(debt["status"], "needs_reconcile")
        restored = self.host_event(fixture, "PostCompact", trigger="auto")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertTrue(json.loads(restored.stdout)["continue"])
        self.assertIn('"status": "needs_reconcile"', json.loads(restored.stdout)["systemMessage"])
        self.assertIn("Read one shared continuity context.", json.loads(restored.stdout)["systemMessage"])
        self.assertIn("Run the selected validation checks.", json.loads(restored.stdout)["systemMessage"])
        self.assertEqual(read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei), debt)

    def test_readback_witness_cannot_write_the_selected_writers_bookkeeping(self):
        from continuity_adapters import read_tui_context

        fixture = self.make_fixture()
        before = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        child = self.host_event(fixture, "UserPromptSubmit", session_id="gui-witness", prompt="Readback only.")
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue(json.loads(child.stdout)["continue"])
        self.assertEqual(read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei), before)

    def test_native_hook_is_inert_in_another_home_even_for_a_matching_thread_id(self):
        from continuity_adapters import read_tui_context

        fixture = self.make_fixture()
        before = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        child = self.host_event(fixture, "UserPromptSubmit", selected_home=fixture.root / "tui-home", observed_home=fixture.root / "gui-home")
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue(json.loads(child.stdout)["suppressOutput"])
        self.assertEqual(read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei), before)



class TestAdapters(unittest.TestCase):
    def test_adapter_module_is_importable_from_the_repository_package(self):
        from scripts.continuity_adapters import SUPPORTED_HOSTS

        self.assertEqual(SUPPORTED_HOSTS, frozenset({"gui", "tui"}))

    def test_gui_and_tui_adapters_resume_one_context_without_writing(self):
        from continuity_adapters import read_gui_context, read_tui_context

        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            state_files = [
                fixture.mei / "registry" / "continuity.yaml",
                fixture.context_dir / "project.json",
                fixture.context_dir / "checkpointer.json",
            ]
            before = {path: path.read_bytes() for path in state_files}

            gui = read_gui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            tui = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(gui["host"], "gui")
            self.assertEqual(tui["host"], "tui")
            self.assertEqual(gui["status"], tui["status"])
            self.assertEqual(gui["status"], "clean")
            self.assertEqual(gui["repo_id"], tui["repo_id"])
            self.assertEqual(gui["context_id"], tui["context_id"])
            self.assertEqual(gui["project_sha256"], tui["project_sha256"])
            self.assertEqual(gui["project"]["next_step"], tui["project"]["next_step"])
            self.assertEqual({path: path.read_bytes() for path in state_files}, before)

    def test_both_adapters_expose_loader_statuses_without_repairing_them(self):
        from continuity_adapters import read_gui_context, read_tui_context

        cases = ("dirty", "stale", "needs_reconcile", "conflict")
        for expected in cases:
            with fixture_root() as temp:
                fixture = ContinuityFixture(temp)
                checkpointer_path = fixture.context_dir / "checkpointer.json"
                checkpointer = json.loads(checkpointer_path.read_text(encoding="utf-8"))
                if expected == "dirty":
                    checkpointer["dirty"] = True
                    write_json(checkpointer_path, checkpointer)
                elif expected == "stale":
                    project = dict(fixture.project, stable_refs=[{"kind": "file", "value": "missing.md"}])
                    project_bytes = write_json(fixture.context_dir / "project.json", project)
                    checkpointer["project_sha256"] = hashlib.sha256(project_bytes).hexdigest()
                    write_json(checkpointer_path, checkpointer)
                elif expected == "needs_reconcile":
                    write_json(fixture.context_dir / "project.json", dict(fixture.project, next_step="Changed without sync."))
                else:
                    checkpointer["dirty"] = True
                    checkpointer["needs_reconcile"] = True
                    checkpointer["last_conflict"] = {
                        "expected_generation": 0,
                        "observed_generation": 1,
                        "required_action": "reconcile",
                    }
                    write_json(checkpointer_path, checkpointer)

                gui = read_gui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
                tui = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

                self.assertEqual(gui["status"], tui["status"])
                self.assertEqual(gui["status"], expected)
                self.assertEqual(gui["diagnostics"], tui["diagnostics"])

    def test_generic_adapter_rejects_unknown_hosts_before_loading(self):
        from continuity_adapters import read_context

        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            with self.assertRaisesRegex(ContinuityError, "host must be one of"):
                read_context(fixture.repo, fixture.context_id, host="other", mei_root=fixture.mei)
