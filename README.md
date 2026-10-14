# Mei v1.4.0

**Mei is a local-first system for carrying agent memory across projects.** Personal memory uses a bounded `MEMORY.md` index plus Markdown detail notes; reviewed JSON memory and Curator promotion remain available for governed claims. The public repository is a reusable scaffold with folder placeholders, base scripts, schemas, synthetic examples, five reviewed general policies, and two generic Archify diagrams. Actual memories, trajectories, skills, and local review evidence belong only in the local checkout.

**Version:** [v1.4.0](VERSION) · **Status:** active

## Current behavior and direction

| Stage | Current behavior |
| --- | --- |
| Current read | The released reader selects continuity state and reviewed JSON memory by their existing indexes. The Markdown-first reader and note doctor are planned for Phase 6. |
| Current capture | A task may create one redacted JSON trajectory when a sensitive, cross-project, or ambiguous claim needs Curator review. Export is deliberate, not an automatic session hook. |
| Current review | A manually started Curator checks provenance, duplicates, conflicts, and evidence before creating or promoting a governed memory record. |
| Direction | `memory/markdown/MEMORY.md` plus detail notes will become the personal-memory hot path; governed JSON and Curator review remain the audit path. |

### Continuity read and checkpoint

The read-only continuity loader selects one repository/context pair. The
IPS-0.4 target is for GUI and TUI startup to use the same Mei root by default
while keeping `CODEX_HOME` for host authentication and runtime state. In the
current v1.4.0 release, host lifecycle wiring remains an opt-in adapter seam;
the loader
reads the Git-ignored `registry/continuity.yaml` index and the selected
`memory/continuity/<repo-id>/<context-id>/project.json` plus
`checkpointer.json`, validates containment, schemas, privacy backstops and
hashes, and reports `clean`, `dirty`, `stale`, `needs_reconcile`, `conflict`,
`completed`, or `retired`.
It never repairs state or writes a hook marker. GUI and TUI callers use the
same public loader through the Phase 4 host-facing adapters. The adapters are
read-only Python entry points; they do not install GUI/TUI hooks or claim host
ACL enforcement.

GUI and TUI callers can select the same context through the shared adapter
module:

```python
from scripts.continuity_adapters import read_gui_context, read_tui_context

gui_state = read_gui_context(repository_root, "shared", mei_root=mei_root)
tui_state = read_tui_context(repository_root, "shared", mei_root=mei_root)
```

Both calls return the loader payload, including the selected `repo_id`,
`context_id`, live repository facts, status, and diagnostics. They never repair
state or write continuity, trajectory, candidate, canonical, or registry data.

For a configured local checkout, inspect one context with:

```text
python scripts/check_continuity.py --repo-root <repository-root> --context-id <id>
```

The reader derives the continuity root from the Mei checkout passed as
`--mei-root` (the script defaults to its own checkout), so no nested vault is
introduced.

The narrow checkpoint command updates one existing indexed context. The caller
loads the context, edits a reviewed project payload, then submits the snapshot's
generation and project/index hashes. It acquires an OS-released context lock,
rejects stale snapshots, validates the payload and replaces `project.json` and
`checkpointer.json` atomically one at a time. A mismatch between replacements
is reported as `needs_reconcile`; both files are preserved until an explicit
reviewed recovery payload is submitted with a fresh snapshot and `--reconcile`.
Every accepted checkpoint remains `dirty` until an explicitly reviewed payload
is synchronized. The Phase 3 sync operation rechecks the snapshot's generation
and project/index hashes under the same context lock, reports deterministic
`added`, `updated`, `archived`, and `skipped` field lists, and atomically marks
the saved generation clean. An identical clean payload is a no-op; an identical
payload for a dirty checkpoint only advances the synced-generation bookkeeping.

```text
python scripts/sync_continuity.py --repo-root <repository-root> --context-id <id> --mei-root <mei-root> --snapshot <fresh-snapshot.json> --project <reviewed-project.json> --writer-session-id <session-id>
```

