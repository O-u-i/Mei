"""Behavior tests for the Phase 4 GUI/TUI continuity read adapters."""

from __future__ import annotations

import sys
import hashlib
import json
import os
import subprocess
import unittest
import uuid
from unittest.mock import patch
from types import SimpleNamespace
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
        env["CODEX_PERMISSION_PROFILE"] = "mei-maintainer"
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

    def test_stop_requests_one_current_agent_save_and_does_not_loop_or_write(self):
        from continuity_adapters import read_tui_context

        fixture = self.make_fixture()
        self.assertTrue(json.loads(self.host_event(fixture, "UserPromptSubmit").stdout)["continue"])
        before = read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        first = self.host_event(fixture, "Stop")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_wire = json.loads(first.stdout)
        self.assertFalse(first_wire["continue"])
        self.assertTrue(first_wire["meiStopCheck"]["attempted"])
        self.assertEqual(read_tui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei), before)
        second = self.host_event(fixture, "Stop", stop_hook_active=True)
        self.assertTrue(json.loads(second.stdout)["continue"])
        self.assertNotIn("must-not-read", second.stdout)

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


class TestSharedHomes(unittest.TestCase):
    """GUI and both TUI homes read one Mei base; only the configured writer records events."""

    def make_fixture(self):
        root = ROOT / ".tmp" / "host-tests" / uuid.uuid4().hex
        root.mkdir(parents=True)
        fixture = ContinuityFixture(root)
        memory = fixture.mei / "memory" / "markdown"
        memory.mkdir(parents=True)
        (memory / "MEMORY.md").write_text("# Mei Memory\n\nShared base memory marker.\n", encoding="utf-8")
        return fixture

    def run_hook(self, fixture, event, *, home, extra=(), **metadata):
        envelope = dict(cwd=str(fixture.repo), session_id="any-thread", hook_event_name=event)
        envelope.update(metadata)
        command = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/continuity_adapters.py"),
                   "--repo-root", str(fixture.repo), "--mei-root", str(fixture.mei), *extra]
        env = dict(os.environ, CODEX_HOME=str(home), CODEX_PERMISSION_PROFILE="mei-maintainer")
        child = subprocess.run(command, input=json.dumps(envelope), capture_output=True, text=True,
                               encoding="utf-8", timeout=20, env=env)
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout)

    def test_every_home_reads_the_same_context_and_memory_without_a_context_flag(self):
        fixture = self.make_fixture()
        seen = []
        for home in ("gui-home", "tui-gpt-home", "tui-claude-home"):
            wire = self.run_hook(fixture, "SessionStart", home=fixture.root / home, source="startup")
            payload = json.loads(wire["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(payload["context_id"], "shared")
            self.assertEqual(payload["memory"]["status"], "ok")
            self.assertIn("Shared base memory marker.", payload["memory"]["text"])
            seen.append((payload["project_sha256"], payload["memory"]["index_sha256"]))
        self.assertEqual(len(set(seen)), 1)

    def test_missing_context_is_reported_and_never_created(self):
        fixture = self.make_fixture()
        (fixture.mei / "registry" / "continuity.yaml").write_text(
            "schema_version: v0.1.0\nversion: v1.1.0\nentries: []\n", encoding="utf-8")
        wire = self.run_hook(fixture, "SessionStart", home=fixture.root / "gui-home")
        payload = json.loads(wire["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(payload["status"], "context_missing")
        self.assertIn("Shared base memory marker.", payload["memory"]["text"])
        self.assertFalse((fixture.mei / "memory" / "continuity" / fixture.repo_id / "new").exists())

    def test_other_home_witness_cannot_write_but_still_gates_manual_compact(self):
        from continuity_adapters import read_gui_context

        fixture = self.make_fixture()
        writer = ["--codex-home", str(fixture.root / "tui-home")]
        self.run_hook(fixture, "UserPromptSubmit", home=fixture.root / "tui-home", extra=writer)
        dirty = read_gui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
        self.assertEqual(dirty["status"], "dirty")
        gate = self.run_hook(fixture, "PreCompact", home=fixture.root / "gui-home", extra=writer, trigger="manual")
        self.assertFalse(gate["continue"])
        auto = self.run_hook(fixture, "PreCompact", home=fixture.root / "gui-home", extra=writer, trigger="auto")
        self.assertTrue(auto["continue"])
        self.assertEqual(read_gui_context(fixture.repo, fixture.context_id, mei_root=fixture.mei), dirty)

    def test_injection_rollback_switch_removes_memory_but_keeps_continuity(self):
        fixture = self.make_fixture()
        (fixture.mei / "local").mkdir()
        (fixture.mei / "local" / "paths.toml").write_text("[host]\nmemory_injection = false\n", encoding="utf-8")
        wire = self.run_hook(fixture, "SessionStart", home=fixture.root / "gui-home")
        payload = json.loads(wire["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("memory", payload)
        self.assertEqual(payload["context_id"], "shared")

    def test_broken_index_fails_open_and_does_not_hide_shared_memory(self):
        fixture = self.make_fixture()
        (fixture.mei / "registry/continuity.yaml").write_text("entries: wrong\n", encoding="utf-8")
        wire = self.run_hook(fixture, "SessionStart", home=fixture.root / "gui-home")
        self.assertTrue(wire["continue"])
        payload = json.loads(wire["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(payload["status"], "context_invalid")
        self.assertEqual(payload["memory"]["status"], "ok")

    def test_configured_memory_root_is_used_instead_of_hardcoded_default(self):
        fixture = self.make_fixture()
        personal = fixture.mei / "memory/markdown/personal"
        personal.mkdir()
        (personal / "MEMORY.md").write_text("# Configured root marker\n", encoding="utf-8")
        (fixture.mei / "local").mkdir()
        (fixture.mei / "local/paths.toml").write_text(f"[paths]\nmemory_root = '{personal}'\n", encoding="utf-8")
        wire = self.run_hook(fixture, "SessionStart", home=fixture.root / "gui-home")
        payload = json.loads(wire["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(payload["memory"]["root"], str(personal.resolve()))
        self.assertIn("Configured root marker", payload["memory"]["text"])

    def test_native_lifecycle_uses_selected_sandbox_and_forwards_metadata_only(self):
        from continuity_adapters import _sandboxed_lifecycle

        fixture = self.make_fixture()
        native = fixture.root / "native.exe"
        native.write_bytes(b"synthetic path only; subprocess is mocked")
        (fixture.mei / "local").mkdir()
        (fixture.mei / "local/paths.toml").write_text(
            f"[paths]\ncodex_cli = '{native}'\n[host]\ncontinuity_lifecycle_profile = 'mei-continuity-writer'\n", encoding="utf-8")
        args = SimpleNamespace(mei_root=fixture.mei, repo_root=fixture.repo)
        envelope = {"cwd": str(fixture.repo), "session_id": "synthetic-session", "hook_event_name": "UserPromptSubmit",
                    "prompt": "Never forward raw prompt text.", "transcript_path": "never-read.jsonl"}
        with patch("continuity_adapters.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='{"continue":true}')) as run:
            result = _sandboxed_lifecycle(args, envelope, "gui", fixture.root / "gui-home", fixture.context_id)
        self.assertTrue(result["continue"])
        command = run.call_args.args[0]
        self.assertEqual(command[1:4], ["sandbox", "-P", "mei-continuity-writer"])
        self.assertIn("--lifecycle-child", command)
        metadata = json.loads(run.call_args.kwargs["input"])
        self.assertNotIn("prompt", metadata)
        self.assertNotIn("transcript_path", metadata)

    def test_unavailable_projection_sandbox_preserves_post_compact_continuity(self):
        fixture = self.make_fixture()
        (fixture.mei / "local").mkdir()
        (fixture.mei / "local/paths.toml").write_text(
            f"[paths]\nobsidian_vault = '{fixture.root / 'external-vault'}'\n"
            "[host]\nobsidian_projection_profile = 'mei-obsidian-projection'\n", encoding="utf-8")
        wire = self.run_hook(fixture, "PostCompact", home=fixture.root / "gui-home", trigger="auto")
        self.assertTrue(wire["continue"])
        payload = json.loads(wire["systemMessage"].split(": ", 1)[1])
        self.assertEqual(payload["context_id"], "shared")
        self.assertEqual(payload["memory"]["status"], "ok")
        self.assertEqual(payload["obsidian"]["status"], "obsidian_projection_stale")

    def test_direct_root_post_compact_needs_no_projection_sandbox_or_note_copy(self):
        fixture = self.make_fixture()
        memory = fixture.mei / "memory/markdown"
        (fixture.mei / "local").mkdir()
        (fixture.mei / "local/paths.toml").write_text(
            f"[paths]\nmemory_root = '{memory}'\nobsidian_vault = '{memory}'\n"
            "obsidian_memory_dir = ''\n[host]\nobsidian_projection_profile = 'mei-obsidian-projection'\n", encoding="utf-8")
        before = {path: path.read_bytes() for path in memory.rglob("*") if path.is_file()}
        wire = self.run_hook(fixture, "PostCompact", home=fixture.root / "gui-home", trigger="auto")
        payload = json.loads(wire["systemMessage"].split(": ", 1)[1])
        self.assertEqual(payload["obsidian"]["status"], "synced", payload)
        self.assertTrue(payload["obsidian"]["direct_root"])
        self.assertEqual({path: path.read_bytes() for path in memory.rglob("*") if path.is_file()}, before)
        self.assertFalse((memory / "views").exists())
