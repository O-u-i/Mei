# Permissions policy

The Codex `mei-project` profile is the ordinary project mode: the project workspace remains writable, Mei canonical memory and policies are read-only, and the only Mei write exception is creating one eligible redacted trajectory in `trajectories/inbox`. A separately selected `mei-personal-memory` profile may create a reviewed low-risk Markdown note and its bounded index under `memory_root`; existing-note changes require a proposal and explicit maintainer approval. It cannot write legacy JSON, registries, continuity, credentials, or host runtime state. The CLI requires `--reviewed-personal` for direct low-risk creation. An existing-note update requires both the current base hash and a content-bound, explicitly approved proposal; a previous hash alone never authorizes an update.

A separately selected `mei-continuity-writer` profile grants only the explicitly
configured context directory, not the whole continuity tree or registry. Changing
that selection requires a reviewed profile update. Explicit creation/index rebase
is a maintainer operation, with a retained backup of the old locator and technical
records. Hook event writes require a selected writer profile as well as native
filesystem authorization. User-level hooks are discovered per host and trusted
by the user; never synthesize a trust hash or use a trust-bypass flag as acceptance.

The default Obsidian vault is `memory/markdown` itself. Direct-root sync and
drift checks are read-only: they never generate a second note projection or
need a projection writer. The optional legacy `mei-obsidian-projection` profile
starts from `:read-only`, grants
only the configured generated namespace, and explicitly makes its human inbox
read-only. Obsidian settings remain outside the grant. When the user selects
automatic lifecycle/projection assistance, hooks invoke these named native
sandboxes; an environment-variable profile name is not proof of authorization.
Personal semantic writes and governed promotion are never automatic hook work.
No helper may silently choose a broader profile after a denied write.

The explicitly selected Codex `mei-maintainer` profile is for a scoped Mei maintenance task. It may write the Mei checkout and the user-selected installed skill directory only within that task, while Curator review, provenance, conflict checks, validation, and explicit promotion decisions still apply. It does not authorize other projects, credentials, system permissions, or automatic promotion.

The installed personal skills directory is independent of `codex_home`; record its machine-specific location as `skills_root` in Git-ignored `local/paths.toml` and the local skill registry. The repository validator checks that active skill pointers resolve under the configured `skills_root`, not that it equals `codex_home/skills`. GUI1 and GUI2 may use different host homes and memory-source paths, but both resolve the same Mei `memory_root` and continuity root.

`codex_home_gui1` and `codex_home_gui2` are host-owned runtime roots. Authentication,
capability tokens, configuration, active SQLite databases, locks, sessions,
caches, plugins, and attachments remain there. A memory migration may read
classified Markdown source files through `codex_memory_source_gui1` and
`codex_memory_source_gui2`, but it does not move or rewrite either host home.
The previous `gui` and `tui` labels remain valid for historical evidence and
compatibility readers; current local bindings identify GUI1 and GUI2 explicitly.

These are Codex command/filesystem boundaries. Verify each host's effective boundaries separately before assuming equivalent enforcement. Approvals, prompts, browser/connectors, and escalated operations have separate controls. `AGENTS.md` and session naming are behavior guidance, not filesystem isolation. Select the intended Codex mode explicitly, confirm the workspace and paths in a fresh session, and describe only enforcement that has been verified. Keep machine-specific verification evidence local.

Machine-specific roots and migration state belong in Git-ignored `local/paths.toml` and `local/pi-permissions-migration.md`. Follow the repository rule against batch deletion; remove only one explicit file per action when necessary.

Continuity assistance may write only the selected context and, when a
separately configured draft writer is selected, the local `memory/proposals/`
namespace plus its generated Obsidian review cards. The draft writer cannot
write `memory/markdown`, `memory/candidates`, `memory/canonical`, registries,
policies, skills, `.obsidian`, or host runtime state. Keep that profile disabled
until its real native boundary is tested; the handoff CLI remains an explicit
manual entry point while it is disabled.