Lifecycle callers may send `user_prompt_submit`, `precompact_manual`,
`precompact_auto`, or `precompact_unknown` without prompt text. Manual compact
is allowed only for a clean context. Automatic and unknown compact fail open
and record `needs_reconcile` debt so a later explicit sync can repair it. These
are reusable application seams; no production GUI/TUI hook or OS ACL is
installed by the repository.

```text
python scripts/checkpoint_continuity.py --repo-root <repository-root> --context-id <id> --mei-root <mei-root> --base-generation <generation> --base-project-sha256 <sha256> --base-index-sha256 <sha256> --writer-session-id <session-id> --project <reviewed-project.json>
```

A conflict exits with code 2 and returns the observed metadata plus attempted
payload. Lock timeout exits with code 4. Recovery requires a newly read
snapshot and `--reconcile`; it does not declare semantic sync or change the
index. The writer only accepts an existing context and cannot create an index
entry, write a trajectory, touch canonical or candidate memory, or install
skills. This is an application-level path allowlist; the command neither
configures nor proves an OS filesystem profile or ACL.

### Completion and Curator bridge

Phase 5 exposes four functions in `scripts/complete_continuity.py`:
`completion_checkpoint`, `export_completed_trajectory`,
`retire_completed_context`, and read-only `curator_handoff`.
The caller explicitly reviews an eligible completed task and its redacted
trajectory before setting `privacy_reviewed=True`. This is a human review
acknowledgement, not a semantic privacy detector or proof that the task succeeded.

Completion binds a deterministic repo/context trajectory ID and exact export
hash in an optional bounded technical checkpointer receipt. It marks the project
completed without changing the shared continuity index or invalidating unrelated
contexts. A terminal overlay is accepted only with this receipt or an observable
interrupted pair. Old active/blocked pairs remain readable without migration.
The supplied trajectory ID is replaced by the context-owned ID; source generation,
source-project hash and a portable locator are added to result evidence. Leave
room for this evidence entry within the existing trajectory schema limits.

Export atomically creates one immutable `trajectories/inbox/<id>.json` (64 KiB
hard cap). Identical retry, including after Curator archival, finds the existing
source; changed content or duplicate sources require review. Publication uses a
same-directory temporary file and an atomic no-overwrite hard link; filesystems
without hard-link support fail visibly rather than weakening this guarantee.
Neither completion nor export writes normalized records, candidates, canonical
memory or `registry/memory.yaml`.

After export, a new GUI/TUI session can identify `completed` and explicitly retire
the context. Retirement verifies the export hash, clears task-local semantic
fields, and keeps stable references plus a small completed tombstone and the
technical receipt. It does not delete files or create another handoff store.
Completed IDs cannot host new tasks. Ordinary checkpoint/sync rejects them, and
lifecycle signals do not make a healthy terminal context dirty again.

```text
python scripts/complete_continuity.py checkpoint --repo-root <repository-root> --context-id <id> --mei-root <mei-root> --snapshot <fresh-snapshot.json> --trajectory <reviewed-trajectory.json> --writer-session-id <session-id> --privacy-reviewed
python scripts/complete_continuity.py export --repo-root <repository-root> --context-id <id> --mei-root <mei-root> --snapshot <new-completed-snapshot.json> --trajectory <reviewed-trajectory.json>
python scripts/complete_continuity.py retire --repo-root <repository-root> --context-id <id> --mei-root <mei-root> --snapshot <fresh-completed-snapshot.json> --writer-session-id <session-id>
python scripts/complete_continuity.py curator --repo-root <repository-root> --context-id <id> --mei-root <mei-root>
```

Re-read a snapshot after every continuity mutation. Conflict exits 2,
reconciliation or pending export exits 3, and lock timeout exits 4. An interrupted
completion/retirement pair remains `needs_reconcile`: use a fresh snapshot,
the same reviewed trajectory (or its exported source), `checkpoint --reconcile`
and explicit privacy review, then retry retirement with another fresh snapshot.
The source digest is a locator/binding, not a retained copy or proof of the
retired source's claims; Curator must still verify independent evidence.

