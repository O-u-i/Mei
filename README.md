# Mei v2.0.0

Mei is local-first agent memory infrastructure shared by GUI and TUI hosts. **Markdown with YAML frontmatter under `memory/markdown/` is the only runtime canonical source.** The same directory opens directly as an Obsidian vault. Reading, search, proposals, relations, diagnostics and migration do not require Obsidian, an Obsidian plugin or an online service.

**Status:** v2.0.0 workspace implementation. Production user-data migration has not been performed in this development pass. Synthetic tests, native command probes, real host cold starts and human acceptance are separate evidence.

## What changed

| Capability | v2.0.0 behavior |
| --- | --- |
| Canonical memory | `memory/markdown/<category>/<memory_id>.md`; Claim, Evidence and History in the body, identity/scope/provenance/relations/audit metadata in YAML. |
| Retrieval | Read, search, bounded host digest and relation traversal; active notes without `review_required` only. |
| Generated indexes | `MEMORY.md` and `registry/memory.yaml` are bounded derived views, never sources of facts. |
| Direct creation | P2/P3 may create reviewed low-risk preferences, project or feedback notes; this is not an upsert API. |
| Reviewed updates | Existing notes, high-risk categories and relations require proposal, preview, current base hash, explicit approval, approved-content hash, atomic write and audit. |
| Paired relations | Reciprocal `related` and eight typed inverse relations; proposals bind both endpoint hashes. Partial failures roll back; interrupted transactions require explicit recovery. |
| Continuity | Shared repo/context identity, fresh snapshots, generations and hashes; save, complete, export and retire are distinct operations. |
| Obsidian | The vault reads the canonical files directly. Default sync/check creates no `views/` copy or second canonical index. Review cards are inputs, not facts. |
| Migration | Byte-preserved JSON evidence, in-place Markdown upgrades, manifest and verification reports, repeatable execution and protected single-file rollback. |
| Technical protocols | Continuity, trajectory, episodic JSON and evaluation fixtures remain separate from ordinary knowledge memory. |

## Flow

All current diagrams use **Light only, 2048 × 1320**. HTML supports search, focus, relationship tracing and export; SVG is self-contained.

| Diagram | Viewer / static image | Editable source |
| --- | --- | --- |
| System workflow | [HTML](docs/architecture/memory-v2.workflow.html) · [SVG](docs/architecture/memory-v2.workflow.svg) | [Workflow JSON](docs/architecture/memory-v2.workflow.json) |
| Shared GUI/TUI reading | [HTML](docs/architecture/memory-read.html) · [SVG](docs/architecture/memory-read.svg) | [Architecture JSON](docs/architecture/memory-read.architecture.json) |
| Approval and recovery | [HTML](docs/architecture/memory-promotion.html) · [SVG](docs/architecture/memory-promotion.svg) | [Workflow JSON](docs/architecture/memory-promotion.workflow.json) |

### Reading and writing are different boundaries

1. Each host resolves the same Mei checkout, validates the selected repo/context, reads the bounded index and opens only relevant active notes. Legacy evidence, proposals, archives and notes awaiting review are not default context.
2. P2 may create a new reviewed low-risk note. Knowing an existing note's SHA-256 does not authorize overwriting it.
3. A proposal binds the suggested content and target base hash. Preview records the diff and unresolved issues. A checkbox expresses approval intent only.
4. A human requests `apply --confirm --capability P3` for that version. The writer rechecks the target, approved content, paths and privacy, then writes, verifies and records receipt/history.
5. Relation changes use the same proposal/apply handler and bind both endpoints. Drift is reported, never silently repaired.

### Three non-knowledge stores

- **Continuity:** a bounded current-task handoff, not a permanent fact. Saving is not completion or promotion.
- **Trajectory / episodic:** redacted evidence from completed tasks. One eligible task produces at most one trajectory; full chat exports are prohibited.
- **Legacy JSON:** read-only migration/audit evidence. Original bytes are retained and excluded from normal memory retrieval.

