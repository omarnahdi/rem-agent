# Rem proactive memory (beta)

## Goal

Agno decides which facts deserve long-term retention. Mem0 OSS 2.2.1 stores and semantically retrieves those already-curated facts. PostgreSQL stores the authoritative owner/scope, source evidence, and active/superseded/deleted state. Agno's separate native `MemoryManager` is not enabled, so Rem does not accidentally maintain two independent long-term memory stores.

## Runtime flow

1. The Discord router/moderation gate decides whether a message is eligible for Rem.
2. `RemMemoryService.prepare_message()` loads scoped memories for the current speaker and guild. Those memories can be included in the current answer.
3. Eligible raw message evidence is stored for up to `REM_MEMORY_EVENT_RETENTION_DAYS` (default 30 days). Obvious credentials, matched sensitive-personal-detail patterns, URL-only messages, and command-only messages are rejected before raw evidence storage. This is heuristic screening, not a guarantee that every sensitive phrase is recognized.
4. A lightweight gate sends preference/project/goal/event-like statements, longer messages, and periodic messages to the Agno curator. It returns zero to three structured proposals.
5. Python validates every proposal. Mem0 is called with `infer=False` so Mem0 stores the fact selected by Agno rather than running a second independent extraction process.
6. Only after Mem0 confirms an inserted ID is the metadata row/evidence committed. For updates, the replacement is stored before the old row is marked superseded. PostgreSQL state and scope are checked on recall, so stale vectors are not exposed.

## Scopes

- `user`: keyed by the current author's numeric Discord user ID.
- `community`: keyed by the current guild ID.
- Session history: managed independently by Agno; it is not a long-term memory record.
- Character rules: maintained in the Rem persona/character-bible files, not learned from chat.

Private user memory searches and mutations are filtered by that author's scope in the service layer. Explicit community-wide writes, updates, and forget operations are Master-only. Automatic curation may propose a shared fact when the evidence is non-sensitive and clearly server-relevant; Python still validates its category and scope.

## Retention and operations

- Raw message evidence is retained for 30 days by default. Set `REM_MEMORY_EVENT_RETENTION_DAYS` to adjust it (1–365 days).
- Curated stable memories do not expire automatically. The curator can assign an expiry of 1–90 days for temporary plans/events; users can request a specific memory be forgotten through the registered tools.
- The beta uses `REM_MEMORY_COLLECTION=rem_agno_beta_memory_index`, separate from legacy `rem_memory_index`. The service refuses to start if configured with the legacy collection.
- The beta may reuse the existing Neon branch: beta-specific SQL table names and the separate `rem_agno_beta_memory_index` Mem0 collection keep memory namespaces distinct. Leave `REM_MEMORY_DATABASE_URL` blank to reuse `NEON_DATABASE_URL`; set it only if you later want the memory service to use a different database. `AGNO_DATABASE_URL` configures Agno session history separately.
- Existing legacy Rem memories/profiles are not imported automatically.

## Local verification

```powershell
uv sync
uv run python -m unittest discover -s tests -v
```

Then use a test Discord application/server and isolated beta database. Confirm in logs that the memory schema and Mem0 client initialize successfully. Send a message with a durable preference/project (without saying "remember"); after the background curation finishes, search for it in a later turn. Test two users with different facts and verify cross-user retrieval is impossible; test update/forget and a vector-store failure before inviting all members.

The repository's pure policy/helper tests can run without installing provider SDKs. They do **not** prove compatibility with a live Agno/Mem0/PostgreSQL/Gemini/Discord environment. A real integration smoke test remains required before public rollout.

## Current limitations

- The initial curation gate prioritizes explicit value cues, longer messages, and periodic review; it is a budget-conscious heuristic, not a guarantee that every important fact will be detected.
- Existing legacy memories and curated community profiles are not migrated.
- Mem0 and PostgreSQL are separate systems, so failures can leave orphan vectors or cleanup work pending. SQL scope/status verification prevents unrecognized or inactive vectors from being shown to Rem, but operational reconciliation and fault-injection tests remain important.
- Secret/sensitive-data filters are heuristic. Tell beta members how Rem's memory works and how to ask her to forget a stored fact.
