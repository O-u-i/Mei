# Permissions policy

The Codex `mei-project` profile is the ordinary project mode: the project workspace remains writable, Mei canonical memory and policies are read-only, and the only Mei write exception is creating one eligible redacted trajectory in `trajectories/inbox`. A separately selected `mei-personal-memory` profile may create a reviewed low-risk Markdown note and its bounded index under `memory_root`; existing-note changes require a proposal and explicit maintainer approval. It cannot write legacy JSON, registries, continuity, credentials, or host runtime state. The CLI requires `--reviewed-personal` for direct low-risk creation. An existing-note update requires both the current base hash and a content-bound, explicitly approved proposal; a previous hash alone never authorizes an update.

Continuity enrollment binds one explicit repo/context and host/session writer in
Mei's local attachment. It creates no persistent context, lifecycle or diagnostics
profiles in either host configuration. Every enrolled native worker receives a command-local
`mei-worker` grant extending `:read-only`, with exactly one writable context
directory (or `local/hook-events` for metadata diagnostics) and network disabled.
Old attachment profile fields are compatibility data, not sandbox selectors.
No denied write may fall back to ambient access or a broader grant. Writer
identity, locks, CAS, validation and explicit clean-state takeover remain required.
Explicit creation/index rebase is a maintainer operation with technical backups.
User-level hook discovery and trust remain native user actions; never synthesize
trust hashes or use a trust-bypass flag as acceptance.
Legacy unenrolled lifecycle opt-in retains its explicitly configured named grant;
it is never reinterpreted as permission to write a newly selected context.

The default Obsidian vault is `memory/markdown` itself. Direct-root sync and
drift checks are read-only: they never generate a second note projection or
need a projection writer. Automatic external projection is retired; external
projections use the explicit Obsidian sync entrypoint. The old
`mei-obsidian-projection` and `mei-continuity-writer` menu profiles are no longer
required. An environment-variable profile name is not proof of authorization.
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

Continuity hooks write only technical lifecycle state in the selected context;
they never write semantic memory or proposals. Optional memory drafts remain
an explicit handoff operation with separately authorized proposal access, not
a profile created by setup or attach.
