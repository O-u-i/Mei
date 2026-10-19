"""End-to-end v2 contracts, using retained synthetic workspaces only."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
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
        for command in (("read", "--id", "one"), ("search", "searchable")):
            code, output = cli(root, memory, *command)
            self.assertEqual(code, 0, output)
            self.assertEqual([item["memory_id"] for item in output["notes"]], ["one"])
            if command[0] == "search":
                self.assertNotIn("body", output["notes"][0])
                self.assertEqual(output["notes"][0]["description"], "test scope")
        selected = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/memory.py"),
                                   "read", "--mei-root", str(root)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(selected.returncode, 0, selected.stdout)
        discovery = json.loads(selected.stdout)
        self.assertEqual(discovery["ids"], ["one"])
        self.assertNotIn("notes", discovery)
        self.assertNotIn("Unique searchable claim.", selected.stdout)
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
        from urllib.parse import parse_qs, urlsplit
        self.assertEqual(parse_qs(urlsplit(note_uri(memory, "project/one.md")).query),
                         {"path": [str((memory / "project/one.md").resolve())]})

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
        from _common import publish_new_file
        def concurrent(source, destination):
            destination.write_bytes(b"Concurrent human file.\n")
            publish_new_file(source, destination)
        with patch("migrate_memory.publish_new_file", side_effect=concurrent):
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
        from _common import publish_new_file
        def concurrent(source, destination):
            destination.write_bytes(concurrent_bytes)
            publish_new_file(source, destination)
        with patch("memory_markdown.publish_new_file", side_effect=concurrent):
            with self.assertRaises(FileExistsError):
                write_note(memory, "project/one.md", note("one", "project", "Not authorized to replace."))
        self.assertEqual(target.read_bytes(), concurrent_bytes)
        self.assertEqual(read_memory(memory)["index"]["ids"], [])

    @unittest.skipUnless(os.name == "nt", "Windows uses atomic non-replacing rename instead of hard links")
    def test_windows_creation_works_without_hardlink_privilege(self):
        root, memory = fixture()
        with patch("os.link", side_effect=PermissionError("Hard links are not permitted by the managed sandbox")):
            result = write_note(memory, "project/new-note.md", note("new-note", "project", "No hard-link privilege required."))
        self.assertEqual(result["status"], "written")
        self.assertEqual(read_memory(memory)["index"]["ids"], ["new-note"])

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

    def test_obsidian_health_cli_binds_the_selected_checkout(self):
        root, memory = fixture()
        write_note(memory, "project/selected-note.md", note("selected-note", "project", "Selected checkout only."))
        command = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/check_obsidian.py"), "--mei-root", str(root)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output.get("direct_root"), output)
        self.assertEqual(output["index"]["ids"], ["selected-note"])
        with patch("sync_obsidian.resolve_memory_root", return_value=memory):
            self.assertTrue(check_vault(None).get("direct_root"))
        self.assertTrue(check_vault(memory).get("direct_root"))
        self.assertFalse((memory / "views").exists())

    def test_registry_and_migration_cannot_silently_use_an_unselected_root(self):
        from rebuild_registry import expected_registry, rebuild
        from validate_repository import validate_repository

        root, default = fixture()
        selected = default / "personal"
        initialize_memory(selected)
        write_note(selected, "project/selected-note.md", note("selected-note", "project", "Only the selected root."))
        (root / "local").mkdir()
        (root / "local/paths.toml").write_text(
            f"[paths]\nmei_root = '{root}'\nmemory_root = '{selected}'\n"
            f"codex_home = '{root / 'host'}'\nskills_root = '{root / 'skills'}'\ninbox = '{root / 'trajectories/inbox'}'\n",
            encoding="utf-8",
        )
        shutil.copyfile(ROOT / "VERSION", root / "VERSION")
        shutil.copyfile(ROOT / "README.md", root / "README.md")
        (root / "schemas").mkdir()
        shutil.copyfile(ROOT / "schemas/memory-note.schema.json", root / "schemas/memory-note.schema.json")
        records = expected_registry(root)["records"]
        self.assertEqual([record["id"] for record in records], ["selected-note"])
        self.assertEqual(records[0]["path"], "memory/markdown/personal/project/selected-note.md")
        (root / "registry").mkdir()
        default_index = (default / "MEMORY.md").read_bytes()
        self.assertTrue(rebuild(root))
        self.assertFalse(rebuild(root))
        self.assertEqual((default / "MEMORY.md").read_bytes(), default_index)
        for name in ("projects", "skills"):
            shutil.copyfile(ROOT / f"registry/{name}.example.yaml", root / f"registry/{name}.yaml")
        validate_repository(root)
        selected_note = selected / "project/selected-note.md"
        original = selected_note.read_bytes()
        selected_note.write_text("not a canonical note", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Markdown memory invalid"):
            validate_repository(root)
        selected_note.write_bytes(original)
        with self.assertRaisesRegex(MigrationError, "configured memory_root"):
            plan(root)
        self.assertFalse((root / "memory/migration").exists())

    def test_projection_cannot_target_another_canonical_root_or_nested_namespace(self):
        root, memory = fixture()
        other_root, other = fixture()
        write_note(memory, "project/source-note.md", note("source-note", "project", "Synthetic source."))
        write_note(other, "project/other-note.md", note("other-note", "project", "Keep the other vault."))
        before = {path: path.read_bytes() for path in other.rglob("*") if path.is_file()}
        with self.assertRaisesRegex(MemoryError, "canonical"):
            sync_obsidian(other, "cross-root", memory_root=memory)
        self.assertEqual({path: path.read_bytes() for path in other.rglob("*") if path.is_file()}, before)
        with self.assertRaisesRegex(MemoryError, "canonical"):
            sync_obsidian(memory, "nested", memory_root=memory, namespace="Mei Memory", lock_timeout=0.01)
        self.assertEqual(check_vault(memory, namespace="Mei Memory")["status"], "invalid")
        legacy = root / "obsidian-vault"
        legacy.mkdir()
        report = check_vault(legacy, memory_root=memory)
        self.assertEqual(report["status"], "invalid")
        self.assertIn("explicitly unregister", " ".join(report["errors"]))
        self.assertFalse((memory / "Mei Memory").exists())

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


def paired_fixture():
    root, memory = fixture()
    canonical = root / "memory/canonical/domain/same-id.json"
    archive = root / "memory/archive/same-id.json"
    data = {"id": "same-id", "category": "domain", "claim": "One historical claim.",
            "scope": "dated fixture", "status": "canonical", "verified_at": "2026-09-26T07:00:00Z",
            "provenance": [{"source": "fixture.md", "locator": "dated observation", "evidence_type": "current_file"}],
            "notes": "Later review notes.", "references": ["episodic/index.md"]}
    for path, record in ((canonical, data), (archive, dict(data, status="archived", verified_at=None,
                                                        notes="Archived original notes.", references=[]))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(record).encode() + b"\r\n")
    def pin(path):
        return {"source": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    decisions = {"schema_version": "v1.0.0", "pairs": [{"id": "same-id", "canonical": pin(canonical),
                  "archive": pin(archive), "disposition": "canonical_with_archive_evidence_review_required",
                  "reason": "Same claim; retain both historical sources without merging notes or provenance."}]}
    path = root / "reviewed-decisions.json"
    path.write_bytes(json.dumps(decisions).encode())
    return root, memory, path, decisions


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MigrationDispositionTests(unittest.TestCase):
    def test_unresolved_duplicate_and_missing_or_stale_approval_write_nothing(self):
        root, memory, decisions, _ = paired_fixture()
        self.assertEqual(migrate(root)["status"], "invalid")
        self.assertEqual(plan(root, dispositions_path=decisions)["status"], "ok")
        for kwargs in ({}, {"approved_dispositions_sha256": "0" * 64}):
            with self.assertRaises(MigrationError):
                migrate(root, dispositions_path=decisions, **kwargs)
        self.assertFalse((root / "memory/migration/manifest.yaml").exists())
        self.assertFalse((root / "memory/legacy-json").exists())
        self.assertFalse((memory / "domain/same-id.md").exists())

    def test_preserves_both_versions_id_history_and_review_gate_repeats_and_rolls_back_one_note(self):
        root, memory, decisions, data = paired_fixture()
        original = {pair["source"]: (root / pair["source"]).read_bytes() for pair in
                    (data["pairs"][0]["canonical"], data["pairs"][0]["archive"])}
        args = {"dispositions_path": decisions, "approved_dispositions_sha256": digest(decisions)}
        result = migrate(root, **args)
        self.assertEqual(result["status"], "migrated", result)
        manifest = root / "memory/migration/manifest.yaml"
        target = memory / "domain/same-id.md"
        raw = target.read_bytes()
        note = parse_note(raw.decode(), target)
        self.assertEqual(note["memory_id"], "same-id")
        self.assertTrue(note["review_required"])
        self.assertEqual(read_memory(memory)["notes"], [])
        self.assertEqual(note["references"], ["episodic/index.md"])
        self.assertIn("Later review notes.", note["body"])
        self.assertNotIn("Archived original notes.", note["body"])
        self.assertIn(data["pairs"][0]["archive"]["sha256"], note["body"])
        for source, source_raw in original.items():
            self.assertEqual((root / source).read_bytes(), source_raw)
            self.assertEqual((root / "memory/legacy-json" / source.removeprefix("memory/")).read_bytes(), source_raw)
        self.assertEqual(migrate(root, **args)["status"], "migrated")
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(verify(manifest, root)["status"], "ok")
        receipt = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        saved = manifest.read_bytes()
        archive_entry = next(entry for entry in receipt["entries"] if entry["kind"] == "evidence")
        archive_entry["created_by_manifest"] = True
        manifest.write_bytes(yaml.safe_dump(receipt).encode())
        with self.assertRaises(MigrationError):
            rollback(manifest, root, target=archive_entry["target"])
        archive_entry["created_by_manifest"] = False
        receipt["entries"] = [entry for entry in receipt["entries"] if entry["kind"] != "evidence"]
        manifest.write_bytes(yaml.safe_dump(receipt).encode())
        self.assertEqual(verify(manifest, root)["status"], "invalid")
        manifest.write_bytes(saved)
        self.assertEqual(rollback(manifest, root, target="memory/markdown/domain/same-id.md")["status"], "rolled_back")
        self.assertFalse(target.exists())
        self.assertEqual(verify(manifest, root)["status"], "ok")
        for source, source_raw in original.items():
            self.assertEqual((root / "memory/legacy-json" / source.removeprefix("memory/")).read_bytes(), source_raw)

    def test_pins_inventory_claims_relations_and_evidence_fail_closed(self):
        for failure in ("source_drift", "new_source", "claim", "relations", "evidence", "id"):
            with self.subTest(failure=failure):
                root, memory, decisions, data = paired_fixture()
                pair = data["pairs"][0]
                archive = root / pair["archive"]["source"]
                if failure in {"source_drift", "claim", "relations"}:
                    record = json.loads(archive.read_bytes())
                    record["claim" if failure != "relations" else "related"] = "Different fact" if failure != "relations" else ["other-id"]
                    archive.write_bytes(json.dumps(record).encode())
                    if failure != "source_drift":
                        pair["archive"]["sha256"] = digest(archive)
                elif failure == "new_source":
                    (archive.parent / "unexpected.json").write_bytes(archive.read_bytes())
                elif failure == "evidence":
                    evidence = root / "memory/legacy-json/canonical/domain/same-id.json"
                    evidence.parent.mkdir(parents=True)
                    evidence.write_bytes(b"Edited existing evidence.")
                else:
                    pair["id"] = "renamed-id"
                decisions.write_bytes(json.dumps(data).encode())
                try:
                    result = migrate(root, dispositions_path=decisions, approved_dispositions_sha256=digest(decisions))
                except MigrationError:
                    pass
                else:
                    self.assertEqual(result["status"], "invalid", result)
                self.assertFalse((root / "memory/migration/manifest.yaml").exists())
                self.assertFalse((memory / "domain/same-id.md").exists())

    def test_cli_approval_gate_and_post_migration_decision_drift(self):
        root, memory, decisions, data = paired_fixture()
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/migrate_memory.py"),
                                 "migrate", "--root", str(root), "--dispositions", str(decisions), "--maintainer"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertIn("separately approved", json.loads(result.stdout)["error"])
        first = migrate(root, dispositions_path=decisions, approved_dispositions_sha256=digest(decisions))
        self.assertEqual(first["status"], "migrated")
        target = memory / "domain/same-id.md"
        saved = target.read_bytes()
        data["pairs"][0]["reason"] = "Changed after review"
        decisions.write_bytes(json.dumps(data).encode())
        self.assertEqual(verify(root / "memory/migration/manifest.yaml", root)["status"], "invalid")
        with self.assertRaises(MigrationError):
            migrate(root, dispositions_path=decisions, approved_dispositions_sha256=digest(decisions))
        self.assertEqual(target.read_bytes(), saved)

    def test_source_drift_between_decision_validation_and_inventory_is_blocked(self):
        from migrate_memory import _source_records
        root, memory, decisions, data = paired_fixture()
        def changed_inventory(selected):
            source = root / data["pairs"][0]["canonical"]["source"]
            source.write_bytes(source.read_bytes() + b" ")
            return _source_records(selected)
        with patch("migrate_memory._source_records", side_effect=changed_inventory):
            result = migrate(root, dispositions_path=decisions, approved_dispositions_sha256=digest(decisions))
        self.assertEqual(result["status"], "invalid")
        self.assertFalse((root / "memory/migration/manifest.yaml").exists())
        self.assertFalse((root / "memory/legacy-json").exists())
        self.assertFalse((memory / "domain/same-id.md").exists())


if __name__ == "__main__":
    unittest.main()
