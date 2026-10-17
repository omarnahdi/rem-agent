# Rem v2 live smoke test

Use the **separate beta bot application/token** so this process does not conflict with the existing `rem.service`. The beta can join the existing community server and reuse the existing Neon branch. Start with one allowlisted beta text channel; the bot ignores other guild channels until explicitly enabled.

## Start

```powershell
uv sync
Copy-Item .env.example .env
# Fill in DISCORD_BOT_TOKEN, GEMINI_API_KEY, GROQ_API_KEY and NEON_DATABASE_URL.
# Add the dedicated beta text-channel ID to REM_ALLOWED_CHANNEL_IDS first.
uv run python -m unittest discover -s tests -v
uv run rem-discord
```

## Beta channel gate

Set `REM_ALLOWED_CHANNEL_IDS=123456789012345678` to restrict the bot to one beta text channel. For threads created inside that channel, the parent channel ID is checked too. Leave it blank to ignore all guild messages (Master DMs remain available). Only set `REM_ALLOWED_CHANNEL_IDS=all` when you're deliberately enabling the whole server.

## Checklist

- [ ] Rem connects and logs in without an Agno import/API error.
- [ ] An ordinary server message without an @Rem mention gets a response, matching the chosen Agno default.
- [ ] A message mentioning @Rem gets a response.
- [ ] A direct reply to Rem uses the reply message as context.
- [ ] Master DM works for Discord ID `992390617575477268`.
- [ ] A non-Master DM is ignored before any agent/model call.
- [ ] Two different users can speak in one thread without Agno session ownership errors.
- [ ] Rem uses `fetch_recent_conversation` when a follow-up refers to an older topic or another public thread.
- [ ] A supported PNG/JPEG/WebP/HEIC/HEIF attachment is passed to the model. Attach multiple images and verify the first-attachment-only limitation is gone.
- [ ] An unsupported GIF attachment is reported as unsupported rather than described as if it was inspected. GIF processing is a later phase; video input is not required.
- [ ] A current Minecraft/version question triggers WebSearchTools and provides real source URLs.
- [ ] A Modrinth request returns actual projects and respects Minecraft version/loader filters where provided.
- [ ] Asking “search YouTube for a Minecraft Oozing slime farm tutorial” calls the real `search_youtube` tool and returns real links.
- [ ] When Rem offers YouTube search and the user replies “I want the one which uses Oozing potions”, the follow-up triggers a search. Raw `{"name":"youtube_search","arguments":...}` JSON must never appear in Discord.
- [ ] If all Piped search endpoints fail, Rem clearly reports that search is unavailable and invents no results.
- [ ] Verify the Piped response format `{ "items": [...] }` is parsed; a search must produce real titles and valid YouTube links instead of an empty result or raw tool-call JSON.
- [ ] Reaction add/remove tools perform the requested action and report failures honestly.
- [ ] Guests cannot use timeout, remove-timeout, channel-wide listing, or message deletion tools.
- [ ] Master timeout succeeds only when Discord's effective `moderate_members` permission and role hierarchy allow it, and read-back confirms the requested deadline.
- [ ] Test timeout failure cases: missing permission, target role too high, a bot target, invalid duration, API failure, and verification mismatch.
- [ ] Moderation classifies ordinary, mild, and abusive messages as expected against the legacy bot. Force Gemini and Groq moderation to fail; the result must be `UNAVAILABLE`, not `SAFE` or a punishment.
- [ ] Check service logs for real source creation timestamps and processing timestamps separately.
- [ ] Run `uv run python -m unittest discover -s tests -v`; confirm both moderation-failure tests are executed rather than skipped.

## What is not yet part of the runtime

The legacy Mem0/Neon long-term memory adapter and its `memory_search`, `memory_store`, `memory_update`, and `memory_forget` tools have not been migrated. Agno session history and Discord conversation retrieval are available, but they are not a replacement for curated long-term memory. Do not mark the migration feature-complete until that system receives its own review and regression tests.
