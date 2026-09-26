# Trajectory policy

An eligible unit is a completed real project task, significant debugging result or failure mode, reusable procedure, explicit user correction, or architecture decision. Ordinary factual Q&A is not eligible. One eligible task produces at most one trajectory ID.

Export only a compact task summary. Use the schema fields to record what happened and how it was checked. Do not export raw conversations, private ChatGPT bodies, credentials, account identifiers, financial records, environment variables, or tool output dumps. Redact paths and names when they are not needed for reuse. A regex scan is only a backstop; the author must review privacy before writing.

Project sessions may create one new JSON file in `trajectories/inbox`. They may not edit an existing inbox entry, normalize it, or change candidates, canonical records, registries, policies, or skills. Invalid entries stay in inbox until a Curator explicitly moves the one file to rejected or fixes it with evidence. No background watcher runs in V0.
