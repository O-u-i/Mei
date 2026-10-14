# Permissions policy

The Codex `mei-project` profile is the ordinary project mode: the project workspace remains writable, Mei governed records and policies are read-only, and the only current Mei write exception is creating one eligible redacted trajectory in `trajectories/inbox`. A future, separately selected personal-memory writer may write one validated Markdown note under the configured `memory_root` after its Phase 6 interface and profile are implemented; that writer cannot touch governed JSON, registries, continuity, credentials, or host runtime state. Existing inbox items, `memory/canonical`, `memory/candidates`, registries, policies, schemas, continuity, and personal skills remain outside ordinary project write scope. A normal project must never switch to `mei-maintainer` merely to bypass a denied write.

The explicitly selected Codex `mei-maintainer` profile is for a scoped Mei maintenance task. It may write the Mei checkout and the user-selected installed skill directory only within that task, while Curator review, provenance, conflict checks, validation, and explicit promotion decisions still apply. It does not authorize other projects, credentials, system permissions, or automatic promotion.

The installed personal skills directory is independent of `codex_home`; record its machine-specific location as `skills_root` in Git-ignored `local/paths.toml` and the local skill registry. The repository validator checks that active skill pointers resolve under the configured `skills_root`, not that it equals `codex_home/skills`. GUI and TUI may use different host homes and memory-source paths, but both resolve the same Mei `memory_root` and continuity root.

`codex_home_gui` and `codex_home_tui` are host-owned runtime roots. Authentication,
capability tokens, configuration, active SQLite databases, locks, sessions,
caches, plugins, and attachments remain there. A memory migration may read
classified Markdown source files through `codex_memory_source_gui` and
`codex_memory_source_tui`, but it does not move or rewrite either host home.

These are Codex command/filesystem boundaries. Verify each host's effective boundaries separately before assuming equivalent enforcement. Approvals, prompts, browser/connectors, and escalated operations have separate controls. `AGENTS.md` and session naming are behavior guidance, not filesystem isolation. Select the intended Codex mode explicitly, confirm the workspace and paths in a fresh session, and describe only enforcement that has been verified. Keep machine-specific verification evidence local.

Machine-specific roots and migration state belong in Git-ignored `local/paths.toml` and `local/pi-permissions-migration.md`. Follow the repository rule against batch deletion; remove only one explicit file per action when necessary.
