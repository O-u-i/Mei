# Trajectory policy

An eligible unit is a completed real project task, significant debugging result or failure mode, reusable procedure, explicit user correction, or architecture decision. Ordinary factual Q&A is not eligible. One eligible task produces at most one trajectory ID.

Export only a compact task summary. Use the schema fields to record what happened and how it was checked. Do not export raw conversations, private ChatGPT bodies, credentials, account identifiers, financial records, environment variables, or tool output dumps. Redact paths and names when they are not needed for reuse. A regex scan is only a backstop; the author must review privacy before writing.

An explicitly selected continuity writer may save a compact handoff from the
current agent's reviewed fields (`goal`, decisions, constraints, blockers,
next step, and bounded references). It must not read a transcript or persist
chat text. A long-term candidate may be written only to the isolated proposal
namespace as a draft; it is not a trajectory, candidate, canonical record, or
retrieval input. A `Stop` hook may make one status check using the host's
`stop_hook_active` guard and request the current agent to save missing state; it
never starts another model and never loops.

Project sessions may create one new JSON file in `trajectories/inbox`. They may not edit an existing inbox entry, normalize it, or change candidates, canonical records, registries, policies, or skills. Invalid entries stay in inbox until a Curator explicitly rejects or fixes that one file with evidence. After a final promotion or rejection decision, the Curator archives the one source trajectory and its normalized copy with `scripts/archive_trajectory.py`, records the reason locally, and updates source locators. Normalized copies are retained as review evidence under `trajectories/archive/normalized`, including when canonical memory cites one. Unresolved entries remain in inbox. No background watcher runs in the current release.