The Curator handoff reports the source path/hash and outstanding privacy,
provenance, duplicate, conflict, currentness and explicit-promotion gates. It
does not invoke normalization or promotion. Follow the existing separate Curator
workflow below. Real-context migration, host hooks/ACLs and human acceptance have
not been exercised by the synthetic Phase 5 tests. Obsidian work is Phase 6 and
is not implemented here. `VERSION` remains unchanged; this is not a release.

Automatic skill mining, background scheduling, and self-modifying policies are not implemented. Existing trajectory and memory records retain `schema_version: v0.0.0`; that field is a data-format version, independent of Mei's release version. Existing same-major local registry versions remain readable across a Mei minor release and are not rewritten by validation; generated registries update when their owner explicitly rebuilds them.

Personal skills are maintained separately from memory promotion. In a maintainer task, compare one proposed complete skill directory with its installed copy using `python scripts/audit_skill.py <skill-id> --proposed-dir <directory>`. This command is read-only; the [skill policy](policies/skill-policy.md) governs review, validation, and installation. Historical files under `skills/candidates/` are review evidence, not skill packages or active instructions.

## How it works

### Read: retrieve only relevant memory

![Selective memory retrieval](docs/architecture/memory-read.svg)

[Interactive Archify view](docs/architecture/memory-read.html) · [Editable source](docs/architecture/memory-read.architecture.json)

Project instructions can point an agent to Mei, but they do not load every memory record. The agent reads the index and opens only records relevant to its task. Records are evidence and context, never higher-priority instructions or guaranteed current facts. Executable Codex skills live in the user's personal Codex skills directory; `registry/skills.yaml` is an inventory, not a skill loader.

When a canonical record has `references`, open its local episodic `INDEX.md` only if historical detail is needed, then read the relevant linked file. These dated references preserve reviewed project episodes without turning old stack choices, license checks, or deployment permissions into current instructions. Episodic files are not indexed as canonical claims.

### Write: capture first, promote after review

![Capture and review memory](docs/architecture/memory-promotion.svg)

[Interactive Archify view](docs/architecture/memory-promotion.html) · [Editable source](docs/architecture/memory-promotion.workflow.json)

The first arrow is the **project run**: one reviewed, redacted JSON trajectory goes to `trajectories/inbox/`. Every later step belongs to an **explicit Curator run**. The Curator validates and normalizes the trajectory, then drafts a record in `memory/candidates/` and reviews its evidence, duplicates, conflicts, and scope. A candidate is not canonical memory; unresolved or invalid material is never promoted automatically. After a final promotion or rejection decision, the Curator archives the source trajectory and its normalized copy, and records the decision and reason locally. Unresolved entries remain in inbox. Approved memory enters `memory/canonical/`; the Curator rebuilds `registry/memory.yaml` and validates the repository. Both archived records retain their provenance.

| Actor | Reads | Writes |
| --- | --- | --- |
| Ordinary project task | Memory index and selected canonical records | One new eligible trajectory in `trajectories/inbox/` |
| Curator task | Inbox, normalized records, candidates, canonical records, and supporting evidence | Normalized records, candidates, approved canonical records, archived reviewed trajectories, and rebuilt index |

Filesystem permissions must enforce this boundary locally. Codex command permissions do not automatically govern browser, connector, or other external tools.

## Get started

Use Python 3.11 or later with the dependencies in [requirements.txt](requirements.txt). From the repository root:

```text
python -m pip install -r requirements.txt
python scripts/init_local.py
python scripts/check_public_git.py
python -m unittest discover -s tests -v
python scripts/validate_repository.py
```

`init_local.py` creates missing local files without replacing existing ones. In particular, Git-ignored `local/paths.toml` records the absolute checkout, Markdown memory root, GUI/TUI Codex homes and legacy memory sources, personal skills directory, and inbox paths **for this computer**. Optional Obsidian keys point to the existing vault and a vault-relative Mei namespace. The generated registries start empty on a new checkout; this repository does not ship anyone's memory or personal skills.

