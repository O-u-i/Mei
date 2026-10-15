"""Creation never reuses an identity or invalidates an existing semantic pair."""

import hashlib
import json
import unittest

from tests.test_continuity import ContinuityFixture, fixture_root, fixture_project
from scripts.create_continuity import create_context
from check_continuity import ContinuityError, load_context


class CreationTests(unittest.TestCase):
    def test_creation_rebases_only_technical_index_fields_and_keeps_backup(self):
        with fixture_root() as root:
            fixture = ContinuityFixture(root)
            before = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            project_bytes = (fixture.context_dir / "project.json").read_bytes()
            result = create_context(fixture.repo, "next-task", fixture_project(fixture.repo_id, "next-task"),
                                    mei_root=fixture.mei, expected_index_sha256=before["index_sha256"],
                                    writer_session_id="explicit-maintainer", rebase_index=True)
            self.assertEqual(result["status"], "clean")
            after = load_context(fixture.repo, fixture.context_id, mei_root=fixture.mei)
            self.assertEqual(after["status"], "clean")
            self.assertEqual((fixture.context_dir / "project.json").read_bytes(), project_bytes)
            for key in before["checkpointer"]:
                if not key.startswith("index_"):
                    self.assertEqual(after["checkpointer"][key], before["checkpointer"][key])
            backup = json.loads(__import__("pathlib").Path(result["backup"]).read_text(encoding="utf-8"))
            self.assertEqual(hashlib.sha256(backup["previous_index"].encode()).hexdigest(), before["index_sha256"])

    def test_stale_or_unapproved_creation_does_not_change_the_index(self):
        with fixture_root() as root:
            fixture = ContinuityFixture(root)
            path = fixture.mei / "registry/continuity.yaml"
            before = path.read_bytes()
            for options in ({"expected_index_sha256": "0" * 64, "rebase_index": True},
                            {"expected_index_sha256": hashlib.sha256(before).hexdigest()}):
                with self.assertRaises(ContinuityError):
                    create_context(fixture.repo, "next-task", fixture_project(fixture.repo_id, "next-task"),
                                   mei_root=fixture.mei, writer_session_id="maintainer", **options)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((fixture.context_dir.parent / "next-task").exists())
