"""End-to-end v2 contracts, using retained synthetic workspaces only."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from memory_markdown import (MemoryError, initialize_memory, parse_note, write_note, read_memory, read_note, doctor)
from migrate_memory import MigrationError, migrate, rollback, verify, plan
from relations import add, check, repair_proposal, recover, traverse
from review_proposals import draft_proposal, preview_proposal, apply_proposal, validate_proposal_store
from sync_obsidian import sync_obsidian, check_vault, note_uri
from tests.test_memory_markdown import note


def fixture():
    root = ROOT / ".tmp" / "v2-contracts" / uuid.uuid4().hex
    memory = root / "memory/markdown"
    initialize_memory(memory)
    return root, memory


def approve(root, proposal):
    preview_proposal(proposal["proposal_id"], mei_root=root)
    return apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root)


def cli(root, memory, *args):
    result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/memory.py"),
                             *args, "--root", str(memory), "--mei-root", str(root)],
                            capture_output=True, text=True, encoding="utf-8")
    return result.returncode, json.loads(result.stdout)


class V2MemoryTests(unittest.TestCase):
    def test_duplicate_frontmatter_keys_are_rejected(self):
        raw = note("abc", "project", "Safe.")
        self.assertEqual(parse_note(raw, Path("abc.md"))["memory_id"], "abc")
        with self.assertRaises(MemoryError):
            parse_note(raw.replace("scope: test scope", "scope: test\nscope: duplicate"), Path("abc.md"))

    def test_cli_defaults_read_search_and_capability_boundaries(self):
        root, memory = fixture()
        content = note("one", "project", "Unique searchable claim.")
        result = write_note(memory, "project/one.md", content)
        for command in (("read",), ("search", "searchable")):
            code, output = cli(root, memory, *command)
            self.assertEqual(code, 0, output)
            self.assertEqual([item["memory_id"] for item in output["notes"]], ["one"])
        selected = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/memory.py"),
                                   "read", "--mei-root", str(root)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(selected.returncode, 0, selected.stdout)
        self.assertEqual([item["memory_id"] for item in json.loads(selected.stdout)["notes"]], ["one"])
        input_path = root / "input.md"
        for capability, text in (("P1", note("two", "project", "Safe.")),
                                 ("P2", note("two", "domain", "High risk.")),
                                 ("P2", note("one", "project", "Overwrite."))):
            input_path.write_text(text, encoding="utf-8")
            category = "domain" if "category: domain" in text else "project"
            target = "one" if "memory_id: one" in text else "two"
            code, _ = cli(root, memory, "write", "--path", f"{category}/{target}.md", "--content-file", str(input_path),
                          "--capability", capability, "--reviewed-personal", "--expected-sha256", result["sha256"])
            self.assertEqual(code, 1)
            self.assertEqual((memory / "project/one.md").read_text(encoding="utf-8"), content)
        with self.assertRaisesRegex(MemoryError, "approved proposal"):
            write_note(memory, "project/two.md", note("two", "project", "Relations.", relationships=["one"]))

    def test_cli_proposal_apply_works_without_obsidian_or_local_config(self):
        root, memory = fixture()
        write_note(memory, "project/one.md", note("one", "project", "Before."))
        content_path = root / "change.md"
        content_path.write_text(note("one", "project", "After."), encoding="utf-8")
        code, proposal = cli(root, memory, "propose", "--target", "project/one.md", "--content-file", str(content_path),
                             "--scope", "test scope", "--source-kind", "user_decision", "--source", "Synthetic human decision")
        self.assertEqual(code, 0, proposal)
        self.assertFalse((memory / ".obsidian").exists())
        self.assertFalse((memory / "views").exists())
        self.assertTrue((root / "memory/proposals" / (proposal["proposal_id"] + ".md")).exists())
        self.assertEqual(cli(root, memory, "apply", proposal["proposal_id"], "--capability", "P3", "--confirm")[0], 1)
        preview_proposal(proposal["proposal_id"], mei_root=root)
        self.assertEqual(cli(root, memory, "apply", proposal["proposal_id"], "--capability", "P2", "--confirm")[0], 1)
        code, applied = cli(root, memory, "apply", proposal["proposal_id"], "--capability", "P3", "--confirm")
        self.assertEqual(code, 0, applied)
        self.assertIn("After.", read_note(memory, "project/one.md")["body"])
        self.assertEqual(applied["receipt"]["writer"], "memory_markdown.write_note")

    def test_unapproved_namespaces_and_provenance_never_enter_retrieval(self):
        root, memory = fixture()
        write_note(memory, "project/one.md", note("one", "project", "Active."))
        for namespace in ("views", "archive", "proposals", "inbox", "review"):
            path = memory / namespace / "unapproved.md"
            path.parent.mkdir(exist_ok=True)
            path.write_text("Not canonical and deliberately not a valid note.", encoding="utf-8")
        pending = note("two", "project", "Pending.").replace("status: active", "status: active\nreview_required: true")
        (memory / "project/two.md").write_text(pending, encoding="utf-8")
        self.assertEqual([item["memory_id"] for item in read_memory(memory)["notes"]], ["one"])
        with self.assertRaises(MemoryError): read_memory(memory, {"two"})
        raw = note("bad", "project", "No evidence.").replace("provenance:\n  - source: user_decision\n    locator: test\n    evidence_type: user_decision\n    verified_at: null", "provenance: []")
        with self.assertRaises(MemoryError): parse_note(raw, Path("bad.md"))
        alternate = root / "alternate/schemas"
        alternate.mkdir(parents=True)
        (alternate / "memory-note.schema.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(MemoryError):
            write_note(memory, "project/bad.md", raw, schema_root=alternate.parent)
        self.assertFalse((memory / "project/bad.md").exists())

    def test_relation_proposals_bind_both_hashes_and_rollback_partial_failure(self):
        root, memory = fixture()
        for name in ("one", "two"):
            write_note(memory, f"project/{name}.md", note(name, "project", "Stable claim."))
        before = {path: path.read_bytes() for path in (memory / "project/one.md", memory / "project/two.md")}
        proposal = add(memory, "one", "two", "supports", mei_root=root)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        preview_proposal(proposal["proposal_id"], mei_root=root)
        self.assertEqual(apply_proposal(proposal["proposal_id"], mei_root=root)["status"], "authorization_required")
        import relations
        original = relations._atomic_write
        def fail_second(path, raw):
            if path.name == "two.md": raise OSError("synthetic second endpoint failure")
            return original(path, raw)
        with patch("relations._atomic_write", side_effect=fail_second):
            failed = apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root)
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        applied = apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root)
        self.assertEqual(applied["status"], "applied", applied)
        self.assertEqual(check(memory)["status"], "ok")
        self.assertIn({"type": "supported_by", "target": "one"}, read_note(memory, "project/two.md")["relations"])
        self.assertEqual(traverse(memory, "one")["nodes"], ["one", "two"])
        stale = add(memory, "one", "two", "depends_on", mei_root=root)
        preview_proposal(stale["proposal_id"], mei_root=root)
        second = memory / "project/two.md"
        second.write_text(second.read_text(encoding="utf-8").replace("Stable claim.", "External edit."), encoding="utf-8")
        self.assertEqual(apply_proposal(stale["proposal_id"], confirm=True, mei_root=root)["status"], "stale")
        self.assertIn("External edit.", second.read_text(encoding="utf-8"))

    def test_relation_drift_repair_only_drafts_and_direct_root_is_read_only(self):
        root, memory = fixture()
        for name in ("one", "two"):
            write_note(memory, f"project/{name}.md", note(name, "project", "Stable."))
        one = memory / "project/one.md"
        one.write_text(one.read_text(encoding="utf-8").replace("related: []", "related: [two]"), encoding="utf-8")
        before = one.read_bytes()
        self.assertEqual(check(memory)["status"], "drift")
        with self.assertRaisesRegex(MemoryError, "relation graph invalid"):
            read_memory(memory)
        proposal = repair_proposal(memory, mei_root=root)["proposals"][0]
        self.assertEqual(one.read_bytes(), before)
        self.assertEqual(approve(root, proposal)["status"], "applied")
        canonical = {path: path.read_bytes() for path in memory.glob("*/*.md")}
        self.assertEqual(sync_obsidian(None, "test", memory_root=memory)["status"], "synced")
        self.assertEqual(check_vault(None, memory_root=memory)["status"], "ok")
        self.assertFalse((memory / "views").exists())
        self.assertFalse((memory / "index.md").exists())
        self.assertEqual({path: path.read_bytes() for path in canonical}, canonical)
        self.assertIn("project%2Fone.md", note_uri(memory, "project/one.md"))

    def test_migration_repeats_verifies_preserves_protocols_and_single_file_rollback(self):
        root, memory = fixture()
        for group in ("continuity", "episodic"):
            path = root / "memory" / group / "technical.json"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'{"technical":true}\n')
        source = root / "memory/canonical/domain/legacy.json"
        source.parent.mkdir(parents=True)
        raw = b'{"id":"legacy","category":"domain","claim":"old","scope":"test","status":"canonical"}\r\n'
        source.write_bytes(raw)
        first = migrate(root)
        self.assertEqual(first["status"], "migrated", first)
        manifest = root / "memory/migration/manifest.yaml"
        target = memory / "domain/legacy.md"
        original = target.read_bytes()
        self.assertTrue(parse_note(original.decode(), target)["review_required"])
        self.assertEqual(parse_note(original.decode(), target)["provenance"], [])
        self.assertEqual(read_memory(memory)["notes"], [])
        self.assertEqual(migrate(root)["status"], "migrated")
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(verify(manifest, root)["status"], "ok")
        self.assertEqual(rollback(manifest, root)["status"], "rolled_back")
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), raw)
        self.assertEqual((root / "memory/legacy-json/canonical/domain/legacy.json").read_bytes(), raw)
        for group in ("continuity", "episodic"):
            self.assertEqual((root / "memory" / group / "technical.json").read_bytes(), b'{"technical":true}\n')

    def test_candidate_proposals_upgrade_body_and_protect_edited_targets(self):
        root, memory = fixture()
        old = "---\nschema_version: v0.1.0\nmemory_id: existing\ncategory: project\nstatus: active\nscope: old\nsource: user_decision\n---\nOriginal body stays.\n"
        path = memory / "project/existing.md"
        path.write_text(old, encoding="utf-8")
        source = root / "memory/candidates/candidate.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({"id": "candidate", "category": "project", "claim": "Unapproved", "scope": "test"}), encoding="utf-8")
        result = migrate(root)
        self.assertEqual(result["status"], "migrated", result)
        self.assertEqual(parse_note(path.read_text(encoding="utf-8"), path)["body"], "Original body stays.\n")
        self.assertEqual(parse_note(path.read_text(encoding="utf-8"), path)["provenance"], [])
        self.assertEqual(validate_proposal_store(root), 1)
        self.assertEqual(migrate(root)["status"], "migrated")
        self.assertEqual(verify(root / "memory/migration/manifest.yaml", root)["status"], "ok")
        proposal = root / "memory/proposals/candidate.md"
        proposal.write_text(proposal.read_text(encoding="utf-8") + "Human annotation.\n", encoding="utf-8")
        changed = proposal.read_bytes()
        self.assertEqual(migrate(root)["status"], "invalid")
        self.assertEqual(proposal.read_bytes(), changed)
        self.assertEqual(rollback(root / "memory/migration/manifest.yaml", root)["status"], "protected")

    def test_invalid_migration_and_escape_manifest_never_write_or_delete(self):
        root, memory = fixture()
        source = root / "memory/canonical/domain/bad.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"id":"bad","id":"other","category":"domain"}', encoding="utf-8")
        self.assertEqual(migrate(root)["status"], "invalid")
        self.assertFalse((root / "memory/legacy-json").exists())
        self.assertFalse((root / "memory/migration/manifest.yaml").exists())
        with self.assertRaises(MigrationError): plan(memory)
        outside = root / "keep.md"
        outside.write_text("Keep.", encoding="utf-8")
        manifest = root / "memory/migration/manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(yaml.safe_dump({"schema_version": "v0.2.0", "root": str(root), "entries": [{"source": "memory/canonical/a.json", "source_sha256": "0"*64, "target": "../keep.md", "id": "bad", "group": "canonical", "created_by_manifest": True}]}), encoding="utf-8")
        with self.assertRaises(ValueError): rollback(manifest, root)
        self.assertEqual(outside.read_text(encoding="utf-8"), "Keep.")

    def test_evidence_rechecked_at_apply_and_external_files_never_opened(self):
        root, memory = fixture()
        write_note(memory, "project/one.md", note("one", "project", "Before."))
        evidence = root / "evidence.md"
        evidence.write_bytes(b"Reviewed synthetic evidence.\n")
        proposal = draft_proposal("personal_markdown", "memory/markdown/project/one.md", note("one", "project", "After."),
                                  scope="test", source={"kind": "current_file", "locator": "evidence.md",
                                                        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}, mei_root=root)
        preview_proposal(proposal["proposal_id"], mei_root=root)
        evidence.write_bytes(b"Changed after preview.\n")
        self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root)["status"], "failed")
        self.assertIn("Before.", read_note(memory, "project/one.md")["body"])
        external = root.parent / (root.name + "-external-evidence.md")
        external.write_bytes(b"Never open automatically.\n")
        proposal = draft_proposal("personal_markdown", "memory/markdown/project/one.md", note("one", "project", "After."),
                                  scope="external", source={"kind": "current_file", "locator": str(external)}, mei_root=root)
        original = Path.read_bytes
        def guarded(path):
            if path == external:
                raise AssertionError("external evidence was opened")
            return original(path)
        with patch.object(Path, "read_bytes", guarded):
            preview = preview_proposal(proposal["proposal_id"], mei_root=root)
            self.assertEqual(preview["preview"]["source_status"], "external_review_required")
            self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root)["status"], "failed")
        self.assertIn("Before.", read_note(memory, "project/one.md")["body"])

    def test_tampered_content_and_approval_hash_cannot_write(self):
        from review_proposals import ProposalError, _read_proposal, _write_proposal
        root, memory = fixture()
        write_note(memory, "project/one.md", note("one", "project", "Before."))
        proposal = draft_proposal("personal_markdown", "memory/markdown/project/one.md", note("one", "project", "After."),
                                  scope="test", source={"kind": "user_decision", "locator": "synthetic decision"}, mei_root=root)
        preview_proposal(proposal["proposal_id"], mei_root=root)
        path = root / "memory/proposals" / (proposal["proposal_id"] + ".md")
        record = _read_proposal(path, root)
        record["approval"] = {"explicit": True, "approved_content_sha256": "0"*64, "approved_at": record["updated_at"]}
        _write_proposal(path, record, root)
        self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root)["status"], "failed")
        record = _read_proposal(path, root)
        record["suggested_content"] += "Tampered without a matching hash.\n"
        _write_proposal(path, record, root)
        with self.assertRaisesRegex(ProposalError, "content hash"):
            apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root)
        self.assertIn("Before.", read_note(memory, "project/one.md")["body"])

    def test_interrupted_relation_journal_requires_explicit_safe_recovery(self):
        import relations
        for external_edit in (False, True):
            with self.subTest(external_edit=external_edit):
                root, memory = fixture()
                for name in ("one", "two"):
                    write_note(memory, f"project/{name}.md", note(name, "project", "Before."))
                before = {path: path.read_bytes() for path in memory.glob("project/*.md")}
                proposal = add(memory, "one", "two", mei_root=root)
                preview_proposal(proposal["proposal_id"], mei_root=root)
                original = relations._atomic_write
                def interrupted(path, raw):
                    if path.name == "two.md" or (path.name == "one.md" and raw == before[path]):
                        raise OSError("synthetic interruption and failed rollback")
                    original(path, raw)
                with patch("relations._atomic_write", side_effect=interrupted):
                    self.assertEqual(apply_proposal(proposal["proposal_id"], confirm=True, mei_root=root)["status"], "failed")
                journal = memory / ".relation-transaction.json"
                journal_hash = hashlib.sha256(journal.read_bytes()).hexdigest()
                with self.assertRaisesRegex(MemoryError, "transaction"):
                    read_memory(memory)
                with self.assertRaises(MemoryError): recover(memory, journal_sha256=journal_hash)
                with self.assertRaises(MemoryError): recover(memory, journal_sha256="0"*64, confirm=True)
                if external_edit:
                    (memory / "project/two.md").write_bytes(before[memory / "project/two.md"] + b"Human edit.\n")
                    changed = {path: path.read_bytes() for path in before}
                    with self.assertRaisesRegex(MemoryError, "external edit"):
                        recover(memory, journal_sha256=journal_hash, confirm=True)
                    self.assertEqual({path: path.read_bytes() for path in before}, changed)
                    self.assertTrue(journal.exists())
                else:
                    self.assertEqual(recover(memory, journal_sha256=journal_hash, confirm=True)["status"], "recovered")
                    self.assertEqual({path: path.read_bytes() for path in before}, before)
                    self.assertFalse(journal.exists())
                    self.assertEqual(check(memory)["status"], "ok")

    def test_migration_protects_upgrade_cards_and_concurrent_creation(self):
        from migrate_memory import _atomic
        root, memory = fixture()
        old = "---\nschema_version: v0.1.0\nmemory_id: existing\ncategory: project\nstatus: active\nscope: old\nsource: user_decision\n---\nOriginal body.\n"
        path = memory / "project/existing.md"
        path.write_text(old, encoding="utf-8")
        self.assertEqual(migrate(root)["status"], "migrated")
        path.write_bytes(path.read_bytes() + b"Human annotation.\n")
        changed = path.read_bytes()
        self.assertEqual(migrate(root)["status"], "invalid")
        self.assertEqual(path.read_bytes(), changed)
        root, memory = fixture()
        source = root / "memory/candidates/candidate.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({"id": "candidate", "category": "project", "scope": "test", "claim": "Pending."}), encoding="utf-8")
        self.assertEqual(migrate(root)["status"], "migrated")
        card = memory / "review/candidate.md"
        card.write_bytes(card.read_bytes() + b"Human decision pending.\n")
        changed = card.read_bytes()
        self.assertEqual(migrate(root)["status"], "invalid")
        self.assertEqual(card.read_bytes(), changed)
        target = root / "concurrent.md"
        original_link = os.link
        def concurrent(source, destination):
            destination.write_bytes(b"Concurrent human file.\n")
            original_link(source, destination)
        with patch("migrate_memory.os.link", side_effect=concurrent):
            with self.assertRaises(FileExistsError): _atomic(target, b"Migration bytes.\n", expected_sha256=None)
        self.assertEqual(target.read_bytes(), b"Concurrent human file.\n")

    def test_single_file_rollback_cannot_create_dangling_relations(self):
        root, memory = fixture()
        for memory_id, other in (("one", "two"), ("two", "one")):
            source = root / "memory/canonical/project" / (memory_id + ".json")
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(json.dumps({"id": memory_id, "category": "project", "scope": "test", "claim": "Legacy.",
                                          "related": [other]}), encoding="utf-8")
        self.assertEqual(migrate(root)["status"], "migrated")
        manifest = root / "memory/migration/manifest.yaml"
        with self.assertRaisesRegex(MigrationError, "batch deletion"):
            rollback(manifest, root)
        before = {path: path.read_bytes() for path in memory.glob("project/*.md")}
        result = rollback(manifest, root, target="memory/markdown/project/one.md")
        self.assertEqual(result["status"], "protected", result)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(check(memory)["status"], "ok")

    def test_direct_creation_never_overwrites_or_deletes_a_concurrent_note(self):
        root, memory = fixture()
        target = memory / "project/one.md"
        concurrent_bytes = note("one", "project", "Concurrent reviewed note.").encode("utf-8")
        original_link = os.link
        def concurrent(source, destination):
            destination.write_bytes(concurrent_bytes)
            original_link(source, destination)
        with patch("memory_markdown.os.link", side_effect=concurrent):
            with self.assertRaises(FileExistsError):
                write_note(memory, "project/one.md", note("one", "project", "Not authorized to replace."))
        self.assertEqual(target.read_bytes(), concurrent_bytes)
        self.assertEqual(read_memory(memory)["index"]["ids"], [])

    def test_direct_root_inbox_cli_uses_selected_root_without_obsidian_config(self):
        root, memory = fixture()
        capture = memory / "inbox/capture.md"
        capture.parent.mkdir()
        capture.write_text(note("captured", "project", "Reviewed capture."), encoding="utf-8")
        before = capture.read_bytes()
        command = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/sync_obsidian.py"), "import-inbox",
                   "--note", "capture.md", "--memory-root", str(memory), "--reviewed-personal"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "written")
        self.assertIn("Reviewed capture.", read_note(memory, "project/captured.md")["body"])
        self.assertEqual(capture.read_bytes(), before)
        self.assertFalse((memory / "views").exists())
        self.assertFalse((memory / ".obsidian").exists())
        repeated = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(repeated.returncode, 1)
        self.assertIn("approved proposal", json.loads(repeated.stdout)["error"])

    def test_verify_reconstructs_source_fields_not_just_manifest_hashes(self):
        root, memory = fixture()
        source = root / "memory/canonical/project/one.json"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({"id": "one", "category": "project", "scope": "test", "claim": "Original."}), encoding="utf-8")
        self.assertEqual(migrate(root)["status"], "migrated")
        target = memory / "project/one.md"
        target.write_bytes(target.read_bytes().replace(b"Original.", b"Altered."))
        manifest = root / "memory/migration/manifest.yaml"
        record = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        record["entries"][0]["markdown_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest.write_text(yaml.safe_dump(record), encoding="utf-8")
        result = verify(manifest, root)
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("source fields" in error for error in result["errors"]), result)


if __name__ == "__main__":
    unittest.main()
