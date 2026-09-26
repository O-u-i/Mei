# Skill policy

Active personal skills live under the user's Codex skills directory, for example `<codex-home>/skills/<name>/SKILL.md`. The local `local/paths.toml` records the actual directory, and the Git-ignored `registry/skills.yaml` is the Mei index. Files inside Mei `skills/candidates` or `skills/archive` are review artifacts, never assumed to be active Codex skills.

Use a skill for a repeatable workflow that changes an agent's decisions. Keep `name` and `description` discriminating, scope instructions to the trigger, and put background details in optional references. A prior memory record is evidence for a proposed skill, not proof that the workflow works. Promotion requires explicit maintainer work, format validation, and a realistic behavior check. No automatic skill mining in V0.
