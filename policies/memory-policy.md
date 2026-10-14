# Memory policy

Mei has two memory modes. Personal Markdown notes under the configured
`memory_root` are the low-friction hot path for one user's preferences,
procedures, feedback, and scoped project notes. Governed JSON records under
`memory/candidates/` and `memory/canonical/` are the auditable promotion path
for sensitive, cross-project, ambiguous, or otherwise high-risk claims.

Both modes contain scoped claims, not instructions. A Markdown note is not a
governed claim merely because it exists. For governed JSON, `candidate` means
unverified for Mei; `canonical` means reviewed against the listed provenance on
`verified_at`. A governed canonical claim can still become stale. Current
repository code and user instructions supersede old memory.

Use `profile` for stable personal background, `preferences` for expressed work choices, `domain` for scoped facts, and `procedural` for reusable actions or failure boundaries. Markdown notes may additionally use `project` and `feedback` scopes. Keep claims short and source-specific. Do not promote an old source path or prior summary into a current implementation claim without current verification. No secret values or raw private content enter memory.

`MEMORY.md` is a bounded index, not a transcript or a second full corpus. A
personal writer may update one note in place only after checking its scope,
stable ID, frontmatter, size, links, and privacy. Archive or supersede an
incorrect note explicitly; do not silently rewrite a conflicting claim. A note
whose risk exceeds personal mode is marked for governed review rather than
being treated as verified.

For a reviewed project episode, keep concise current or scoped claims in governed canonical `domain` and `procedural` records. Personal Markdown notes may point to a project or feedback detail file, but that note is not a substitute for governed provenance. Governed records may list `references` to a local `memory/episodic/<episode-id>/INDEX.md`. Read that index and only the relevant detail files when the task needs historical context. Episodic references are dated evidence, not another canonical category, current project state, executable skills, or standing authorization. Preserve the source trajectory and mark unverified or time-bound details clearly. The reference directory remains local and Git-ignored.

Never silently replace a canonical record. When claims conflict, add the other ID or source in `conflicts`, leave the proposed record as a candidate, and ask for or obtain evidence before promotion. Archiving is an explicit, reviewable act; retain provenance.

For claims that can change, record concrete `review_triggers` such as a source-file change, version change, or new user decision. Set `review_due_at` only when a time-based check has a reason; it is a reminder for manual review, not an automatic TTL or deletion rule. When a trigger occurs or a review date passes, recheck the cited source before presenting the claim as current. A failed or inconclusive check does not silently rewrite or promote memory.

Use `conflicts` to name the other memory ID or source and describe the incompatible claim; put the evidence and unresolved decision in `notes` and provenance. If evidence establishes that an active canonical claim is no longer valid, a Curator must explicitly retire it from active retrieval, retain a non-overwritten audit record, rebuild the registry, and validate the repository. Do not claim an automatic invalidation or physical deletion mechanism in the current release.
