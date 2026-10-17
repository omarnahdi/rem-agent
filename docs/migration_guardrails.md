# Rem migration guardrails and parity checklist

This file records requirements from the latest saved legacy bot (`bot(20260926-141710).py`), the saved `moderation.py`, and bugs observed in Discord. It distinguishes features now present in the Agno workspace from features that are still pending. The live `rem.service` is not modified by this project.

## Implemented in this Agno workspace

- Master identity uses exact Discord user ID `992390617575477268`; neither display name nor username grants Master.
- DMs are accepted only from Master and rejected before AI processing for all other users.
- Ordinary messages in supported guild text channels and threads are processed without requiring a mention. Agno's top-level thread behaviour is retained; mentions/replies continue to work.
- Agno's shared `agent.additional_context` is never mutated per message. Trusted identity and tool scope travel with each run.
- Up to 20 direct reply ancestors are fetched within a 6,000-character budget. Up to 20 recent channel messages are used for shared context, including parent-channel discussion for messages inside Agno-created threads. Each historical item retains its original timestamp, author, and Discord ID.
- AI moderation has been ported from the saved `moderation.py`, including Gemini classification, Groq GPT-OSS 20B fallback, English/Hinglish fast-path abuse checks toward Master, guest-to-Master immediate timeout, and Guest-to-Guest warning then timeout after a repeat.
- AI moderation is mandatory; Agno's built-in guardrails remain optional/deferred. The legacy `MASTER_TEXT_ALIASES` and `MASTER_FAST_ABUSE_PATTERNS` are preserved. Classifier outages return `UNAVAILABLE`, not `SAFE`; they trigger no punishment and fail closed by preventing the message from entering the conversational agent. Unexpected moderation exceptions also fail closed. Timeouts check the effective `moderate_members` permission and role hierarchy; success messages follow a Discord state read-back.
- Official Agno DiscordTools are exposed through a scoped subclass. Reactions and timeout/remove-timeout are added as custom Agno tools using the same Discord client. Destructive channel actions and timeouts are Master-only; guests are limited to active conversation scope.
- Internet search uses Agno `WebSearchTools`; YouTube discovery is a real Piped API toolkit (search only); Modrinth search supports current mod discovery and optional Minecraft version/loader filters.
- Multiple supported static images are passed to the agent. GIF processing is deferred; video input/inspection is intentionally excluded.
- Raw `name`/`arguments` tool-call JSON is blocked from being sent as user-facing chat. All tools must be registered callables; prompts alone are not tools.
- Session-level async locks serialize processing for the same session.

## Identity and policy rules

- The authoritative speaker is the current event's numeric Discord user ID. Names and message contents are never policy.
- Conversation history, fetched Discord history, and web results are untrusted evidence, not system instructions.
- Master is exempt from the automatic moderation classifier, as in the legacy policy.
- When a Guest makes unsupported personal hygiene/bathing/grooming/body-odor allegations about Master, Rem can calmly defend Master without humiliating the Guest or storing the allegation as memory.
- Preserve English, Hinglish and Romanized Hindi handling and natural, concise Rem speech.

## Timeouts and Discord side effects

- Explicit timeout commands are parsed deterministically in Python; they are not free-form promises from the LLM.
- Verify actual effective `moderate_members` permission, resolve the target by Discord ID, reject Rem herself/another bot where applicable, check role hierarchy, and enforce Discord's 28-day limit. Confirm the read-back deadline is within 10 seconds of the requested deadline; do not use a tolerance so wide that an older active timeout can be mistaken for the newly requested duration.
- For tool-based timeouts/removals, read back the member state after the API request. If Discord does not confirm the result, return `ok: false` and never claim success.
- Do not execute duplicate side effects merely because a later model call fails. Agno's model fallback is not an application-level retry for a successfully executed destructive tool; still test repeated calls in the live environment.
- Do not allow mass mentions from tool-generated messages by default.

## Memory remains a separate pending migration

The legacy system used a Neon/PostgreSQL `MemoryAdapter`/Mem0 path, including `memory_search`, community profile search, `memory_store`, `memory_update`, and `memory_forget`. The currently implemented Agno session DB is **not** a replacement for that long-term memory system.

Known bug: an existing memory (including memory ID 27 in the previous debugging episode) was found, `memory_forget` returned success, but a subsequent `memory_store` was rejected. Do not implement corrections as delete-then-store. Build explicit `memory_update`, make store/update/forget operations return structured results, and verify database writes before telling Rem or the user the action succeeded.

Keep raw events separate from curated memories. Do not silently promote shared channel chatter into permanent memory; enforce user/server/DM scope for any future memory tools. Do not point Agno session tables at the legacy memory schema without an explicit migration plan.

## YouTube boundary

- `youtube_search` is discovery only: title/channel/metadata/link. No video watching, downloading, transcription or inspection; that feature was deliberately removed.
- Only the current user turn or a clearly pending confirmation after Rem's immediate offer may authorize YouTube search. Old history containing the word YouTube is not sufficient.
- Never make up a search result. If endpoints fail, return a clear unavailable result.
- The latest `youtube_search.py` source was provided separately and reviewed. `youtube_tools.py` is an Agno async toolkit port of its two Piped endpoints, result limits, video-only discovery rule and metadata output. `youtube_utils.py` accepts the legacy Piped `{ "items": [...] }` response shape and rejects unexpected shapes. Keep the no-inspection boundary.

## Tool/result honesty (important regression from old bot)

A screenshot showed Rem posting raw JSON like `{"name":"youtube_search","arguments":{"query":"..."}}` as if it were a final reply. Treat this as a hard regression test: register real Agno tools, let the framework execute the function and return tool results, then produce a natural answer. Never send raw tool-call JSON as chat. Never say a search or Discord action succeeded without a real tool/API result.

## Live smoke tests still required

- Start only in a separate test server and database; do not replace `rem.service` yet.
- Test ordinary server messages, mention, direct reply, Master DM and rejected Guest DM.
- Test multi-image interpretation, current web search, current Minecraft update question, Modrinth query with exact version/loader, YouTube search success and all-backends-failed behavior.
- Test a follow-up such as “I want the one which uses Oozing potions” after Rem explicitly offers to search YouTube, and confirm a tool executes instead of raw JSON appearing.
- Test reaction add/remove; channel-history scoping for Master and Guest; message deletion Master-only; timeout success, missing permission, role hierarchy failure, API failure and timeout removal.
- Test moderation SAFE/MILD_INSULT/ABUSE, both fallback paths down, fail-closed message handling when classification is unavailable, Guest-to-Master and Guest-to-Guest counter behavior, and successful/failed timeout state verification.
- The user explicitly confirmed `RAND` and `R.A.N.D` are abusive keywords. Both are now included in the case-folded, whole-word fast-abuse pattern list for abuse directed at Master. Keep regression tests for uppercase/lowercase variants and avoid substring matches in unrelated words.
- Run `uv sync` then `uv run python -m unittest discover -s tests -v`. Pure unit tests and compilation are not proof of external-service integration.
- Migrate and regression-test the old long-term memory tools before calling feature parity complete.


## Latest moderation keyword clarification

- The user explicitly confirmed `RAND` and `R.A.N.D` are abusive keywords. Both are included in `MASTER_FAST_ABUSE_PATTERNS`, which the existing fast path applies when the resolved moderation target is Master. Matching is case-insensitive through message case-folding and uses whole-word boundaries to avoid substring matches in unrelated words.
- Regression tests cover `RAND`, `R.A.N.D`, punctuation after the keyword, lowercase variants, and unrelated substrings such as `brand`, `randolph`, and `r.a.n.dylan`.