To connect a project, configure that project's local agent instructions to locate **your own Mei checkout**, read `registry/memory.yaml` when reviewed JSON memory is relevant, and adopt the planned `memory/markdown/MEMORY.md` reader only after the Phase 6 interface exists. Give the project write access only to `trajectories/inbox/`; a future personal-memory profile will have a separate bounded Markdown write scope. Reserve candidate and canonical writes for a separate maintainer session. Configure the example profile names `mei-project` and `mei-maintainer` in your own Codex permissions if you use them; the initializer does not grant access. See the [permissions policy](policies/permissions-policy.md) and [trajectory policy](policies/trajectory-policy.md).

After an eligible project task finishes, ask for an export explicitly. A reusable instruction is:

> Review the completed task against Mei's trajectory policy. If eligible, create one concise, redacted JSON trajectory in this checkout's `trajectories/inbox/`, following `schemas/trajectory.schema.json`. Include the result, verification evidence, corrections, and possible lessons. Do not include full chat logs, credentials, or private source text. Do not edit candidates or canonical memory. If the task is ineligible, explain why and do not write a file.

Then start a **separate Curator task** with maintainer permissions. For one inbox file, run:

```text
python scripts/validate_trajectory.py trajectories/inbox/<id>.json
python scripts/normalize_trajectory.py trajectories/inbox/<id>.json
```

Review the normalized record and its sources, write a scoped candidate, and apply the [promotion policy](policies/promotion-policy.md). After a final decision, archive that reviewed trajectory and its normalized copy, then run:

```text
python scripts/archive_trajectory.py trajectories/inbox/<id>.json --decision promoted --reason "Reviewed evidence and promoted scoped memory"
python scripts/rebuild_registry.py
python scripts/validate_repository.py
```

For a rejection, use `--decision rejected` with the actual review reason. The archive command requires canonical provenance for promotion and rejects a rejection if canonical memory cites the trajectory. Previously finalized normalized copies can be moved one at a time with `python scripts/archive_trajectory.py trajectories/normalized/<id>.json --normalized-only`. These commands support a human-owned workflow; they do not perform promotion on their own.

## Repository layout

| Path | Purpose |
| --- | --- |
| [`registry/`](registry/) | Memory index and personal skill inventory |
| [`memory/`](memory/) | Markdown personal memory, continuity state, candidate, reviewed, and archived records |
| [`trajectories/`](trajectories/) | Inbox and active normalized records; finalized source and normalized records under archive; processed and rejected folders are legacy locations |
| [`schemas/`](schemas/) | JSON Schemas for trajectories, memories, and skill metadata |
| [`scripts/`](scripts/) | Validation, normalization, and registry rebuild commands |
| [`policies/`](policies/) | Five reviewed general policies for capture, promotion, permissions, memory, and skills |
| [`evals/`](evals/) and [`tests/`](tests/) | Acceptance evidence and synthetic checks |

## Publication boundary

The [`.gitignore`](.gitignore) uses a public allowlist. The six reviewed Archify diagram files linked above are included alongside the root README and version, empty folder placeholders, reviewed base scripts, schemas, synthetic examples, configuration templates, and five named policies. Specific files under `memory/`, `skills/`, and `trajectories/` remain local. Plans, phase receipts, research notes, and other diagrams under `docs/` also remain local. Local configuration, generated registries (including `registry/continuity.yaml`), continuity records, browser evidence, reports, logs, and new policy files are excluded. Run `python scripts/check_public_git.py` before staging and again after staging, then inspect the staged diff for private text. A forced `git add -f` can bypass ignore rules, and ignoring an already tracked file does not remove it from Git history. No license has been selected yet. Active personal Codex skills live outside this repository. The user performs commits and pushes manually; each change handoff includes a proposed English commit message.