## Start here

### First installation

Python 3.11+ is required. Runtime dependencies are jsonschema and PyYAML from `requirements.txt`. Node and Archify are documentation tools, not memory runtime dependencies. Review the **exact versions and transitive licenses** before installation; these commands do not bypass your license gate.

```powershell
python -m pip install -r requirements.txt
python scripts/init_local.py
python scripts/memory.py init --root memory/markdown
python scripts/memory.py doctor --root memory/markdown
```

Run examples from the Mei checkout root. Initialization, migration and registry rebuilding belong to an explicitly selected maintainer workflow. Capability labels and confirmation flags never grant filesystem permissions.

Review Git-ignored `local/paths.toml`; its portable template is [local/paths.example.toml](local/paths.example.toml). Existing configuration is not silently rewritten to match a new README.

### Upgrading an existing installation

1. Preserve local configuration, source memory and current Git changes. Do not clean up user files merely because they are not in a diff.
2. Point `paths.memory_root` and `paths.obsidian_vault` at this checkout's `memory/markdown`, with `obsidian_memory_dir = ""`.
3. Run doctor and read-only migration plan. Review conflicts, provenance gaps, duplicate IDs, relations and the manifest before any production write.
4. Run migration only after explicit approval for the actual user data. Fixture validation is not that approval.
5. Rebuild derived indexes during explicit maintenance and validate the repository. Never edit an index to conceal an invalid note.

## Daily operations

### Read, search and diagnose

```powershell
python scripts/memory.py read
python scripts/memory.py read --id example-note
python scripts/memory.py search "keyword"
python scripts/memory.py doctor
python scripts/memory.py relations traverse example-note --depth 2
python scripts/memory.py relations check
```

`memory.py --root` selects the **Markdown root** and defaults to local configuration. Search matches ID, category, scope, source and body. Traversal returns active, retrievable notes only, with depth 1–4 and a 100-node limit. Doctor checks schema, paths/IDs, privacy, index and relations. Check is read-only. Explicit IDs do not bypass archived/pending-review exclusions.

Advanced `--schema-root` validation can add constraints; it never replaces or weakens the built-in note contract.

### Create a low-risk new note

```powershell
python scripts/memory.py write --path project/example-note.md --content-file ./reviewed-note.md --reviewed-personal --capability P2
```

The input must be a complete v0.2.0 note. Profile/domain/procedural notes, existing notes, archival changes and relation metadata cannot use this shortcut.

<details>
<summary>Minimal synthetic note: verify scope, provenance, date and privacy before using it</summary>

```yaml
---
schema_version: v0.2.0
memory_id: example-note
category: project
status: active
scope: example project only
source: explicit user decision
source_format: markdown
provenance:
  - source: explicit user decision
    locator: current reviewed decision
    evidence_type: user_decision
    verified_at: null
review_due_at: null
review_triggers: []
references: []
conflicts: []
related: []
relations: []
updated_at: '2026-01-01T00:00:00Z'
migrated_from: null
migrated_at: null
---
## Claim

A bounded, reviewed claim.

## Evidence

The explicit decision identified above.

## History

New note; not a transcript.
```

</details>

### Propose, preview, inspect, apply or reject

```powershell
python scripts/memory.py propose --target project/example-note.md --content-file ./proposed-note.md --scope "example project only" --source-kind user_decision --source "explicit reviewed decision" --reason "Explain this change"
python scripts/review_proposals.py preview --proposal-id proposal-id-from-output
python scripts/review_proposals.py status --proposal-id proposal-id-from-output
python scripts/memory.py apply proposal-id-from-output --confirm --capability P3
# Alternatively, reject explicitly and preserve the card/history:
python scripts/review_proposals.py reject --proposal-id proposal-id-from-output --reason "Explain the rejection"
```

