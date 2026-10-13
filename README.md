# Mei v1.1.0

**Mei is a local-first system for carrying reviewed agent experience across projects.** The public repository is a reusable scaffold with folder placeholders, base scripts, schemas, synthetic examples, five reviewed general policies, and two generic Archify diagrams. Actual memories, trajectories, skills, and local review evidence belong only in the local checkout.

**Version:** [v1.1.0](VERSION) · **Status:** active

## What works now

| Stage | Current behavior |
| --- | --- |
| Capture | A project task may create one redacted JSON trajectory in `trajectories/inbox/`. Export is a deliberate action, not an automatic session hook. |
| Check | Python scripts validate the schema, check for some secret patterns, and normalize a trajectory while retaining its source hash. Human privacy review is still required. |
| Review | A manually started Curator checks provenance, duplicates, conflicts, and evidence before creating a candidate memory. |
| Reuse | Approved records enter `memory/canonical/` and are indexed by `registry/memory.yaml` for selective, read-only retrieval by later project tasks. |

### Continuity read

The read-only continuity loader selects one repository/context pair. It
reads the Git-ignored `registry/continuity.yaml` index and the selected
`memory/continuity/<repo-id>/<context-id>/project.json` plus
`checkpointer.json`, validates containment, schemas, privacy backstops and
hashes, and reports `clean`, `dirty`, `stale`, `needs_reconcile`, or `conflict`.
It never repairs state, writes a hook marker, or touches candidates,
canonical memory, registries, or installed skills. GUI and TUI callers use the
same public loader. Host-specific adapters are not included.

For a configured local checkout, inspect one context with:

```text
python scripts/check_continuity.py --repo-root <repository-root> --context-id <id>
```

The reader derives the continuity root from the Mei checkout passed as
`--mei-root` (the script defaults to its own checkout), so no nested vault or
additional memory writer is introduced.

Automatic skill mining, background scheduling, and self-modifying policies are not implemented. Existing trajectory and memory records retain `schema_version: v0.0.0`; that field is a data-format version, independent of Mei's release version.

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

`init_local.py` creates missing local files without replacing existing ones. In particular, Git-ignored `local/paths.toml` records the absolute checkout, Codex home, personal skills directory, and inbox paths **for this computer**. The generated registries start empty on a new checkout; this repository does not ship anyone's memory or personal skills.

To connect a project, configure that project's local agent instructions to locate **your own Mei checkout** and read `registry/memory.yaml` only when relevant. Give the project write access only to `trajectories/inbox/`; reserve candidate and canonical writes for a separate maintainer session. Configure the example profile names `mei-project` and `mei-maintainer` in your own Codex permissions if you use them; the initializer does not grant access. See the [permissions policy](policies/permissions-policy.md) and [trajectory policy](policies/trajectory-policy.md).

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
| [`memory/`](memory/) | Candidate, reviewed, and archived memory records |
| [`trajectories/`](trajectories/) | Inbox and active normalized records; finalized source and normalized records under archive; processed and rejected folders are legacy locations |
| [`schemas/`](schemas/) | JSON Schemas for trajectories, memories, and skill metadata |
| [`scripts/`](scripts/) | Validation, normalization, and registry rebuild commands |
| [`policies/`](policies/) | Five reviewed general policies for capture, promotion, permissions, memory, and skills |
| [`evals/`](evals/) and [`tests/`](tests/) | Acceptance evidence and synthetic checks |

## Publication boundary

The [`.gitignore`](.gitignore) uses a public allowlist. The six reviewed Archify diagram files linked above are included alongside the root README and version, empty folder placeholders, reviewed base scripts, schemas, synthetic examples, configuration templates, and five named policies. Specific files under `memory/`, `skills/`, and `trajectories/` remain local. Plans, phase receipts, research notes, and other diagrams under `docs/` also remain local. Local configuration, generated registries (including `registry/continuity.yaml`), continuity records, browser evidence, reports, logs, and new policy files are excluded. Run `python scripts/check_public_git.py` before staging and again after staging, then inspect the staged diff for private text. A forced `git add -f` can bypass ignore rules, and ignoring an already tracked file does not remove it from Git history. No license has been selected yet. Active personal Codex skills live outside this repository. The user performs commits and pushes manually; each change handoff includes a proposed English commit message.
