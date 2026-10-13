"""Synthetic public-interface checks for the narrow continuity writer."""

from __future__ import annotations

import sys
import unittest
import uuid
import json
import subprocess
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_continuity import load_context  # noqa: E402
from tests.test_continuity import ContinuityFixture, write_json  # noqa: E402


@contextmanager
def writer_fixture_root():
    """Keep isolated synthetic fixtures as ignored verification evidence."""
    root = ROOT / ".tmp" / "checkpoint-tests" / uuid.uuid4().hex
    root.mkdir(parents=True)
    yield root


def checkpoint(fixture: ContinuityFixture, state: dict, project: dict, **options) -> dict:
    from checkpoint_continuity import checkpoint_context

    arguments = {
        "mei_root": fixture.mei,
        "base_generation": state["checkpointer"]["generation"],
        "base_project_sha256": state["project_sha256"],
        "base_index_sha256": state["index_sha256"],
        "writer_session_id": "writer-one",
    }
    arguments.update(options)
    return checkpoint_context(fixture.repo, fixture.context_id, project, **arguments)


def cli_arguments(fixture: ContinuityFixture, state: dict, project_path: Path) -> list[str]:
    return [
        "--repo-root", str(fixture.repo),
        "--context-id", fixture.context_id,
        "--mei-root", str(fixture.mei),
        "--base-generation", str(state["checkpointer"]["generation"]),
        "--base-project-sha256", state["project_sha256"],
        "--base-index-sha256", state["index_sha256"],
        "--writer-session-id", "cli-writer",
        "--project", str(project_path),
    ]


def run_cli(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / "checkpoint_continuity.py"), *arguments],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )


class CheckpointTests(unittest.TestCase):
    def test_checkpoint_is_readable_at_the_next_generation_and_remains_dirty(self):
        from checkpoint_continuity import checkpoint_context

        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            proposed = dict(initial["project"], next_step="Review the saved checkpoint.")

            result = checkpoint_context(
                fixture.repo,
                fixture.context_id,
                proposed,
                mei_root=fixture.mei,
                base_generation=initial["checkpointer"]["generation"],
                base_project_sha256=initial["project_sha256"],
                base_index_sha256=initial["index_sha256"],
                writer_session_id="writer-one",
            )
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result["status"], "checkpointed")
            self.assertEqual(saved["project"]["next_step"], "Review the saved checkpoint.")
            self.assertEqual(saved["checkpointer"]["generation"], 1)
            self.assertEqual(saved["checkpointer"]["synced_generation"], 0)
            self.assertEqual(saved["status"], "dirty")
            self.assertEqual(result["project_sha256"], saved["project_sha256"])
            self.assertEqual(saved["checkpointer"]["writer_session_id"], "writer-one")

    def test_wrong_project_hash_conflicts_even_at_the_current_generation(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            rejected = checkpoint(fixture, initial, dict(initial["project"], next_step="Wrong base hash."), base_project_sha256="f" * 64)
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(rejected["status"], "conflict")
            self.assertEqual(saved["project_sha256"], initial["project_sha256"])
            self.assertEqual(saved["checkpointer"], initial["checkpointer"])

    def test_wrong_index_snapshot_conflicts_without_mutation(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            rejected = checkpoint(fixture, initial, initial["project"], base_index_sha256="f" * 64)
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(rejected["status"], "conflict")
            self.assertEqual(rejected["conflict"]["expected_index_sha256"], "f" * 64)
            self.assertEqual(rejected["conflict"]["observed_index_sha256"], initial["index_sha256"])
            self.assertEqual(saved["project_sha256"], initial["project_sha256"])
            self.assertEqual(saved["checkpointer"], initial["checkpointer"])

    def test_stale_writer_receives_conflict_and_preserves_the_saved_payload(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            checkpoint(fixture, initial, dict(initial["project"], next_step="Keep the accepted update."))
            attempted = dict(initial["project"], next_step="A stale proposal to review and merge.")

            rejected = checkpoint(fixture, initial, attempted, writer_session_id="stale-writer")
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(rejected["status"], "conflict")
            self.assertEqual(rejected["conflict"]["expected_generation"], 0)
            self.assertEqual(rejected["conflict"]["observed_generation"], 1)
            self.assertEqual(rejected["conflict"]["required_action"], "reread")
            self.assertEqual(rejected["attempted_project"], attempted)
            self.assertEqual(saved["project"]["next_step"], "Keep the accepted update.")
            self.assertEqual(saved["checkpointer"]["generation"], 1)


    def test_inconsistent_pair_is_preserved_until_explicit_recovery(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            interrupted = dict(fixture.project, next_step="Review the interrupted replacement.")
            write_json(fixture.context_dir / "project.json", interrupted)
            observed = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            result = checkpoint(fixture, observed, dict(interrupted, next_step="An unapproved repair."))
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result["status"], "needs_reconcile")
            self.assertEqual(result["required_action"], "explicit_reconcile")
            self.assertEqual(saved["project"], interrupted)
            self.assertEqual(saved["checkpointer"], observed["checkpointer"])


    def test_explicit_recovery_saves_the_reviewed_payload_as_dirty(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            write_json(fixture.context_dir / "project.json", dict(fixture.project, next_step="Interrupted state."))
            observed = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            reviewed = dict(observed["project"], next_step="A reviewed recovery decision.")

            recovered = checkpoint(fixture, observed, reviewed, reconcile=True)
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(recovered["status"], "checkpointed")
            self.assertEqual(saved["project"], reviewed)
            self.assertEqual(saved["checkpointer"]["generation"], 1)
            self.assertEqual(saved["checkpointer"]["last_event"], "reconcile-checkpoint")
            self.assertEqual(saved["checkpointer"]["synced_generation"], 0)
            self.assertEqual(saved["status"], "dirty")
            self.assertFalse(saved["checkpointer"]["needs_reconcile"])


    def test_loader_exposes_actual_index_sizes_and_pair_reconcile_flag(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index_path.write_bytes(index_path.read_bytes() + b"# reviewed index change\n")

            observed = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertTrue(observed["needs_reconcile"])
            self.assertEqual(observed["index_bytes"], len(index_path.read_bytes()))
            self.assertEqual(observed["index_lines"], len(index_path.read_bytes().splitlines()))


    def test_cli_checkpoint_returns_a_receipt_and_can_be_reloaded(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            project_path = root / "proposed.json"
            write_json(project_path, dict(initial["project"], next_step="Continue from the CLI checkpoint."))

            process = run_cli(cli_arguments(fixture, initial, project_path))
            self.assertEqual(process.returncode, 0, process.stderr)
            receipt = json.loads(process.stdout)
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(receipt["status"], "checkpointed")
            self.assertEqual(saved["project"]["next_step"], "Continue from the CLI checkpoint.")
            self.assertEqual(receipt["generation"], 1)


    def test_two_independent_writers_serialize_and_the_loser_gets_conflict(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            proposal = root / "first.json"
            write_json(proposal, dict(initial["project"], next_step="The first writer owns this generation."))
            arguments = cli_arguments(fixture, initial, proposal)
            code = """
import json, os, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from checkpoint_continuity import main
real_replace = os.replace
def pause_replace(source, destination):
    if str(destination).endswith('project.json'):
        print('READY', flush=True)
        if sys.stdin.readline().strip() != 'go':
            raise RuntimeError('Missing test release')
    return real_replace(source, destination)
with patch('os.replace', pause_replace):
    raise SystemExit(main(json.loads(sys.argv[2])))
"""
            with subprocess.Popen(
                [sys.executable, "-B", "-c", code, str(ROOT / "scripts"), json.dumps(arguments)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            ) as first:
                try:
                    self.assertEqual(first.stdout.readline().strip(), "READY")
                    second_proposal = root / "second.json"
                    write_json(second_proposal, dict(initial["project"], next_step="The competing stale proposal."))
                    competitor = run_cli(cli_arguments(fixture, initial, second_proposal) + ["--lock-timeout", "0.15"])
                    self.assertEqual(competitor.returncode, 4, competitor.stdout + competitor.stderr)
                    self.assertEqual(json.loads(competitor.stdout)["status"], "lock_timeout")
                    first.stdin.write("go\n")
                    first.stdin.flush()
                    stdout, stderr = first.communicate(timeout=10)
                    self.assertEqual(first.returncode, 0, stderr)
                    self.assertEqual(json.loads(stdout)["generation"], 1)

                    stale = run_cli(cli_arguments(fixture, initial, second_proposal))
                    self.assertEqual(stale.returncode, 2, stale.stdout + stale.stderr)
                    saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
                    self.assertEqual(saved["project"]["next_step"], "The first writer owns this generation.")
                finally:
                    if first.poll() is None:
                        first.kill()
                        first.communicate(timeout=5)


    def test_second_replace_failure_is_reported_as_recovery_debt_by_the_cli(self):
        from checkpoint_continuity import main
        from contextlib import redirect_stdout
        from io import StringIO

        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            proposal_path = root / "proposed.json"
            proposed = dict(initial["project"], next_step="Review the interrupted I/O operation.")
            write_json(proposal_path, proposed)
            real_replace = os.replace

            def fail_second_replace(source, destination):
                if Path(destination).name == "checkpointer.json":
                    raise OSError("Synthetic replacement failure")
                return real_replace(source, destination)

            output = StringIO()
            with patch("os.replace", fail_second_replace), redirect_stdout(output):
                exit_code = main(cli_arguments(fixture, initial, proposal_path))
            observed = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(exit_code, 3, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["status"], "needs_reconcile")
            self.assertEqual(observed["project"], proposed)
            self.assertEqual(observed["checkpointer"], initial["checkpointer"])
            self.assertEqual(observed["status"], "needs_reconcile")
            recovered = checkpoint(fixture, observed, dict(proposed, next_step="Explicitly reviewed recovery."), reconcile=True)
            self.assertEqual(recovered["status"], "checkpointed")


    def test_process_death_between_replacements_releases_lock_and_requires_recovery(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            proposal = root / "interrupted.json"
            proposed = dict(initial["project"], next_step="Preserve the interrupted process payload.")
            write_json(proposal, proposed)
            code = """
import json, os, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from checkpoint_continuity import main
real_replace = os.replace
def crash_replace(source, destination):
    if Path(destination).name == 'checkpointer.json':
        os._exit(73)
    return real_replace(source, destination)
with patch('os.replace', crash_replace):
    main(json.loads(sys.argv[2]))
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", code, str(ROOT / "scripts"), json.dumps(cli_arguments(fixture, initial, proposal))],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(crashed.returncode, 73, crashed.stderr)
            observed = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            self.assertEqual(observed["status"], "needs_reconcile")
            self.assertEqual(observed["project"], proposed)
            self.assertEqual(observed["checkpointer"], initial["checkpointer"])

            retry = run_cli(cli_arguments(fixture, observed, proposal) + ["--lock-timeout", "0"])
            self.assertEqual(retry.returncode, 3, retry.stdout + retry.stderr)
            recovered = run_cli(cli_arguments(fixture, observed, proposal) + ["--reconcile", "--lock-timeout", "0"])
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            self.assertEqual(saved["checkpointer"]["generation"], 1)
            self.assertEqual(saved["status"], "dirty")


    def test_checkpoint_rejects_invalid_cas_inputs_before_returning_a_conflict(self):
        from check_continuity import ContinuityError

        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            for options in (
                {"base_generation": -1},
                {"base_generation": True},
                {"base_generation": "0"},
                {"base_project_sha256": "invalid"},
                {"base_index_sha256": None},
                {"writer_session_id": ""},
                {"writer_session_id": "x" * 129},
                {"writer_session_id": "api_key=synthetic-private-value"},
                {"reconcile": "yes"},
                {"lock_timeout": -1},
                {"lock_timeout": float("nan")},
                {"lock_timeout": float("inf")},
                {"lock_timeout": 61},
            ):
                with self.subTest(options=options), self.assertRaises(ContinuityError):
                    checkpoint(fixture, initial, initial["project"], **options)
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            self.assertEqual(saved["project_sha256"], initial["project_sha256"])
            self.assertEqual(saved["checkpointer"], initial["checkpointer"])


    def test_checkpoint_changes_only_the_selected_continuity_pair(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            other_context = fixture.mei / "memory" / "continuity" / fixture.repo_id / "other"
            other_context.mkdir(parents=True)
            protected = (
                fixture.mei / "memory" / "canonical" / "protected.json",
                fixture.mei / "memory" / "candidates" / "protected.json",
                fixture.mei / "trajectories" / "inbox" / "protected.json",
                fixture.mei / "local" / "paths.toml",
                fixture.mei / "registry" / "memory.yaml",
            )
            for path in protected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic unrelated bytes\n")
            other_pair = (other_context / "project.json", other_context / "checkpointer.json")
            for path in other_pair:
                path.write_bytes(b"other selected context remains untouched\n")
            before = {path: path.read_bytes() for path in (*protected, *other_pair)}
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            checkpoint(fixture, initial, dict(initial["project"], next_step="Only this context changes."))

            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(sorted(p.name for p in fixture.context_dir.iterdir()), ["checkpointer.json", "project.json"])

    def test_path_escape_and_identity_change_are_rejected_before_mutation(self):
        from check_continuity import ContinuityError

        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            escaped = dict(initial["project"], stable_refs=[{"kind": "file", "value": "../../outside.json"}])
            wrong_identity = dict(initial["project"], repo_id="repo-" + "a" * 32)

            for payload in (escaped, wrong_identity):
                with self.assertRaises(ContinuityError):
                    checkpoint(fixture, initial, payload)

            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            self.assertEqual(saved["project_sha256"], initial["project_sha256"])
            self.assertEqual(saved["checkpointer"], initial["checkpointer"])

    def test_cli_malformed_project_returns_invalid_without_mutation(self):
        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            initial = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")

            result = run_cli(cli_arguments(fixture, initial, malformed))
            saved = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "invalid")
            self.assertIn("invalid JSON", json.loads(result.stdout)["error"])
            self.assertEqual(saved["project_sha256"], initial["project_sha256"])
            self.assertEqual(saved["checkpointer"], initial["checkpointer"])

    def test_previous_same_major_continuity_index_remains_readable_after_release_bump(self):
        from check_continuity import ContinuityError

        with writer_fixture_root() as root:
            fixture = ContinuityFixture(root)
            (fixture.mei / "VERSION").write_text("v1.2.0\n", encoding="utf-8")

            try:
                result = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            except ContinuityError as exc:
                self.fail(f"existing v1.1.0 continuity index was not read-compatible: {exc}")

            self.assertEqual(result["index"]["version"], "v1.1.0")


if __name__ == "__main__":
    unittest.main()