Proposals live in `memory/proposals/<proposal-id>.md`; review cards live in the canonical root's `review/` directory. Neither is canonical memory. No Obsidian app, settings or connection is required. For another checkout, `--mei-root` selects the **Mei checkout** while `--root` still selects its configured Markdown root. Do not confuse the two.

File evidence uses `--source-kind current_file --source ./evidence.md --source-sha256 HASH`. Missing/changed evidence, card edits and target changes require renewed review. Evidence outside the selected Mei root is not automatically opened: review it separately rather than using a proposal as permission to access another location. A stale target is never overwritten. Do not edit proposal hashes/status to bypass checks. `governed_json` is a legacy Curator draft interface, not permission to write JSON canonical memory.

### Relations are proposals first

```powershell
python scripts/memory.py relations add example-note other-note --type related
python scripts/memory.py relations add newer-note older-note --type supersedes
python scripts/memory.py relations remove example-note other-note --type related
# These return proposals; continue with preview and explicit P3 apply.
python scripts/memory.py relations repair --proposal
```

| Forward | Inverse |
| --- | --- |
| related | related |
| supersedes | superseded_by |
| depends_on | dependency_of |
| supports | supported_by |
| contradicts | contradicted_by |

Schema validation rejects duplicate edges and illegal types. Check reports dangling targets, self-relations and missing reciprocals. Repair drafts one reviewable reciprocal fix; it does not guess missing targets or silently rewrite the graph.

Paired writes use a lock, both expected hashes, atomic file replacements and failure rollback. This does **not** claim that Windows provides a single two-file rename. An interrupted `.relation-transaction.json` blocks ordinary reads from accepting a partial pair. Inspect the journal and current hash before explicit recovery:

```powershell
Get-FileHash memory/markdown/.relation-transaction.json -Algorithm SHA256
python scripts/memory.py relations recover --journal-sha256 CURRENT-HASH --confirm --capability P3
```

Recovery restores the pre-transaction versions and refuses conflicting external edits. Reread, preview and explicitly apply afterward. Approval history and successful receipts remain in the proposal.

## GUI/TUI integration and continuity

### One Mei checkout, separate host homes

Each host retains its configuration, authentication, sessions, SQLite, locks, cache and plugins in its own `CODEX_HOME`. Both resolve the same `memory_root` through local configuration. Host runtime stores are not moved or merged.

| Configuration | Purpose |
| --- | --- |
| paths.mei_root / memory_root | The checkout and its single canonical Markdown root. |
| paths.codex_home_gui / codex_home_tui | Identify host homes; these fields do not grant access. |
| paths.codex_cli / skills_root | Machine-local native CLI and installed personal Skills. |
| host.memory_injection | False disables bounded memory injection without deleting data. |
| host.continuity_lifecycle_profile | Separately selected narrow context writer; no selection means no automatic write. |
| host.codex_config_profile_tui | Set only when that host has the corresponding layered configuration. |
| continuity.selected_contexts | Explicit verified `repo-id = "context-id"` binding when multiple contexts are active. Never inferred from a writable path. |

Named permissions use `[permissions.<name>]` and `default_permissions`. `--profile` selects a separate configuration layer, not a permission profile. Check the installed CLI's help and [official configuration documentation](https://learn.chatgpt.com/docs/config-file/config-basic). AGENTS, environment variables and session names are not ACL evidence.

```powershell
python scripts/continuity_adapters.py --diagnose --mei-root . --repo-root . --host gui
python scripts/continuity_adapters.py --diagnose --mei-root . --repo-root . --host tui
```

Diagnostics do not modify contexts or read transcripts. `--host` labels a surface; it does not switch homes or permissions. Set the actual process's `CODEX_HOME` before testing TUI. `native_permissions_verified: false` and `cold_session_verified: false` intentionally distinguish a command diagnostic from real host acceptance.

### Hooks and manual fallback

