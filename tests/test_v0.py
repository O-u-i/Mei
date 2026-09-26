"""Focused checks for the V0 record and provenance contract."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _common import assert_unique_ids, check_private_content, json_text, read_json, validate_schema  # noqa: E402
from normalize_trajectory import normalization_payload  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
