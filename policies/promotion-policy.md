# Promotion policy

Markdown/YAML is the canonical memory source. Legacy JSON is read-only migration evidence; only a deliberately invoked Curator using `mei-maintainer` may apply changes to existing notes.

Only a deliberately invoked Curator using `mei-maintainer` may apply an approved change to an existing Markdown note. For each proposal: validate privacy and provenance; compare scope and claim against active notes; check duplicate IDs and semantic conflicts; record the review decision. For mutable claims, set concrete `review_triggers`; record unresolved contradictions in `conflicts` rather than overwriting them. The Curator may use Codex to draft, but a human-owned Curator task makes the decision.

New or changed notes use `memory/proposals/<proposal-id>.md`; `apply` requires preview, current base hash, explicit approval, approved-content hash, atomic write, and an audit receipt. Unapproved material stays in proposals. Legacy JSON remains byte-preserved under `memory/legacy-json/` during migration and is excluded from retrieval.
