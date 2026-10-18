"""Synthetic enrollment, permissions plans and compact/readback acceptance.

No fixture event is evidence of an actual GUI event or native hook trust.
"""
import hashlib
import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from tests.test_continuity import ContinuityFixture, fixture_root
from scripts.mei import setup, attach, status, save_enrolled
from check_continuity import ContinuityError, load_context
from continuity_adapters import select_context
from continuity_handoff import save_handoff
from sync_continuity import handle_lifecycle_event

ROOT = Path(__file__).resolve().parents[1]


class EnrollmentTests(unittest.TestCase):
    def test_denied_config_does_not_publish_or_transfer_attachment(self):
        from scripts.mei import _replace_record as replace_record

        for previous_writer in (False, True):
            with self.subTest(previous_writer=previous_writer), fixture_root() as directory:
                fixture, homes = self.fixture(directory)
                attachment = fixture.mei / f"local/attachments/{fixture.repo_id}.json"
                if previous_writer:
                    attach(fixture.mei, fixture.repo, context_id="shared", host="gui1",
                           session_id="session-a", role="writer", apply=True)
                before = attachment.read_bytes() if attachment.exists() else None
                settings = homes[1] / "config.toml"
                config_before = settings.read_bytes()

                def deny_config(path, raw):
                    if path == settings:
                        raise PermissionError("synthetic host config denial")
                    return replace_record(path, raw)

                with patch("scripts.mei._replace_record", side_effect=deny_config):
                    with self.assertRaises(PermissionError):
                        attach(fixture.mei, fixture.repo, context_id="shared", host="gui2",
                               session_id="session-b", role="writer",
                               takeover=previous_writer, apply=True)
                self.assertEqual(attachment.read_bytes() if attachment.exists() else None, before)
                self.assertEqual(settings.read_bytes(), config_before)

    def test_explicit_attach_host_does_not_change_other_home(self):
        with fixture_root() as directory:
            fixture, homes = self.fixture(directory)
            original = (homes[0] / "config.toml").read_bytes()
            plan = attach(fixture.mei, fixture.repo, context_id="shared",
                          host="gui2", session_id="session-b", role="writer")
            self.assertNotIn(str(homes[0] / "config.toml"), plan["changed_files"])
            attach(fixture.mei, fixture.repo, context_id="shared", host="gui2",
                   session_id="session-b", role="writer", apply=True)
            self.assertEqual((homes[0] / "config.toml").read_bytes(), original)
            self.assertIn("mei-context-", (homes[1] / "config.toml").read_text())

    def test_writer_transfer_rechecks_dirty_state_and_queued_old_writes(self):
        from contextlib import contextmanager
        from scripts.mei import _context_lock as real_lock

        with fixture_root() as directory:
            fixture, _ = self.fixture(directory)
            attach(fixture.mei, fixture.repo, context_id="shared", host="gui1",
                   session_id="session-a", role="writer", apply=True)
            before = load_context(fixture.repo, "shared", mei_root=fixture.mei)
            attachment = fixture.mei / f"local/attachments/{fixture.repo_id}.json"
            original = attachment.read_bytes()

            @contextmanager
            def prompt_arrives(directory, timeout):
                if directory == fixture.context_dir:
                    handle_lifecycle_event(fixture.repo, "shared", "user_prompt_submit",
                                           mei_root=fixture.mei, writer_session_id="gui1-session-a")
                with real_lock(directory, timeout):
                    yield

            with patch("scripts.mei._context_lock", prompt_arrives):
                with self.assertRaisesRegex(ContinuityError, "context changed"):
                    attach(fixture.mei, fixture.repo, context_id="shared", host="gui2",
                           session_id="session-b", role="writer", takeover=True, apply=True)
            self.assertEqual(attachment.read_bytes(), original)
            dirty = load_context(fixture.repo, "shared", mei_root=fixture.mei)
            save_handoff(fixture.repo, "shared", dirty, dirty["project"],
                         mei_root=fixture.mei, writer_session_id="gui1-session-a")
            clean = load_context(fixture.repo, "shared", mei_root=fixture.mei)
            attach(fixture.mei, fixture.repo, context_id="shared", host="gui2",
                   session_id="session-b", role="writer", takeover=True, apply=True)
            for operation in (
                lambda: save_handoff(fixture.repo, "shared", clean, before["project"],
                                     mei_root=fixture.mei, writer_session_id="gui1-session-a"),
                lambda: handle_lifecycle_event(fixture.repo, "shared", "user_prompt_submit",
                                               mei_root=fixture.mei, writer_session_id="gui1-session-a"),
            ):
                with self.assertRaisesRegex(ContinuityError, "enrolled writer changed"):
                    operation()
            self.assertEqual(load_context(fixture.repo, "shared", mei_root=fixture.mei), clean)

    def fixture(self, directory):
        fixture = ContinuityFixture(directory)
        (fixture.mei / "local").mkdir()
        skills = directory / "skills"
        skills.mkdir()
        homes = [directory / host for host in ("gui1", "gui2")]
        for home in homes:
            home.mkdir()
            (home / "config.toml").write_text('default_permissions = ":read-only"\n', encoding="utf-8")
        (fixture.mei / "local/paths.toml").write_text(
            f"[paths]\nmei_root = '{fixture.mei}'\nskills_root = '{skills}'\n"
            f"codex_home_gui1 = '{homes[0]}'\ncodex_home_gui2 = '{homes[1]}'\n", encoding="utf-8")
        return fixture, homes

    def test_setup_is_plan_first_and_idempotent_preserving_unrelated_hooks(self):
        with fixture_root() as directory:
            fixture, homes = self.fixture(directory)
            original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "unrelated"}]}]}}
            hooks = homes[0] / "hooks.json"
            hooks.write_text(json.dumps(original), encoding="utf-8")
            before = hooks.read_bytes()
            plan = setup(fixture.mei, ["gui1", "gui2"])
            self.assertTrue(plan["changed_files"])
            self.assertEqual(hooks.read_bytes(), before)
            self.assertFalse((fixture.mei / "local/host-setup.json").exists())
            setup(fixture.mei, ["gui1", "gui2"], apply=True)
            self.assertEqual(json.loads(hooks.read_bytes())["hooks"]["Stop"][0], original["hooks"]["Stop"][0])
            rerun = setup(fixture.mei, ["gui1", "gui2"], apply=True)
            self.assertEqual(rerun["changed_files"], [])
            self.assertFalse(rerun["live_compact_verified"])

    def test_existing_context_attachment_is_readonly_by_default_and_single_writer(self):
        with fixture_root() as directory:
            fixture, homes = self.fixture(directory)
            setup(fixture.mei, ["gui1", "gui2"], apply=True)
            attach(fixture.mei, fixture.repo, context_id="shared", apply=True)
            before = load_context(fixture.repo, "shared", mei_root=fixture.mei)
            self.assertIsNone(status(fixture.mei, fixture.repo)["writer"])
            self.assertEqual(select_context(fixture.repo, mei_root=fixture.mei)["selection"], "explicit_attachment")
            result = attach(fixture.mei, fixture.repo, context_id="shared", apply=True)
            self.assertEqual(result["changed_files"], [])
            attach(fixture.mei, fixture.repo, context_id="shared", host="gui1", session_id="session-a", role="writer", apply=True)
            with self.assertRaises(ContinuityError):
                attach(fixture.mei, fixture.repo, context_id="shared", host="gui2", session_id="session-b", role="writer", apply=True)
            self.assertEqual(load_context(fixture.repo, "shared", mei_root=fixture.mei), before)
            for home in homes:
                text = (home / "config.toml").read_text()
                self.assertIn(str(fixture.context_dir).replace("\\", "\\\\"), text)
                self.assertNotIn("trusted_hash", text)

    def test_new_project_context_creation_keeps_existing_semantic_bytes(self):
        with fixture_root() as directory:
            fixture, homes = self.fixture(directory)
            repo = directory / "another-project"
            repo.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            project_path = fixture.context_dir / "project.json"
            before = project_path.read_bytes()
            setup(fixture.mei, ["gui1", "gui2"], apply=True)
            plan = attach(fixture.mei, repo, goal="Explicit test task.")
            self.assertEqual(len(plan["technical_index_rebase"]), 1)
            attach(fixture.mei, repo, goal="Explicit test task.", apply=True)
            self.assertEqual(project_path.read_bytes(), before)
            self.assertEqual(load_context(fixture.repo, "shared", mei_root=fixture.mei)["status"], "clean")
            self.assertEqual(attach(fixture.mei, repo, apply=True)["changed_files"], [])

    def test_agent_save_then_automatic_compact_and_readback_keeps_payload(self):
        with fixture_root() as directory:
            fixture, homes = self.fixture(directory)
            setup(fixture.mei, ["gui1", "gui2"], apply=True)
            attach(fixture.mei, fixture.repo, context_id="shared", host="gui1", session_id="session-a", role="writer", apply=True)
            handle_lifecycle_event(fixture.repo, "shared", "user_prompt_submit", mei_root=fixture.mei, writer_session_id="gui1-session-a")
            snapshot = load_context(fixture.repo, "shared", mei_root=fixture.mei)
            project = dict(snapshot["project"], next_step="Resume the current agent's explicitly bounded task.")
            saved = save_handoff(fixture.repo, "shared", snapshot, project, mei_root=fixture.mei, writer_session_id="gui1-session-a")
            self.assertEqual(saved["status"], "handoff_saved")
            clean = load_context(fixture.repo, "shared", mei_root=fixture.mei)
            self.assertEqual(clean["status"], "clean")
            handle_lifecycle_event(fixture.repo, "shared", "precompact_auto", mei_root=fixture.mei, writer_session_id="gui1-session-a")
            envelope = {"hook_event_name": "PostCompact", "cwd": str(fixture.repo), "session_id": "session-a", "trigger": "auto"}
            command = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/continuity_adapters.py"),
                       "--mei-root", str(fixture.mei), "--host", "gui1"]
            child = subprocess.run(command, input=json.dumps(envelope), capture_output=True, encoding="utf-8",
                                   env=dict(os.environ, CODEX_HOME=str(homes[0])))
            self.assertEqual(child.returncode, 0, child.stderr)
            wire = json.loads(child.stdout)
            recovered = json.loads(wire["systemMessage"].split(": ", 1)[1])
            self.assertEqual(recovered["project"]["next_step"], project["next_step"])
            self.assertEqual(recovered["project_sha256"], clean["project_sha256"])
            self.assertEqual(recovered["status"], "needs_reconcile")
            # No native CLI in this fixture: a denied/unavailable diagnostic
            # writer must be reported without corrupting recovered JSON.
            self.assertEqual(recovered["event_diagnostics"], "native_sandbox_unavailable")
            metadata = {"hook_event_name": "PostCompact", "session_id": "session-a", "trigger": "auto",
                        "finished": True, "allow": True, "outcome": "adapter_returned"}
            receipt_child = subprocess.run([*command, "--repo-root", str(fixture.repo), "--diagnostic-child"],
                                           input=json.dumps(metadata), capture_output=True, encoding="utf-8")
            self.assertEqual(json.loads(receipt_child.stdout)["diagnostics"], "recorded")
            receipt = status(fixture.mei, fixture.repo)["events"]["gui1"]["PostCompact"]
            self.assertEqual(receipt["stage"], "finished")
            self.assertFalse(receipt["native_event_verified"])
            # A native invocation selects via attachment, not --context-id.
            # The diagnostic subprocess still receives that resolved context.
            from continuity_adapters import main as adapter_main
            import io
            from contextlib import redirect_stdout
            with patch("continuity_adapters._event_receipt", return_value="recorded") as record, \
                    patch("continuity_adapters.sys.stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(envelope).encode()))), \
                    patch.dict(os.environ, CODEX_HOME=str(homes[0])), redirect_stdout(io.StringIO()):
                self.assertEqual(adapter_main(["--mei-root", str(fixture.mei), "--host", "gui1"]), 0)
                self.assertTrue(record.called)
                self.assertEqual(record.call_args.args[0].context_id, "shared")

    def test_enrolled_save_requires_matching_writer_and_native_sandbox(self):
        with fixture_root() as directory:
            fixture, homes = self.fixture(directory)
            attach(fixture.mei, fixture.repo, context_id="shared", host="gui1", session_id="session-a", role="writer", apply=True)
            native = directory / "native.exe"
            native.write_bytes(b"fixture, never executed")
            config = fixture.mei / "local/paths.toml"
            config.write_text(config.read_text() + f"codex_cli = '{native}'\n", encoding="utf-8")
            snapshot = load_context(fixture.repo, "shared", mei_root=fixture.mei)
            project = snapshot["project"]
            (directory / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
            (directory / "project.json").write_text(json.dumps(project), encoding="utf-8")
            with patch.dict(os.environ, CODEX_HOME=str(homes[0]), CODEX_THREAD_ID="wrong"):
                with self.assertRaises(ContinuityError):
                    save_enrolled(fixture.mei, fixture.repo, "shared", directory / "snapshot.json", directory / "project.json", writer_session_id="gui1-session-a")
            with patch.dict(os.environ, CODEX_HOME=str(homes[0]), CODEX_THREAD_ID="session-a"), \
                    patch("scripts.mei._repository", return_value=fixture.repo), \
                    patch("scripts.mei._attachment", return_value={"context_id": "shared", "writer": {"host": "gui1", "session_id": "session-a"}, "permission_profile": "mei-context-" + fixture.repo_id[5:]}), \
                    patch("scripts.mei.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='{"status":"handoff_saved"}')) as run:
                result = save_enrolled(fixture.mei, fixture.repo, "shared", directory / "snapshot.json", directory / "project.json", writer_session_id="gui1-session-a")
                self.assertEqual(result["status"], "handoff_saved")
                self.assertEqual(run.call_args.args[0][1:4], ["sandbox", "-P", "mei-context-" + fixture.repo_id[5:]])
                self.assertIn("--input-stdin", run.call_args.args[0])
                self.assertNotIn("--snapshot", run.call_args.args[0])
                self.assertNotIn("--project", run.call_args.args[0])
                self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"snapshot": snapshot, "project": project})
                self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(homes[0].resolve()))
                self.assertEqual(run.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
            # GUI1 may omit CODEX_HOME: forward the already verified default,
            # never choose a different host or change the parent environment.
            default_home = directory / "default-user/.codex"
            default_home.mkdir(parents=True)
            config_before = config.read_text()
            config.write_text(config_before.replace(str(homes[0]), str(default_home)), encoding="utf-8")
            with patch.dict(os.environ, CODEX_THREAD_ID="session-a"), \
                    patch("scripts.mei.Path.home", return_value=default_home.parent), \
                    patch("scripts.mei._repository", return_value=fixture.repo), \
                    patch("scripts.mei._attachment", return_value={"context_id": "shared",
                          "writer": {"host": "gui1", "session_id": "session-a"},
                          "permission_profile": "mei-context-" + fixture.repo_id[5:]}), \
                    patch("scripts.mei.subprocess.run", return_value=SimpleNamespace(
                        returncode=0, stdout='{"status":"handoff_saved"}')) as run:
                os.environ.pop("CODEX_HOME", None)
                save_enrolled(fixture.mei, fixture.repo, "shared", directory / "snapshot.json",
                              directory / "project.json", writer_session_id="gui1-session-a")
                self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(default_home.resolve()))
                self.assertNotIn("CODEX_HOME", os.environ)
            config.write_text(config_before, encoding="utf-8")
            with patch.dict(os.environ, CODEX_HOME=str(homes[0]), CODEX_THREAD_ID="session-a"), \
                    patch("scripts.mei._repository", return_value=fixture.repo), \
                    patch("scripts.mei._attachment", return_value={"context_id": "shared",
                          "writer": {"host": "gui1", "session_id": "session-a"},
                          "permission_profile": "mei-context-" + fixture.repo_id[5:]}), \
                    patch("scripts.mei.subprocess.run", return_value=SimpleNamespace(
                        returncode=1, stdout="", stderr="write cap sid file PRIVATE-SYNTHETIC-STDERR")):
                with self.assertRaises(ContinuityError) as caught:
                    save_enrolled(fixture.mei, fixture.repo, "shared", directory / "snapshot.json",
                                  directory / "project.json", writer_session_id="gui1-session-a")
                self.assertIn("runtime_cap_sid_unavailable", str(caught.exception))
                self.assertNotIn("PRIVATE-SYNTHETIC-STDERR", str(caught.exception))
            for signature, code in (("CreateRestrictedToken failed: 87", "runtime_restricted_token_unavailable"),
                                    ("CreateDesktopW failed: 5", "runtime_desktop_unavailable")):
                with self.subTest(signature=signature), \
                        patch.dict(os.environ, CODEX_HOME=str(homes[0]), CODEX_THREAD_ID="session-a"), \
                        patch("scripts.mei._repository", return_value=fixture.repo), \
                        patch("scripts.mei.subprocess.run", return_value=SimpleNamespace(
                            returncode=1, stdout="",
                            stderr="windows sandbox failed: " + signature + " PRIVATE-SYNTHETIC-STDERR")) as run:
                    with self.assertRaises(ContinuityError) as caught:
                        save_enrolled(fixture.mei, fixture.repo, "shared", directory / "snapshot.json",
                                      directory / "project.json", writer_session_id="gui1-session-a")
                    self.assertIn(code + "; stage=native_initialization", str(caught.exception))
                    self.assertNotIn("PRIVATE-SYNTHETIC-STDERR", str(caught.exception))
                    self.assertEqual(sum(call.args[0][0] == str(native) for call in run.call_args_list), 1)
            cases = [
                ({"status": "handoff_conflict"}, "writer_conflict"),
                ({"status": "handoff_needs_reconcile"}, "writer_needs_reconcile"),
                ({"status": "error", "error_code": "invalid_json",
                  "failure_stage": "read_project"}, "writer_invalid_json; stage=read_project"),
                ({"status": "error", "error_code": "permission_denied",
                  "failure_stage": "read_snapshot"}, "writer_permission_denied; stage=read_snapshot"),
                ({"status": "error", "error_code": "PRIVATE-SYNTHETIC",
                  "failure_stage": "PRIVATE-SYNTHETIC"}, "writer_rejected_unclassified"),
                (["PRIVATE-SYNTHETIC"], "native_child_failed"),
            ]
            for payload, reason in cases:
                if isinstance(payload, dict):
                    payload["error"] = "PRIVATE-SYNTHETIC-ERROR"
                with self.subTest(reason=reason), \
                        patch.dict(os.environ, CODEX_HOME=str(homes[0]), CODEX_THREAD_ID="session-a"), \
                        patch("scripts.mei._repository", return_value=fixture.repo), \
                        patch("scripts.mei.subprocess.run", return_value=SimpleNamespace(
                            returncode=1, stdout=json.dumps(payload), stderr="PRIVATE-SYNTHETIC-STDERR")):
                    with self.assertRaises(ContinuityError) as caught:
                        save_enrolled(fixture.mei, fixture.repo, "shared", directory / "snapshot.json",
                                      directory / "project.json", writer_session_id="gui1-session-a")
                    self.assertIn(reason, str(caught.exception))
                    self.assertNotIn("PRIVATE-SYNTHETIC", str(caught.exception))
            for raw in ('{"PRIVATE-SYNTHETIC": 1, "PRIVATE-SYNTHETIC": 2}', 'x' * 65_537, '{}',
                        json.dumps(dict(project, stable_refs=[{"kind": "file",
                            "value": str(directory / "PRIVATE-SYNTHETIC.md")}]))):
                (directory / "project.json").write_text(raw, encoding="utf-8")
                with self.subTest(input_size=len(raw)), \
                        patch.dict(os.environ, CODEX_HOME=str(homes[0]), CODEX_THREAD_ID="session-a"), \
                        patch("scripts.mei._repository", return_value=fixture.repo), \
                        patch("scripts.mei._attachment", return_value={"context_id": "shared",
                              "writer": {"host": "gui1", "session_id": "session-a"},
                              "permission_profile": "mei-context-" + fixture.repo_id[5:]}), \
                        patch("scripts.mei.subprocess.run") as run:
                    with self.assertRaisesRegex(ContinuityError, "input unavailable or invalid") as caught:
                        save_enrolled(fixture.mei, fixture.repo, "shared", directory / "snapshot.json",
                                      directory / "project.json", writer_session_id="gui1-session-a")
                    self.assertNotIn("PRIVATE-SYNTHETIC", str(caught.exception))
                    run.assert_not_called()

    def test_handoff_cli_classifies_input_failure_without_running_writer(self):
        import io
        from contextlib import redirect_stdout
        from continuity_handoff import _cli

        with fixture_root() as directory:
            snapshot, project = directory / "snapshot.json", directory / "project.json"
            args = ["continuity_handoff.py", "--repo-root", str(directory),
                    "--context-id", "shared", "--writer-session-id", "session-a",
                    "--snapshot", str(snapshot), "--project", str(project)]
            snapshot.write_text("{}", encoding="utf-8")
            project.write_text("{", encoding="utf-8")
            output = io.StringIO()
            with patch("sys.argv", args), redirect_stdout(output), \
                    patch("continuity_handoff.save_handoff") as writer:
                self.assertEqual(_cli(), 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result["error_code"], "invalid_json")
            self.assertEqual(result["failure_stage"], "read_project")
            writer.assert_not_called()
            for raw in (b"{", b"[]", b"{}", b"x" * 1_048_577):
                output = io.StringIO()
                with self.subTest(size=len(raw)), patch("sys.argv", args[:args.index("--snapshot")] + ["--input-stdin"]), \
                        patch("sys.stdin", SimpleNamespace(buffer=io.BytesIO(raw))), \
                        redirect_stdout(output), patch("continuity_handoff.save_handoff") as writer:
                    self.assertEqual(_cli(), 1)
                result = json.loads(output.getvalue())
                self.assertEqual(result["failure_stage"], "read_stdin")
                writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
