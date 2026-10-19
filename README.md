# Mei v2.0.0

[![GitHub](https://img.shields.io/badge/GitHub-Repository-181717?logo=github&logoColor=white)](https://github.com/O-u-i/Mei)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](requirements.txt)
[![Checkout v2.0.0](https://img.shields.io/badge/Checkout-v2.0.0-6f42c1)](VERSION)

[Source on GitHub](https://github.com/O-u-i/Mei) · [Report an issue](https://github.com/O-u-i/Mei/issues)

Mei is local-first, reviewed memory infrastructure for coding agents. It keeps reusable knowledge separate from current-task handoffs, so agents can recover context without treating an old summary as a current fact or permission to act. Multiple hosts can share one Mei checkout while retaining independent host configuration and runtime data.

**Markdown with YAML frontmatter under `memory/markdown/` is the only runtime canonical source.** The same directory opens directly as an Obsidian vault. Reading, search, proposals, relations, diagnostics and migration do not require Obsidian, a plugin or an online service.

**Status:** v2.0.0 checkout implementation, not a hosted service or a guarantee of lossless compaction. Migration and native host acceptance are installation-specific; repository tests do not establish either. See [VERSION](VERSION), [memory policy](policies/memory-policy.md) and [permissions policy](policies/permissions-policy.md).

## Quick start

From a local checkout, with Python 3.11+ and an explicitly selected maintenance scope:

```powershell
python -m pip install -r requirements.txt
python scripts/init_local.py
python scripts/memory.py doctor
python scripts/memory.py read
```

The initializer creates an empty canonical vault and missing local configuration; it preserves existing files and does not import personal data, install hooks or register an Obsidian vault. On a fresh install, an empty index is expected. Review `local/paths.toml` before connecting hosts or migrating existing memory. Dependency installation remains subject to the local approval and license policy.

- **Knowledge:** use [daily operations](#daily-operations) to read notes or propose a reviewed change.
- **Task handoff:** use [host integration](#gui1gui2-integration-and-continuity) for setup, explicit project enrollment and continuity.
- **Obsidian:** open the canonical folder directly; see [the workbench and graph guide](#optional-obsidian-direct-root-workbench).
- **Contributing:** see [development and verification](#development-and-verification); private memory is not a contribution fixture.

## What Mei provides

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
| Shared GUI1/GUI2 reading | [HTML](docs/architecture/memory-read.html) · [SVG](docs/architecture/memory-read.svg) | [Architecture JSON](docs/architecture/memory-read.architecture.json) |
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

### Choose the permission mode

| Mode | Use it when | Boundary |
| --- | --- | --- |
| `mei-project` | Doing ordinary work in another project. | Mei is read-only except one eligible new redacted trajectory in `trajectories/inbox`. |
| `mei-personal-memory` | Explicitly recording a reviewed, new low-risk preference, project or feedback note. | No existing-note overwrite, relation edit, canonical promotion or host configuration change. |
| `mei-maintainer` | Performing an explicitly scoped Mei maintenance or Curator task. | Proposals, current hashes and approval still govern existing memory changes; no automatic promotion or access to unrelated projects. |

These are local command/filesystem modes, not interchangeable feature switches. `setup` and `attach` do not add persistent per-context profiles. Full Access, if separately selected in the host, does not bypass Mei's writer ownership, review or CAS checks. A profile name or environment variable is not evidence of effective isolation.

## Start here

### First installation

Python 3.11+ is required. Runtime dependencies are jsonschema and PyYAML from `requirements.txt`. Node and Archify are documentation tools, not memory runtime dependencies. Review the **exact versions and transitive licenses** before installation; these commands do not bypass your license gate.

Use the [quick start](#quick-start) for initialization. `python scripts/memory.py init --root memory/markdown` can also create an empty Markdown layout explicitly; it preserves existing files.

Run examples from the Mei checkout root. Initialization, migration and registry rebuilding belong to an explicitly selected maintainer workflow. Capability labels and confirmation flags never grant filesystem permissions.

Review Git-ignored `local/paths.toml`; its portable template is [local/paths.example.toml](local/paths.example.toml). Existing configuration is not silently rewritten to match a new README. New installations default `skills_root` to the personal `$HOME/.agents/skills` directory, independently of either `CODEX_HOME`; verify your actual installed location against the [official Skill discovery paths](https://learn.chatgpt.com/docs/build-skills). Initialization rejects non-direct-root `MEI_OBSIDIAN_VAULT` or nonempty `MEI_OBSIDIAN_MEMORY_DIR` overrides before writing. It prepares files, not Obsidian's application registration.

### Upgrading an existing installation

1. Preserve local configuration, source memory and current Git changes. Do not clean up user files merely because they are not in a diff.
2. Point `paths.memory_root` and `paths.obsidian_vault` at this checkout's `memory/markdown`, with `obsidian_memory_dir = ""`.
3. Run doctor and read-only migration plan. Review conflicts, provenance gaps, duplicate IDs, relations and the manifest before any production write.
4. Run migration only after explicit approval for the actual user data. Fixture validation is not that approval.
5. Rebuild derived indexes during explicit maintenance and validate the repository. Never edit an index to conceal an invalid note.

## Daily operations

### Read, search and diagnose

Discovery is description-first: bare `read` returns the bounded index (at most
16 KiB of text), never the note bodies. `search` returns matching IDs, paths,
scopes and descriptions; use `read --id` for selected details. YAML
`description` is an optional single-line retrieval cue (1–240 characters);
older notes fall back to `scope`. Startup hooks inject this same bounded index,
not the corpus. Validation/search may inspect files locally; that is distinct
from returning their content to the model. The internal `read_memory` library
still supports whole-corpus maintenance, not automatic context injection.

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

## GUI1/GUI2 integration and continuity

`GUI1` and `GUI2` are local labels for two independently configured agent hosts, not additional products or required services. A single-host installation can configure and verify only that host.

### One Mei checkout, separate host homes

Each host retains its configuration, authentication, sessions, SQLite, locks, cache and plugins in its own `CODEX_HOME`. Both resolve the same `memory_root` through local configuration. Host runtime stores are not moved or merged.

| Configuration | Purpose |
| --- | --- |
| paths.mei_root / memory_root | The checkout and its single canonical Markdown root. |
| paths.codex_home_gui1 / codex_home_gui2 | Identify GUI1 and GUI2 homes; these fields do not grant access. |
| paths.codex_cli / skills_root | Machine-local native CLI and installed personal Skills. |
| host.memory_injection | False disables bounded memory injection without deleting data. |
| local/attachments/<repo-id>.json | Explicit context and host/session writer binding, not a permission profile. |
| host.codex_config_profile_gui1 / codex_config_profile_gui2 | Optional host-specific configuration layer; omit when the effective settings are already in the base config. |
| continuity.selected_contexts | Explicit verified `repo-id = "context-id"` binding when multiple contexts are active. Never inferred from a writable path. |

Named permissions use `[permissions.<name>]` and `default_permissions`. `--profile` selects a separate configuration layer, not a permission profile. Check the installed CLI's help and [official configuration documentation](https://learn.chatgpt.com/docs/config-file/config-basic). AGENTS, environment variables and session names are not ACL evidence.

```powershell
python scripts/continuity_adapters.py --diagnose --mei-root . --repo-root . --host gui1
python scripts/continuity_adapters.py --diagnose --mei-root . --repo-root . --host gui2
```

Diagnostics do not modify contexts or read transcripts. `--host` labels a surface; it does not switch homes or permissions. Set the actual process's `CODEX_HOME` before testing each GUI. New local bindings use `gui1` and `gui2`; legacy `gui`/`tui` readers and archived evidence remain compatible and are not relabeled. `native_permissions_verified: false` and `cold_session_verified: false` intentionally distinguish a command diagnostic from real host acceptance.

### Two-layer enrollment (no daemon)

1. **Once per host:** run the configured checkout's
   `python scripts/mei.py setup --host <gui1|gui2|all>`. Review the read-only
   plan, then use `--apply` within an authorized maintenance task. This preserves
   unrelated hooks/config bytes, backs up changed files, installs a verified
   Skill-root pointer. It creates no persistent permission profiles.
   Native host discovery/trust remains a user-owned first-time action; setup
   never manufactures a trusted hash or changes the default permission mode.
   Setup writes hooks and a root pointer; it does not install the personal Skill
   packages themselves. The public checkout works through its Python scripts.
   Installed Skill examples require separately installed, verified entrypoints
   under the configured `skills_root`.
2. **Once per project:** run `python <Mei>/scripts/mei.py attach --repo-root
   <actual-git-root> --context-id <explicit-id> --goal <current-task-goal>`,
   then `--apply` within the authorized scope. The installed maintenance Skill
   also exposes `entry.py connect attach ...`. New context creation reuses the
   existing locked writer and backs up/rebases only old index technical fields;
   no old semantic project is rewritten. Existing context IDs must be explicit.
   Attach changes only Mei enrollment/context files, never either host config.
   Native sandbox initialization may still require a scoped user approval.

Re-running either layer is a no-op when unchanged. No resident service, package
installation or background model is needed. Default role is observer. To enroll
the current agent as writer, explicitly supply `--role writer --host <gui1|gui2>
--session-id <actual-current-session>`. An explicit `--host gui1` or `--host gui2`
identifies the writer host; it never edits that host's config.
A foreign writer is not overwritten; `--takeover` requires explicit
authorization and clean state. This is a
cooperative lifecycle gate, not an OS lock preventing arbitrary edits by another
agent. CAS/locks still protect continuity writes. The ordinary project grant
stays unchanged. Each native worker receives a command-local `mei-worker`
grant: `:read-only`, exactly one writable context directory, network disabled.
It is not saved in `config.toml` or added to the permission menu. Existing
attachment profile fields remain readable but no longer select a sandbox.
Handoffs no longer need matching profile names in both host homes.
Writer publication rechecks the context under its lifecycle/save lock; queued
saves and lifecycle events recheck the attachment after acquiring that lock.
A former writer cannot silently publish an already queued cooperative save
after a clean-state transfer. Arbitrary direct filesystem access is separate.

The installed continuity save entrypoint dispatches enrolled projects through
`mei.py save` with fresh snapshot and the current agent's bounded project
  payload. The caller validates the bounded schema/snapshot and project-relative
  file references before native startup; the locked writer revalidates them.
  External Mei/Skill source paths belong in bounded decisions, not
  `stable_refs.file`, which may not escape the selected project.
  The caller reads capped JSON files and transfers the input over stdin,
so the inner context-only sandbox does not need access to the outer sandbox's
private temporary directory. Failure reports expose only fixed error codes and
stages, not raw child errors or summaries; no broader fallback is attempted.
Known native initialization failures, including restricted-token or Windows
desktop creation, are
reported separately from writer rejection. A scoped native approval must remain
user-owned; rerun only the approved self-contained operation with fresh inputs,
not a command referencing another sandbox's temporary files. The inner context
profile stays unchanged.
The save child receives the caller's already validated actual home explicitly,
including when CODEX_HOME was absent and the default home was used; the parent
environment is unchanged and another host is never selected as a fallback.
An automatic compact Hook cannot synthesize unsaved reasoning. Stop
requests one supplemental save only from the matching dirty writer, never a
foreign/recovery context; SessionStart (source=compact) injects the last valid
handoff as model context. PostCompact reports a UI/event-stream diagnostic,
not model-visible additionalContext. Mid-turn
automatic compact before such a save remains a declared limit, not a success.
SessionStart now injects a `continuity_check` for the same agent (also included
in the PostCompact diagnostic):
before its next task action it must read the exact context through the installed
reader, compare project/index hashes and saved generation, and inspect current
project evidence before resuming mutations or replaying interrupted effects.
The hook checks saved-record integrity but leaves semantic recovery and
latest-progress coverage unverified; an injected instruction is not acceptance.
The matching enrolled writer also receives a milestone checkpoint instruction:
save changed bounded fields before the next long phase, with a fresh snapshot
and readback, rather than relying only on Stop. Observer, recovery and terminal
states never receive checkpoint permission. Successful checks need no user
verification prompt; unresolved evidence or authorization gaps are reported once.
These are same-agent instructions, not an OS-level tool gate or a guarantee of
saving reasoning before every mid-turn compact. No transcript reader, background
model, new profile or automatic reconcile is introduced; existing recovery debt
still requires explicit authorization. Full Access does not bypass writer checks.
An unenrolled project remains unenrolled: no recovery success is claimed, but
the hook does not require enrollment or block its ordinary authorized work.
Prompt submission conservatively marks the enrolled writer dirty, even for a
read-only prompt. A resulting Stop request never overrides a no-write user
instruction; an observer is preferable for repeated read-only inspections.
If the tool's outer sandbox denies native runtime initialization, save reports
a bounded failure reason (such as `runtime_cap_sid_unavailable`) and stops.
Any native approval belongs to the user; no wider fallback is selected.

`python <Mei>/scripts/mei.py status --repo-root <actual-git-root>` is read-only.
It reports context, writer, source-qualified permission labels and the latest
metadata-only event receipts per repo/host. Diagnostics use the same command-local
worker with only `local/hook-events` writable; denied recording is surfaced.
The one-off `_hook_diagnostics` writer and automatic external Obsidian
projection worker are retired; historical receipts stay untouched. Direct-root
vault checks remain read-only; external projections use explicit Obsidian sync.
Receipts distinguish started/finished, skipped, not-attached and adapter failure;
unobserved means unknown, **not** proof that a native event never fired. No prompt,
transcript or semantic payload is logged. Native identity/trust and actual GUI
compact acceptance must be established separately from synthetic events.

A reusable one-message project request is:

> Use the installed Mei maintenance connect entrypoint to attach this actual Git
> root to Mei. Reuse the explicitly selected existing context or create the
> project context for this task; show and apply only the scoped enrollment plan
> with backups. Keep observer role unless I designate this agent as writer.
> Do not change default permissions, Hook trust, business files, other tasks or
> canonical memory. Return one short health receipt; stop only on real ambiguity
> or a native approval requirement, not after each mechanical check.

### Hooks and manual fallback

The shared adapter supports SessionStart, UserPromptSubmit, PreCompact, PostCompact and Stop:

- SessionStart supplies bounded memory, the selected task snapshot and an agent recovery check, including source=compact. Ambiguous selection requires a human choice; no context is auto-created.
- UserPromptSubmit marks dirty state only through the selected native writer and never saves the prompt.
- Manual compact may stop on dirty state. Automatic compact fails open and records reconciliation debt.
- PostCompact rereads state for UI/event-stream diagnostics; model injection uses SessionStart(source=compact). Direct-root Obsidian operation needs no note projection.
- Stop may ask the current agent for one supplemental handoff, guarded by `stop_hook_active`. This uses `decision: "block"` with a continuation `reason`, **not** `continue: false`, which ends the turn. It reads no transcript, starts no separate model and does not complete the task.

Each host must independently discover and trust the current hook definition. Installing or enabling hooks is not trust; see the [official hook and Stop protocol documentation](https://learn.chatgpt.com/docs/hooks). Never synthesize trust hashes, silently select broader permissions or bypass review. When hooks cannot run, manual reader/save commands remain available.

### Installed-host acceptance is separate from repository tests

Repository validation cannot establish native hook trust, installed Skill semantics, selected filesystem permissions, or a successful interactive launch. Before compact testing:

1. Inspect `/hooks` in **each actual host**. The active definitions must include all five events above, including Stop; avoid duplicate project/user handlers. Historical trust entries do not prove that today's definitions are loaded or trusted.
2. Inspect the effective named permissions and roots, not just a profile name or environment variable. The actual `skills_root`, selected context, and host runtime must have the access required by that operation. `--profile` selects a config layer; where supported, `-c default_permissions=mei-maintainer` selects the named maintenance default. A denied home/alias/runtime startup is a failed launch, not permission evidence; do not substitute a fresh home or broader profile to make it pass.
3. Resolve installed Skills through the active local registry and verify both frontmatter identity and instructions. JSON-canonical or generated-`views/` guidance is not the v2 workflow. Run the actual entrypoint as well as its repository handler; either can be stale independently.
4. In real GUI1 and GUI2 sessions, check startup injection, a dirty manual-compact gate, an explicit fresh-snapshot save, and successful `/compact` followed by the same repo/context and current Markdown index. Automatic compact must remain fail-open and expose reconciliation debt. Keep bounded before/after hashes and generations locally, not raw sessions. A synthetic hook envelope, `--host` label, or previous cold start does not satisfy this acceptance.

Host-native memory snapshots are historical evidence, not proof that Mei migration or injection ran. Do not silently enable them, merge host stores, or promote their old instructions to repair a deployment gap.

### Save through the installed entrypoint

Verify the explicit repo/context through registry metadata and the reader first. Resolve `$Entry` from the verified local `skills_root`. The snapshot is reader output; the reviewed payload contains bounded project fields such as goal, decisions, constraints, blockers, next_step and stable_refs, not a chat transcript.

```powershell
$Entry = Join-Path $env:USERPROFILE '.agents/skills/knowledge-governance/mei-continuity/scripts/entry.py'
python -X utf8 -B $Entry check --repo-root . --context-id chosen-context > ./local/fresh-snapshot.json
# After the current agent prepares the reviewed input from that snapshot:
python -X utf8 -B $Entry save --repo-root . --context-id chosen-context --snapshot ./local/fresh-snapshot.json --project ./local/reviewed-handoff.json --writer-session-id current-writer
```

Only `handoff_saved` and `handoff_noop` are success. Reread stale/conflicting snapshots. Never handwrite `project.json`/`checkpointer.json` or call a task complete because a handoff was saved. A missing context requires an explicit maintenance request and entrypoint `create`; inspect `create_continuity.py --help` for expected index/hash requirements.

When a fresh snapshot reports reconciliation debt after automatic compact, inspect current task evidence and obtain explicit authorization before `save --reconcile`. The recovery hook never grants that authorization. This does not bypass stale/base-hash checks; a stale snapshot still requires rereading.

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
- If canonical and archive inputs reuse a stable ID, stop for a reviewed historical-version disposition. Do not silently suffix IDs, drop archives or merge claims to force a valid plan. Migration targets this checkout's `memory/markdown`; a different configured subroot is rejected rather than silently migrating into an unselected root.
- Same-claim historical pairs may use `plan --dispositions ./reviewed-dispositions.json`. The JSON has `schema_version: "v1.0.0"` and `pairs`; each decision contains `id`, `canonical` and `archive` source/SHA-256 pins, `disposition: "canonical_with_archive_evidence_review_required"`, and a review `reason`. Claims, scope, category, conflicts and relations must match; all legacy inputs must be covered. Divergent facts and Markdown upgrades need separate review.
- This disposition keeps the original ID, renders the canonical version with `review_required: true` (excluded from retrieval), and retains both original JSON versions byte-for-byte. The Markdown History and manifest link the archived version without merging its provenance or claims. A later promotion is a separate reviewed operation.
- Production execution additionally requires `migrate --dispositions ./reviewed-dispositions.json --approved-dispositions-sha256 EXACT-SEPARATELY-APPROVED-HASH --maintainer`. The hash is not approval by itself. Source/decision/inventory drift fails closed; repeated runs retain the same decision binding. Verification rereads that file and both sources, and archive evidence cannot be a rollback deletion target.
- Original JSON bytes are retained in `memory/legacy-json/` without overwriting different evidence. Pre-upgrade Markdown bytes remain in `memory/migration/backups/`.
- The manifest is `memory/migration/manifest.yaml`; verification reports are in `memory/migration/reports/`. Repeated migration retains ownership/hash receipts and protects human edits.
- Rollback removes **one** unchanged, manifest-created Markdown file per invocation. It never deletes legacy JSON, technical protocols or pre-existing Markdown. Select each file explicitly; batch deletion is disabled. Modified targets and removals that would create dangling relations return protected; remove relations through approved proposals first. Upgrade backups remain for reviewed manual recovery.
- Fixture validation does not mean production user data was migrated. Do not run production examples merely to obtain a passing test badge.

## Optional Obsidian direct-root workbench

Choose **Open folder as vault → this checkout's `memory/markdown/`**. No nested vault or plugin is required. Optional `.obsidian/` GUI settings are not runtime memory input.

The Markdown index initializer and generated-index writer emit UTF-8 with LF line endings on every platform. Initialization preserves existing files. File hashes and proposal approvals still bind exact bytes; investigate line-ending drift rather than normalizing hashes or rewriting historical receipts.

For browsing, set Obsidian's **Editor → Default view for new tabs → Reading view** and switch existing editing tabs to Reading view individually. **Strict line breaks** controls rendering, not LF/CRLF storage. Reading view reduces accidental editing but is not filesystem write isolation; retain the approved-writer boundary and exact-byte drift checks.

Setting `paths.obsidian_vault`, creating `.obsidian/`, or passing `check_obsidian.py` does **not** register the folder with the desktop app. Confirm the app actually opens this exact folder, not the whole `memory/` protocol store. When retiring an old `obsidian-vault`, first review captures, settings and generated-file ownership; then remove its application registration and obsolete projection selection through an authorized maintenance path. Preserve its files until individually reviewed. A CLI refusing the old path does not prove that the app has stopped using it.

```powershell
python scripts/sync_obsidian.py check
python scripts/sync_obsidian.py sync --source-generation manual
python scripts/check_obsidian.py
# Requires an existing canonical note; substitute its actual category and ID:
python scripts/sync_obsidian.py uri --note project/example-note.md
```

These commands use the configured absolute vault root. For an explicit Obsidian `--memory-root`/`--vault` override, supply an absolute path; a relative override is not accepted by the vault boundary.

Default direct-root sync/check validates the canonical files without creating `views/`, a second `index.md` or a projection manifest. URI returns a validated open-only link using an encoded absolute file `path`; it does not launch the GUI. Register the containing vault in Obsidian first. Do not pass an absolute directory as the `vault` name. See [Obsidian URI](https://help.obsidian.md/uri). Proposal cards live in `review/`; human captures may live in `inbox/`. `sync_obsidian.py import-inbox --note capture.md --reviewed-personal` imports one reviewed low-risk new note. Existing-note changes still require proposals.

### Graph and internal links

- Generated `MEMORY.md` links use vault-relative category paths, such as `[[project/example-note|example-note]]`, rather than a bare filename that can collide with an old `views/` copy. Legacy ID-only links remain readable. Canonical body links can use an ID or an exact `category/id` path, with optional `.md`, heading and display alias; doctor and the writer validate the actual category and active target.
- After upgrading the index writer, explicitly run `python scripts/rebuild_registry.py` in maintenance scope. This rebuilds derived navigation, not memory claims or relation approvals. Ordinary read/check/sync never repairs links or changes graph settings.
- If historical files crowd the graph, use the Graph view search filter below. It hides non-knowledge folders in that view without deleting, moving or promoting their contents. This filter is not a privacy boundary or the active-retrieval rule; category notes awaiting review can still be visible.
- In the running Graph view, enable **Filters → Existing files only** and disable **Tags** and **Attachments** for note-only browsing. Keep **Orphans** enabled so genuinely unlinked notes remain visible. Under **Display**, lower **Text fade threshold** if labels disappear at the current zoom; zoom in to inspect individual notes. See the [official Graph view guide](https://help.obsidian.md/plugins/graph).

```text
-path:archive/ -path:views/ -path:review/ -path:proposals/ -path:inbox/ -path:index.md
```

The index supplies navigation edges, not evidence of semantic relationships. Mei stores typed reciprocal relations as validated IDs in YAML; plain IDs and relation objects are **not** Obsidian wikilinks. Use `python scripts/memory.py relations traverse example-note` for that logical graph. A note without real internal links can legitimately appear isolated; no code infers links from filenames or promotes archived evidence to make the graph look connected. Existing-note/body or relationship changes still require a reviewed proposal.

Check the actual open vault, click an index link, inspect Backlinks and reopen Graph view when verifying a repair. A passing file check or a saved `.obsidian/graph.json` does not prove the running app loaded the change. Reload the Mei vault in Obsidian if needed; no CLI, remote-debugging port or community plugin is required for normal use.

Explicit external-vault/namespace projection arguments are legacy compatibility interfaces, not the v2 default. Their generated index and body links target the selected vault-relative `views/` namespace, preserving headings/aliases and leaving canonical source bytes unchanged. A canonical vault cannot be another root's projection target or contain a nested projection namespace. Historical generated files are not automatically deleted or overwritten. Inspect ownership and drift before retiring any such file individually.

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

### Contributing

Keep changes scoped and add a synthetic regression for nontrivial behavior. Read the relevant policy and schema before changing a writer. Test fixtures must not contain personal memory, raw sessions, local paths or credentials. Do not publish `local/`, live memory, generated registries, trajectories, host configuration or screenshots. See [the public-path checker](scripts/check_public_git.py) and [third-party notices](THIRD_PARTY_NOTICES.md); do not introduce a dependency or license claim without reviewing it. No hosted CI status or native-GUI acceptance is implied by the commands below.

```powershell
python -X utf8 -B -m unittest discover -s tests -v
python scripts/validate_repository.py
python scripts/check_public_git.py
git diff --check
git diff --cached --check
```

Coverage includes reader/writer contracts, duplicate-key/schema checks, capability boundaries, GUI-free proposal lifecycle, paired-write rollback/stale handling, protected migration/rollback, untouched technical protocols, continuity concurrency/completion and host adapters. Windows new-file publication uses non-replacing rename rather than requiring hardlink privilege; concurrent targets remain protected. Registry generation and validation use the configured canonical root. Synthetic evidence is retained in ignored `.tmp/` rather than batch-deleted. Report denied native junction/symlink fixture setup and skipped checks explicitly; do not weaken reparse validation to obtain a passing result. Production data, native hook trust, real compact/cold starts and Obsidian Graph/Backlinks require separate acceptance.

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
