"""Focused checks for the V0 record and provenance contract."""

import json
import sys
import tempfile
import unittest
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _common import assert_unique_ids, check_private_content, json_text, read_json, validate_schema  # noqa: E402
from normalize_trajectory import normalization_payload  # noqa: E402
from archive_trajectory import archive  # noqa: E402
from validate_repository import validate_repository  # noqa: E402


class V0ContractTests(unittest.TestCase):
    def setUp(self):
        self.trajectory = read_json(ROOT / "evals" / "cases" / "synthetic-task.json")

    def test_synthetic_case_validates_and_preserves_source_identity(self):
        validate_schema(self.trajectory, "trajectory.schema.json")
        output = normalization_payload(self.trajectory, "synthetic-task.json", "a" * 64)
        self.assertEqual(output["source_path"], "trajectories/inbox/synthetic-task.json")
        self.assertEqual(output["source_sha256"], "a" * 64)
        self.assertEqual(json_text(output), json_text(json.loads(json_text(output))))

    def test_missing_result_and_invalid_timestamp_are_rejected(self):
        without_result = dict(self.trajectory)
        del without_result["result"]
        with self.assertRaises(ValueError):
            validate_schema(without_result, "trajectory.schema.json")
        bad_date = dict(self.trajectory, completed_at="yesterday")
        with self.assertRaises(ValueError):
            validate_schema(bad_date, "trajectory.schema.json")

    def test_private_token_and_duplicate_id_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "secret"):
            check_private_content(dict(self.trajectory, goal="api_key=example-private-value"))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            assert_unique_ids([{"id": "same"}, {"id": "same"}], "memory")

    def test_repository_index_and_skill_pointers_are_consistent(self):
        validate_repository(ROOT)

    def test_finalized_trajectory_is_archived_with_provenance_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "schemas").mkdir()
            shutil.copyfile(ROOT / "schemas" / "trajectory.schema.json", root / "schemas" / "trajectory.schema.json")
            inbox = root / "trajectories" / "inbox"
            inbox.mkdir(parents=True)
            (root / "trajectories" / "archive").mkdir()
            (root / "trajectories" / "normalized").mkdir()
            canonical = root / "memory" / "canonical" / "procedural"
            canonical.mkdir(parents=True)
            (root / "memory" / "candidates").mkdir()
            (root / "memory" / "archive").mkdir()
            source = inbox / "synthetic-task.json"
            source.write_text(json_text(self.trajectory), encoding="utf-8")
            import hashlib
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            normalized = root / "trajectories" / "normalized" / source.name
            normalized.write_text(json_text(normalization_payload(self.trajectory, source.name, digest)), encoding="utf-8")
            record = canonical / "example.json"
            record.write_text(json_text({"provenance": [{"source": "trajectories/inbox/synthetic-task.json"}]}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "cited by canonical"):
                archive(source, "rejected", "Evidence does not support claim", root)
            changed = archive(source, "promoted", "Evidence verified", root)
            self.assertFalse(source.exists())
            self.assertTrue((root / "trajectories" / "archive" / source.name).is_file())
            self.assertEqual(read_json(normalized)["source_path"], "trajectories/archive/synthetic-task.json")
            self.assertEqual(read_json(record)["provenance"][0]["source"], "trajectories/archive/synthetic-task.json")
            self.assertEqual(read_json(root / "local" / "trajectory-reviews" / source.name)["decision"], "promoted")
            self.assertIn(record, changed)

    def test_rejected_trajectory_leaves_inbox_with_review_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "schemas").mkdir()
            shutil.copyfile(ROOT / "schemas" / "trajectory.schema.json", root / "schemas" / "trajectory.schema.json")
            inbox = root / "trajectories" / "inbox"
            inbox.mkdir(parents=True)
            (root / "trajectories" / "archive").mkdir()
            (root / "memory" / "canonical").mkdir(parents=True)
            (root / "memory" / "candidates").mkdir()
            (root / "memory" / "archive").mkdir()
            source = inbox / "synthetic-task.json"
            source.write_text(json_text(self.trajectory), encoding="utf-8")

            archive(source, "rejected", "Evidence could not be verified", root)
            self.assertFalse(source.exists())
            self.assertTrue((root / "trajectories" / "archive" / source.name).is_file())
            review = read_json(root / "local" / "trajectory-reviews" / source.name)
            self.assertEqual(review["decision"], "rejected")
            self.assertEqual(review["reason"], "Evidence could not be verified")


if __name__ == "__main__":
    unittest.main()
