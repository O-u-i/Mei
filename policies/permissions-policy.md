# Permissions policy

The default `mei-project` Codex profile permits ordinary workspace edits, read access to Mei, and write access to `trajectories/inbox`. It explicitly keeps `memory/canonical`, registries, policies, schemas, and personal skills outside ordinary write scope. The separate `mei-maintainer` profile is selected for Mei curation and skill maintenance. A normal project must never switch to maintainer simply to avoid a denied write.

This separation assumes an ordinary project has its own workspace root outside the Mei checkout. The checkout's optional local Codex config defaults its own root to `:read-only`; select `mei-maintainer` explicitly for curation. Do not run an ordinary project task with Mei itself as its writable workspace root. Verify both profiles from their intended roots after changing ACLs or Codex configuration. Machine-specific roots belong in the Git-ignored `local/paths.toml` and personal Codex config.

These permissions govern local sandboxed commands. Browser, Computer Use, MCP, connectors, cloud tasks, and escalated operations have separate controls. `AGENTS.md` is a behavioral rule; it is not a filesystem ACL. Verify effective permissions in a fresh session after config changes. Follow the repository rule against batch deletion; remove only one explicit file per action when necessary.
