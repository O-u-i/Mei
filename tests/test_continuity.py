"""Synthetic behavior tests for the shared read-only continuity loader."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))

from check_continuity import ContinuityError, load_context, repo_id_for  # noqa: E402


def write_json(path: Path, value: dict) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    data = payload.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def fixture_project(repo_id: str, context_id: str = "shared") -> dict:
    return {
        "schema_version": "v0.1.0",
        "repo_id": repo_id,
        "context_id": context_id,
        "status": "active",
        "goal": "Read one shared continuity context.",
        "decisions": ["Use one read adapter for both frontends."],
        "constraints": ["Keep continuity outside canonical memory."],
        "blockers": [],
        "next_step": "Run the selected validation checks.",
        "stable_refs": [],
        "updated_at": "2026-10-13T00:00:00Z",
    }


class ContinuityFixture:
    def __init__(self, root: Path, *, context_id: str = "shared") -> None:
        self.root = root
        self.mei = root / "mei"
        self.repo = root / "repo"
        self.mei.mkdir()
        self.repo.mkdir()
        (self.mei / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True)
        self.repo_id = repo_id_for(self.repo)
        self.context_id = context_id
        self.context_dir = self.mei / "memory" / "continuity" / self.repo_id / context_id
        self.context_dir.mkdir(parents=True)
        self.project = fixture_project(self.repo_id, context_id)
        project_bytes = write_json(self.context_dir / "project.json", self.project)
        index = {
            "schema_version": "v0.1.0",
            "version": "v1.1.0",
            "entries": [
                {
                    "repo_id": self.repo_id,
                    "context_id": context_id,
                    "project_path": f"memory/continuity/{self.repo_id}/{context_id}/project.json",
                    "checkpointer_path": f"memory/continuity/{self.repo_id}/{context_id}/checkpointer.json",
                    "status": "active",
                }
            ],
        }
        index_bytes = yaml.safe_dump(index, allow_unicode=True, sort_keys=False).encode("utf-8")
        index_path = self.mei / "registry" / "continuity.yaml"
        index_path.parent.mkdir(parents=True)
        index_path.write_bytes(index_bytes)
        checkpointer = {
            "schema_version": "v0.1.0",
            "repo_id": self.repo_id,
            "context_id": context_id,
            "writer_session_id": "fixture",
            "generation": 0,
            "synced_generation": 0,
            "dirty": False,
            "needs_reconcile": False,
            "last_event": "fixture-created",
            "updated_at": "2026-10-13T00:00:00Z",
            "project_sha256": hashlib.sha256(project_bytes).hexdigest(),
            "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
            "index_lines": len(index_bytes.splitlines()),
            "index_bytes": len(index_bytes),
            "last_conflict": None,
        }
        write_json(self.context_dir / "checkpointer.json", checkpointer)


@contextmanager
def fixture_root():
    root = ROOT / ".tmp" / "pytest" / f"continuity-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ContinuityTests(unittest.TestCase):
    def test_two_frontends_read_one_context_without_writing(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            tracked = [
                fixture.mei / "registry" / "memory.yaml",
                fixture.mei / "registry" / "skills.yaml",
                fixture.mei / "local" / "paths.toml",
                fixture.mei / "memory" / "canonical" / "example.json",
                fixture.mei / "memory" / "candidates" / "example.json",
            ]
            for path in tracked:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Synthetic unrelated record; preserve unchanged.\n", encoding="utf-8")
            before = {path: path.read_bytes() for path in tracked}
            state_files = [fixture.mei / "registry" / "continuity.yaml", fixture.context_dir / "project.json", fixture.context_dir / "checkpointer.json"]
            state_before = {path: path.read_bytes() for path in state_files}

            gui = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei, host="gui")
            tui = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei, host="tui")

            self.assertEqual(gui["status"], "clean")
            self.assertEqual(gui["repo_id"], tui["repo_id"])
            self.assertEqual(gui["context_id"], tui["context_id"])
            self.assertEqual(gui["project_sha256"], tui["project_sha256"])
            self.assertEqual(gui["project"]["next_step"], "Run the selected validation checks.")
            self.assertEqual({path: path.read_bytes() for path in state_files}, state_before)
            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_repo_identity_normalizes_remote_credentials_and_transport(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            authenticated = repo_id_for(fixture.repo, "https://alice:token@github.com/Owner/Project.git")
            ssh = repo_id_for(fixture.repo, "git@github.com:owner/project.git")
            https = repo_id_for(fixture.repo, "https://github.com/OWNER/PROJECT")
            self.assertEqual(authenticated, ssh)
            self.assertEqual(ssh, https)

    def test_repo_identity_normalizes_windows_case_and_slash_aliases(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            case_alias = Path(str(fixture.repo).upper())
            slash_alias = Path(str(fixture.repo).replace("\\", "/"))

            self.assertEqual(repo_id_for(fixture.repo), repo_id_for(case_alias))
            self.assertEqual(repo_id_for(fixture.repo), repo_id_for(slash_alias))

    def test_separate_worktrees_have_distinct_repo_identity(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(fixture.repo),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "--allow-empty",
                    "-m",
                    "fixture",
                ],
                check=True,
                capture_output=True,
            )
            worktree = temp / "worktree"
            subprocess.run(
                ["git", "-C", str(fixture.repo), "worktree", "add", "--detach", str(worktree)],
                check=True,
                capture_output=True,
            )

            self.assertNotEqual(repo_id_for(fixture.repo), repo_id_for(worktree))

    def test_project_hash_mismatch_is_reconcile_required_and_read_only(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            project_path = fixture.context_dir / "project.json"
            project = dict(fixture.project, next_step="A changed step requiring reconciliation.")
            write_json(project_path, project)
            before = project_path.read_bytes()

            result = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result["status"], "needs_reconcile")
            self.assertIn("project hash", " ".join(result["diagnostics"]))
            self.assertEqual(project_path.read_bytes(), before)

    def test_wrong_repository_cannot_select_another_context(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            other_repo = temp / "other-repo"
            other_repo.mkdir()
            subprocess.run(["git", "init", "--quiet", str(other_repo)], check=True)
            with self.assertRaisesRegex(ContinuityError, "not present"):
                load_context(other_repo, fixture.context_id, mei_root=fixture.mei)

    def test_missing_selected_context_record_is_rejected(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            (fixture.context_dir / "project.json").unlink()

            with self.assertRaisesRegex(ContinuityError, "missing"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_missing_index_and_checkpointer_are_rejected(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index_path.unlink()
            with self.assertRaisesRegex(ContinuityError, "index missing"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

        with fixture_root() as second_temp:
            fixture = ContinuityFixture(second_temp)
            (fixture.context_dir / "checkpointer.json").unlink()
            with self.assertRaisesRegex(ContinuityError, "missing"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_dirty_and_index_hash_mismatch_statuses_are_reported(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            checkpointer_path = fixture.context_dir / "checkpointer.json"
            checkpointer = json.loads(checkpointer_path.read_text(encoding="utf-8"))
            checkpointer["dirty"] = True
            write_json(checkpointer_path, checkpointer)

            result = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            self.assertEqual(result["status"], "dirty")

            index_path = fixture.mei / "registry" / "continuity.yaml"
            index_path.write_bytes(index_path.read_bytes() + b"# changed\n")
            before = {path: path.read_bytes() for path in (index_path, checkpointer_path)}
            result = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            self.assertEqual(result["status"], "needs_reconcile")
            self.assertIn("index hash", " ".join(result["diagnostics"]))
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_mei_root_symlink_is_rejected_before_resolution(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            alias = temp / "mei-alias"
            try:
                os.symlink(fixture.mei, alias, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink creation is unavailable on this Windows host: {error}")
            with self.assertRaisesRegex(ContinuityError, "symlink"):
                load_context(fixture.repo, fixture.context_id, mei_root=alias)

    def test_windows_junctions_are_rejected_from_filesystem_attributes(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            original_lstat = Path.lstat

            def windows_lstat(path: Path, *args, **kwargs):
                metadata = original_lstat(path, *args, **kwargs)
                if path == junction:
                    return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400)
                return metadata

            for junction in (fixture.mei, fixture.context_dir):
                with self.subTest(junction=junction):
                    with patch.object(Path, "lstat", windows_lstat):
                        self.assertFalse(junction.is_symlink())
                        with self.assertRaisesRegex(ContinuityError, "junction"):
                            load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_mei_root_reparse_component_is_checked_before_resolution(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            alias = temp / "mei-alias"
            original_resolve = Path.resolve
            original_lstat = Path.lstat

            def resolve_alias(path: Path, strict: bool = False) -> Path:
                if path == alias:
                    return fixture.mei
                return original_resolve(path, strict=strict)

            def windows_lstat(path: Path, *args, **kwargs):
                if path == alias:
                    metadata = original_lstat(fixture.mei)
                    return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400)
                return original_lstat(path, *args, **kwargs)

            with patch.object(Path, "resolve", resolve_alias):
                with patch.object(Path, "lstat", windows_lstat):
                    with self.assertRaisesRegex(ContinuityError, "symlink"):
                        load_context(fixture.repo, fixture.context_id, mei_root=alias)

    def test_malformed_unknown_and_identity_records_are_rejected(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            project_path = fixture.context_dir / "project.json"
            project_path.write_text('{"schema_version":"v9.9.9"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ContinuityError, "schema"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            project_path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ContinuityError, "JSON"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            write_json(project_path, fixture.project)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
            index["entries"][0]["context_id"] = "other"
            index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ContinuityError, "deterministic|context"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_checkpointer_schema_identity_and_privacy_are_enforced(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            checkpointer_path = fixture.context_dir / "checkpointer.json"
            original = json.loads(checkpointer_path.read_text(encoding="utf-8"))

            write_json(checkpointer_path, dict(original, schema_version="v9.9.9"))
            with self.assertRaisesRegex(ContinuityError, "schema"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            write_json(checkpointer_path, dict(original, context_id="other"))
            with self.assertRaisesRegex(ContinuityError, "context_id"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            write_json(checkpointer_path, dict(original, last_event="api_key=example-test-value"))
            with self.assertRaisesRegex(ContinuityError, "possible secret"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            write_json(checkpointer_path, dict(original, raw_prompt="private"))
            with self.assertRaisesRegex(ContinuityError, "schema"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_conflict_state_takes_precedence_over_reconcile_and_dirty(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            checkpointer_path = fixture.context_dir / "checkpointer.json"
            checkpointer = json.loads(checkpointer_path.read_text(encoding="utf-8"))
            checkpointer.update(
                dirty=True,
                needs_reconcile=True,
                last_conflict={
                    "expected_generation": 1,
                    "observed_generation": 2,
                    "required_action": "reconcile",
                },
            )
            write_json(checkpointer_path, checkpointer)

            result = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result["status"], "conflict")
            self.assertIn("checkpointer requires reconciliation", result["diagnostics"])

    def test_duplicate_yaml_and_json_keys_are_rejected(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            original_index = index_path.read_bytes()
            index_path.write_bytes(original_index + b"version: v1.1.0\n")
            with self.assertRaisesRegex(ContinuityError, "duplicate key"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            index_path.write_bytes(original_index)
            checkpointer_path = fixture.context_dir / "checkpointer.json"
            original_checkpointer = checkpointer_path.read_bytes()
            duplicate_json = original_checkpointer.rstrip()[:-1] + b', "generation": 0}\n'
            checkpointer_path.write_bytes(duplicate_json)
            with self.assertRaisesRegex(ContinuityError, "duplicate key"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_yaml_collection_keys_are_rejected_as_invalid_input(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index_path.write_text("? [a, b]\n: value\n", encoding="utf-8")

            with self.assertRaisesRegex(ContinuityError, "YAML"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_possible_secret_is_rejected_before_hash_reconciliation(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            write_json(fixture.context_dir / "project.json", dict(fixture.project, goal="api_key=example-test-value"))
            with self.assertRaisesRegex(ContinuityError, "possible secret"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_path_escape_is_rejected_before_reading_outside_root(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
            index["entries"][0]["project_path"] = "memory/continuity/../../outside.json"
            index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ContinuityError, "path"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_absolute_index_locators_are_rejected(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
            index["entries"][0]["project_path"] = str(temp / "outside.json")
            index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")

            with self.assertRaisesRegex(ContinuityError, "deterministic|absolute"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_every_index_entry_must_use_its_deterministic_path(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
            other_repo = "repo-" + "a" * 32
            index["entries"].append(
                {
                    "repo_id": other_repo,
                    "context_id": "other",
                    "project_path": "memory/continuity/not-the-entry/project.json",
                    "checkpointer_path": f"memory/continuity/{other_repo}/other/checkpointer.json",
                    "status": "active",
                }
            )
            index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ContinuityError, "deterministic"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_soft_caps_are_reported_without_writing_state(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index_bytes = index_path.read_bytes() + (b"# soft-cap\n" * 1600)
            index_path.write_bytes(index_bytes)
            checkpointer_path = fixture.context_dir / "checkpointer.json"
            checkpointer = json.loads(checkpointer_path.read_text(encoding="utf-8"))
            checkpointer["index_sha256"] = hashlib.sha256(index_bytes).hexdigest()
            checkpointer["index_lines"] = len(index_bytes.splitlines())
            checkpointer["index_bytes"] = len(index_bytes)
            write_json(checkpointer_path, checkpointer)
            before = {path: path.read_bytes() for path in (index_path, checkpointer_path)}

            result = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertTrue(any("index lines" in warning for warning in result["warnings"]))
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_index_hard_size_cap_is_enforced(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            index_path = fixture.mei / "registry" / "continuity.yaml"
            index_path.write_bytes(b" " * (262_144 + 1))
            with self.assertRaisesRegex(ContinuityError, "hard cap"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_symlinked_continuity_component_is_rejected(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            real_context = fixture.context_dir.with_name(f"{fixture.context_id}-real")
            fixture.context_dir.rename(real_context)
            try:
                os.symlink(real_context, fixture.context_dir, target_is_directory=True)
            except (OSError, NotImplementedError) as symlink_error:
                result = subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(fixture.context_dir), str(real_context)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode:
                    detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
                    self.skipTest(
                        f"symlink and junction creation are unavailable on this Windows host: "
                        f"os.symlink: {symlink_error}; mklink /J: {detail}"
                    )
            with self.assertRaisesRegex(ContinuityError, "symlink"):
                load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

    def test_stale_stable_reference_is_reported_without_rewriting_state(self):
        with fixture_root() as temp:
            fixture = ContinuityFixture(temp)
            project_path = fixture.context_dir / "project.json"
            project = dict(fixture.project, stable_refs=[{"kind": "file", "value": "missing.md"}])
            project_bytes = write_json(project_path, project)
            checkpointer_path = fixture.context_dir / "checkpointer.json"
            checkpointer = json.loads(checkpointer_path.read_text(encoding="utf-8"))
            checkpointer["project_sha256"] = hashlib.sha256(project_bytes).hexdigest()
            write_json(checkpointer_path, checkpointer)

            result = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)

            self.assertEqual(result["status"], "stale")
            self.assertTrue(result["stable_refs"][0]["stale"])

    def test_continuity_schemas_are_valid_json_schemas(self):
        for path in sorted((ROOT / "schemas").glob("continuity-*.schema.json")):
            with self.subTest(path=path.name):
                Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
