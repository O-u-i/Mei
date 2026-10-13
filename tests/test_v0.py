"""Focused checks for the V0 record and provenance contract."""

import json
import sys
import unittest
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _common import assert_unique_ids, check_private_content, json_text, read_json, validate_schema  # noqa: E402
from normalize_trajectory import normalization_payload  # noqa: E402
from archive_trajectory import archive, archive_normalized  # noqa: E402
from init_local import initialize  # noqa: E402
from validate_repository import check_episodic_references, validate_repository  # noqa: E402


@contextmanager
def fixture_root():
    base = ROOT / ".tmp" / "pytest"
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"v0-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
        with fixture_root() as root:
            for name in ("schemas", "registry", ".codex"):
                (root / name).mkdir()
            for source in (ROOT / "schemas").glob("*.schema.json"):
                shutil.copyfile(source, root / "schemas" / source.name)
            for source in (ROOT / "registry").glob("*.example.yaml"):
                shutil.copyfile(source, root / "registry" / source.name)
            for name in ("VERSION", "README.md", ".codex/config.example.toml"):
                shutil.copyfile(ROOT / name, root / name)
            initialize(root, root / "codex")
            current_version = (root / "VERSION").read_text(encoding="utf-8").strip()
            local_paths = root / "local" / "paths.toml"
            old_skills_root = json.dumps(str(root / "codex" / "skills"))
            new_skills_root = json.dumps(str(root / "agents" / "skills"))
            local_paths.write_text(local_paths.read_text(encoding="utf-8").replace(old_skills_root, new_skills_root), encoding="utf-8")
            validate_repository(root)
            skills = root / "registry" / "skills.yaml"
            skill_file = root / "agents" / "skills" / "example" / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text("name: example\n", encoding="utf-8")
            # JSON is also YAML; keep this synthetic registry independent of local skills.
            skill = {"id": "example", "path": str(skill_file), "status": "active", "description": "Synthetic skill"}
            skills.write_text(json_text({"version": current_version, "skills": [skill]}), encoding="utf-8")
            validate_repository(root)
            skills.write_text(json_text({"version": current_version, "skills": [skill, skill]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate skill id"):
                validate_repository(root)
            skills.write_text(json_text({"version": current_version, "skills": [dict(skill, path=str(root / 'missing.md'))]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "active skill missing"):
                validate_repository(root)

    def test_invalid_json_identifies_the_source_file(self):
        with fixture_root() as root:
            path = root / "empty.json"
            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty.json: invalid JSON"):
                read_json(path)

    def test_episodic_reference_must_resolve_inside_memory(self):
        with fixture_root() as root:
            index = root / "memory" / "episodic" / "example-event" / "INDEX.md"
            index.parent.mkdir(parents=True)
            index.write_text("# Example event\n\n[Detail](references/detail.md)\n", encoding="utf-8")
            detail = index.parent / "references" / "detail.md"
            detail.parent.mkdir()
            detail.write_text("# Detail\n", encoding="utf-8")
            check_episodic_references({"id": "example", "references": ["memory/episodic/example-event/INDEX.md"]}, root)
            detail.unlink()
            with self.assertRaisesRegex(ValueError, "missing or unsafe episodic detail"):
                check_episodic_references({"id": "example", "references": ["memory/episodic/example-event/INDEX.md"]}, root)
            with self.assertRaisesRegex(ValueError, "missing or unsafe"):
                check_episodic_references({"id": "example", "references": ["memory/episodic/missing/INDEX.md"]}, root)

    def test_finalized_trajectory_is_archived_with_provenance_and_hash(self):
        with fixture_root() as root:
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
            record.write_text(json_text({"provenance": [
                {"source": "trajectories/inbox/synthetic-task.json"},
                {"source": "trajectories/normalized/synthetic-task.json"},
            ]}), encoding="utf-8")

            archived_normalized = root / "trajectories" / "archive" / "normalized" / source.name
            archived_normalized.parent.mkdir()
            archived_normalized.write_text("Existing evidence\n", encoding="utf-8")
            before = {path: path.read_bytes() for path in (source, normalized, record, archived_normalized)}
            with self.assertRaisesRegex(ValueError, "normalized archive destination already exists"):
                archive(source, "promoted", "Evidence verified", root)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertFalse((root / "local" / "trajectory-reviews" / source.name).exists())
            archived_normalized.unlink()  # One explicitly named synthetic test file.
            with self.assertRaisesRegex(ValueError, "cited by canonical"):
                archive(source, "rejected", "Evidence does not support claim", root)
            changed = archive(source, "promoted", "Evidence verified", root)
            self.assertFalse(source.exists())
            self.assertTrue((root / "trajectories" / "archive" / source.name).is_file())
            archived_normalized = root / "trajectories" / "archive" / "normalized" / source.name
            self.assertFalse(normalized.exists())
            self.assertEqual(read_json(archived_normalized)["source_path"], "trajectories/archive/synthetic-task.json")
            self.assertEqual(read_json(record)["provenance"][0]["source"], "trajectories/archive/synthetic-task.json")
            self.assertEqual(read_json(record)["provenance"][1]["source"], "trajectories/archive/normalized/synthetic-task.json")
            self.assertEqual(read_json(root / "local" / "trajectory-reviews" / source.name)["decision"], "promoted")
            self.assertIn(record, changed)
            self.assertIn(archived_normalized, changed)
            self.assertNotIn(normalized, changed)
            self.assertEqual(len(changed), len(set(changed)))

            # The standalone command still backfills an already reviewed normalized record.
            archived_normalized.rename(normalized)
            data = read_json(record)
            data["provenance"][1]["source"] = "trajectories/normalized/synthetic-task.json"
            record.write_text(json_text(data), encoding="utf-8")
            review = root / "local" / "trajectory-reviews" / source.name
            review_before = review.read_bytes()
            self.assertEqual(archive_normalized(normalized, root), [archived_normalized, record])
            self.assertEqual(review.read_bytes(), review_before)
            self.assertEqual(read_json(record)["provenance"][1]["source"], "trajectories/archive/normalized/synthetic-task.json")

    def test_rejected_trajectory_can_be_archived_without_normalization(self):
        with fixture_root() as root:
            (root / "schemas").mkdir()
            shutil.copyfile(ROOT / "schemas" / "trajectory.schema.json", root / "schemas" / "trajectory.schema.json")
            source = root / "trajectories" / "inbox" / "synthetic-task.json"
            source.parent.mkdir(parents=True)
            source.write_text(json_text(self.trajectory), encoding="utf-8")
            (root / "trajectories" / "archive").mkdir()
            changed = archive(source, "rejected", "Evidence unverified", root)
            self.assertEqual(len(changed), 2)
            self.assertFalse(source.exists())
            self.assertFalse((root / "trajectories" / "archive" / "normalized").exists())

    def test_rejected_trajectory_leaves_inbox_with_review_reason(self):
        with fixture_root() as root:
            (root / "schemas").mkdir()
            shutil.copyfile(ROOT / "schemas" / "trajectory.schema.json", root / "schemas" / "trajectory.schema.json")
            inbox = root / "trajectories" / "inbox"
            inbox.mkdir(parents=True)
            (root / "trajectories" / "archive").mkdir()
            (root / "trajectories" / "normalized").mkdir()
            (root / "memory" / "canonical").mkdir(parents=True)
            (root / "memory" / "candidates").mkdir()
            (root / "memory" / "archive").mkdir()
            source = inbox / "synthetic-task.json"
            source.write_text(json_text(self.trajectory), encoding="utf-8")
            import hashlib
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            normalized = root / "trajectories" / "normalized" / source.name
            normalized.write_text(json_text(normalization_payload(self.trajectory, source.name, digest)), encoding="utf-8")

            archive(source, "rejected", "Evidence could not be verified", root)
            self.assertFalse(source.exists())
            self.assertTrue((root / "trajectories" / "archive" / source.name).is_file())
            self.assertFalse(normalized.exists())
            self.assertEqual(read_json(root / "trajectories" / "archive" / "normalized" / source.name)["source_sha256"], digest)
            review = read_json(root / "local" / "trajectory-reviews" / source.name)
            self.assertEqual(review["decision"], "rejected")
            self.assertEqual(review["reason"], "Evidence could not be verified")


if __name__ == "__main__":
    unittest.main()
