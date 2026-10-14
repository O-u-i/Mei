"""Behavior tests for the Phase 4 GUI/TUI continuity read adapters."""

from __future__ import annotations

import sys
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_continuity import ContinuityError  # noqa: E402
from tests.test_continuity import ContinuityFixture, fixture_root, write_json  # noqa: E402


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
