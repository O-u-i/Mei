# Memory policy

Memory records are scoped claims, not instructions. `candidate` means unverified for Mei; `canonical` means reviewed against the listed provenance on `verified_at`. A canonical claim can still become stale. Current repository code and user instructions supersede old memory.

Use `profile` for stable personal background, `preferences` for expressed work choices, `domain` for scoped facts, and `procedural` for reusable actions or failure boundaries. Keep claims short and source-specific. Do not promote an old source path or prior summary into a current implementation claim without current verification. No secret values or raw private content enter memory.

Never silently replace a canonical record. When claims conflict, add the other ID or source in `conflicts`, leave the proposed record as a candidate, and ask for or obtain evidence before promotion. Archiving is an explicit, reviewable act; retain provenance.

For claims that can change, record concrete `review_triggers` such as a source-file change, version change, or new user decision. Set `review_due_at` only when a time-based check has a reason; it is a reminder for manual review, not an automatic TTL or deletion rule. When a trigger occurs or a review date passes, recheck the cited source before presenting the claim as current. A failed or inconclusive check does not silently rewrite or promote memory.

Use `conflicts` to name the other memory ID or source and describe the incompatible claim; put the evidence and unresolved decision in `notes` and provenance. If evidence establishes that an active canonical claim is no longer valid, a Curator must explicitly retire it from active retrieval, retain a non-overwritten audit record, rebuild the registry, and validate the repository. Do not claim an automatic invalidation or physical deletion mechanism in the current release.