The shared adapter supports SessionStart, UserPromptSubmit, PreCompact, PostCompact and Stop:

- SessionStart supplies bounded memory and the selected task snapshot. Ambiguous selection requires a human choice; no context is auto-created.
- UserPromptSubmit marks dirty state only through the selected native writer and never saves the prompt.
- Manual compact may stop on dirty state. Automatic compact fails open and records reconciliation debt.
- PostCompact rereads state. Direct-root Obsidian operation needs no note projection.
- Stop may ask the current agent for one supplemental handoff, guarded by `stop_hook_active`. It reads no transcript, starts no model and does not complete the task.

Each host must independently discover and trust the current hook definition. Installing or enabling hooks is not trust; see the [official hook trust documentation](https://developers.openai.com/plugins/build/plugins). Never synthesize trust hashes, silently select broader permissions or bypass review. When hooks cannot run, manual reader/save commands remain available.

### Save through the installed entrypoint

Verify the explicit repo/context through registry metadata and the reader first. Resolve `$Entry` from the verified local `skills_root`. The snapshot is reader output; the reviewed payload contains bounded project fields such as goal, decisions, constraints, blockers, next_step and stable_refs, not a chat transcript.

```powershell
$Entry = Join-Path $env:USERPROFILE '.agents/skills/knowledge-governance/mei-continuity/scripts/entry.py'
python -X utf8 -B $Entry check --repo-root . --context-id chosen-context > ./local/fresh-snapshot.json
# After the current agent prepares the reviewed input from that snapshot:
python -X utf8 -B $Entry save --repo-root . --context-id chosen-context --snapshot ./local/fresh-snapshot.json --project ./local/reviewed-handoff.json --writer-session-id current-writer
```

Only `handoff_saved` and `handoff_noop` are success. Reread stale/conflicting snapshots. Never handwrite `project.json`/`checkpointer.json` or call a task complete because a handoff was saved. A missing context requires an explicit maintenance request and entrypoint `create`; inspect `create_continuity.py --help` for expected index/hash requirements.

When a fresh snapshot reports reconciliation debt after automatic compact, review the current bounded fields and explicitly pass `save --reconcile`. This does not bypass stale/base-hash checks; a stale snapshot still requires rereading.

Completion uses separate `complete_continuity.py checkpoint/export/retire/curator` operations and a selected context. Mutations require fresh snapshots. Export requires a human-reviewed redacted trajectory and is not automatic. The technical schema remains v0.1.0: Mei v2 accepts schema-validated v1 continuity release indexes without rewriting them.

## Migration and recovery

**The root arguments differ:** `memory.py --root` is the Markdown root; `migrate_memory.py --root` is the **Mei checkout**, not `memory/markdown`.

```powershell
python scripts/migrate_memory.py plan --root .
# Only after reviewing the plan/backups and approving the actual migration:
python scripts/migrate_memory.py migrate --root . --maintainer
python scripts/migrate_memory.py verify --root .
python scripts/migrate_memory.py rollback --root . --target memory/markdown/domain/example-note.md --maintainer
```

- Inputs are JSON under old `memory/{candidates,canonical,archive}/` and canonical-category Markdown needing in-place upgrade. Stable IDs, body text and verifiable provenance are retained.
- Missing provenance/scope means `review_required`, never invented verification. Candidates become complete unapproved Markdown proposals, not retrievable canonical facts.
- Preflight rejects duplicate IDs/keys, secrets, invalid fields, unsafe/reparse paths and invalid relations. Invalid preflight does not first write a partial set of notes.
- Original JSON bytes are retained in `memory/legacy-json/` without overwriting different evidence. Pre-upgrade Markdown bytes remain in `memory/migration/backups/`.
- The manifest is `memory/migration/manifest.yaml`; verification reports are in `memory/migration/reports/`. Repeated migration retains ownership/hash receipts and protects human edits.
- Rollback removes **one** unchanged, manifest-created Markdown file per invocation. It never deletes legacy JSON, technical protocols or pre-existing Markdown. Select each file explicitly; batch deletion is disabled. Modified targets and removals that would create dangling relations return protected; remove relations through approved proposals first. Upgrade backups remain for reviewed manual recovery.
- Fixture validation does not mean production user data was migrated. Do not run production examples merely to obtain a passing test badge.

## Optional Obsidian direct-root workbench

Choose **Open folder as vault → this checkout's `memory/markdown/`**. No nested vault or plugin is required. Optional `.obsidian/` GUI settings are not runtime memory input.

```powershell
python scripts/sync_obsidian.py check --memory-root memory/markdown
python scripts/sync_obsidian.py sync --memory-root memory/markdown --source-generation manual
python scripts/check_obsidian.py --memory-root memory/markdown
python scripts/sync_obsidian.py uri --memory-root memory/markdown --note project/example-note.md
```

Default direct-root sync/check validates the canonical files without creating `views/`, a second `index.md` or a projection manifest. URI returns a validated open-only link; it does not launch the GUI. Proposal cards live in `review/`; human captures may live in `inbox/`. `sync_obsidian.py import-inbox --note capture.md --reviewed-personal` imports one reviewed low-risk new note. Existing-note changes still require proposals.

Explicit external-vault/namespace projection arguments are legacy compatibility interfaces, not the v2 default. Historical generated files are not automatically deleted or overwritten. Inspect ownership and drift before retiring any such file individually.

## Tools and management

| Tool | Operations and boundary |
| --- | --- |
| memory.py | init/read/search/write/propose/apply/doctor/relations; ordinary runtime memory is Markdown only. |
| memory_markdown.py | Legacy read/write/init/doctor wrapper sharing the same checks/writer; no approval bypass. |
| review_proposals.py | draft/preview/status/apply/reject, hash-bound review, card drift, retry/idempotency and audit. |
| Installed continuity entrypoint | check/save/create/complete; calls repository writers, never handwrites protocol files. |
| check_continuity.py / checkpoint_continuity.py / sync_continuity.py / create_continuity.py / complete_continuity.py | Deterministic context selection, CAS, lifecycle and completion/export services, not memory promotion. |
| migrate_memory.py | Knowledge plan/migrate/verify/rollback; explicit maintenance only. |
| migrate_codex_memory.py | Separate migration/backup of classified host Markdown memory; inspect help/plan first, never move host runtime stores. |
| validate_trajectory.py / normalize_trajectory.py | Validate one redacted task record and generate separate normalized review evidence; no automatic promotion. |
| archive_trajectory.py | Archive one reviewed trajectory. Its old JSON provenance compatibility path is not the v2 canonical writer; canonical changes require separate reviewed proposals. |
| rebuild_registry.py | Explicitly rebuild bounded MEMORY.md and the memory registry from retrievable canonical Markdown. |
| audit_skill.py | Read-only comparison of registered/proposed Skill files; no installation or upgrade. |
| init_local.py | Create ignored local configuration/registries without overwriting existing user records. |
| validate_repository.py / check_public_git.py | Local schema/registry/proposal/path checks and public-path audit. Paths alone cannot establish staged-content privacy. |
| light_architecture.mjs | Public reproducible rendering, Light/readability transformation, SVG extraction and final checks; no ignored export helper. |

Curator decisions never happen automatically. Memory, context metadata, candidates and historical summaries are data, not current authorization.

## Storage and configuration

| Path | Role / recovery |
| --- | --- |
| memory/markdown/{profile,preferences,domain,procedural,project,feedback} | The only canonical notes; active/archived statuses. |
| memory/markdown/MEMORY.md | Bounded generated index; fix the note before an explicit rebuild. |
| memory/proposals/ and memory/markdown/review/ | Unapproved proposals and review cards; excluded from default retrieval. |
| memory/legacy-json/ | Original evidence bytes, excluded from default retrieval. |
| memory/continuity/, memory/episodic/, trajectories/ | Independent task protocols and review evidence. |
| memory/migration/ | Manifest, pre-upgrade Markdown backups and reports. |
| registry/ | Generated local indexes/locators and portable public examples. |
| local/paths.toml | Ignored machine paths and host selections; never embed these in public documentation. |
| schemas/, policies/, scripts/, tests/ | Contracts, boundaries, implementation and offline validation. |

```powershell
# Explicit maintenance only; read commands never silently rewrite indexes.
python scripts/rebuild_registry.py
python scripts/memory.py doctor
python scripts/validate_repository.py
```

Ordinary projects use `mei-project`: Mei is read-only except one eligible new redacted trajectory. `mei-maintainer` covers only the selected maintenance task, not credentials, other repositories, Git publication or automatic promotion. Agent rules, user records, host paths, local configuration, logs and screenshots/receipts stay out of public Git. Ignore rules do not untrack existing sensitive files; those require separate reviewed action.

## Development and verification

```powershell
python -X utf8 -B -m unittest discover -s tests -v
python scripts/validate_repository.py
python scripts/check_public_git.py
git diff --check
git diff --cached --check
```

Coverage includes reader/writer contracts, duplicate-key/schema checks, capability boundaries, GUI-free proposal lifecycle, paired-write rollback/stale handling, protected migration/rollback, untouched technical protocols, continuity concurrency/completion and host adapters. Synthetic evidence is retained in ignored `.tmp/` rather than batch-deleted. Production data, native hook trust, real cold starts and Obsidian Graph/Backlinks require separate acceptance.

### Reproducible Light diagrams

Approved documentation dependencies are installed **Archify 2.17.0-dev.1** and its embedded **JetBrains Mono Google Fonts v24** bytes. No tool installation/upgrade is automatic. A different version requires renewed license and renderer-contract review.

```powershell
$Archify = Join-Path $env:USERPROFILE '.agents/skills/engineering/archify'
node scripts/light_architecture.mjs --build --archify-root $Archify
node scripts/light_architecture.mjs --check
```

The public JSON, this script and the approved local Archify package reproduce the artifacts. The script runs the upstream renderer and the same native nine-check CLI with retained intermediate files. It does **not** claim an upstream `deliver` receipt: the upstream validate/deliver wrappers recursively clean temporary directories, which conflicts with this workspace's no-batch-deletion policy. Upstream evidence, final Light checks and final hashes are recorded separately in ignored `local/archify-light-transform.json`. Every final SVG is self-contained with 2048×1320 width/height/viewBox; no ignored `local/export_archify_svg.py` is needed and the installed Skill is unchanged.

Final SVG typography is enlarged in the project transform to preserve readability at the fixed dimensions. Final artifact checks run again after that transformation. This remains standard base delivery, not an upstream showcase claim.

### Browser and human checks

Measure the actual final HTML at 1440×900, 1600×1000, 1920×1080 and 2048×1320. Require no document overflow, readable labels, balanced composition, working search/focus/export and Light mode even with a dark OS preference. Use a separately named browser session/profile, retain screenshots locally and bind the record to the final artifact SHA-256/byte count.

The upstream `visual-check` utility also recursively removes a temporary browser profile. Do not run it under this repository's deletion policy without a separate explicit exception. A persistent-profile Chrome session through an already installed browser tool can provide supplementary evidence; it must not be relabeled as an upstream visual-check pass.

Deterministic checks, browser measurements and perceptual review are separate claims. A warning, nonzero command, unavailable browser or missing human acceptance must remain visible. Never hide overflow, clip content, shrink text or remove meaningful edge labels to manufacture a pass.

## License

Mei uses the [MIT License](LICENSE). Approved Archify viewer/font copyright and license notices are retained with the artifacts; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This does not require publishing private Mei data/source and is not blanket approval for future third-party versions.
