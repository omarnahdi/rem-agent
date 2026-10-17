# Audit of the latest uploaded legacy Rem snapshot

Audit basis: the `Rem Bot.zip` provided on 2026-10-17 plus the separate latest `youtube_search.py` source.

## Files present in the uploaded ZIP

- `bot.py`
- `image_handler.py`
- `memory.py`
- `memory_schema.sql`
- `memory_tools.py`
- `moderation.py`
- `pyproject.toml`
- `seed_community_profiles.py`

The ZIP did not include `uv.lock`, the legacy test files, `README.md`, or `bot_backup.py`. The Python source in the ZIP passed `compileall`; this only checks syntax, not runtime behavior.

## Relevant behavior preserved as migration requirements

- Exact Master identity: Discord user ID `992390617575477268`; names and nicknames are not authoritative.
- English, Hinglish, Hindi in Roman script and mixed-language interpretation.
- AI moderation across ordinary human server messages, with recent context and target resolution. Master messages are exempt from the automatic classifier according to the legacy policy.
- Gemini moderation with Groq `openai/gpt-oss-20b` fallback. An outage or malformed classifier response must yield `UNAVAILABLE`, not `SAFE`, and must not increment punishment counters or trigger an automatic punishment.
- Guest-to-Master abuse policy and Guest-to-Guest warning/second-abuse timeout policy, while verifying the requested timeout through a Discord state read-back before claiming success.
- Memory architecture keeps raw events distinct from curated/superseded memories. Existing-memory corrections use `memory_update`, not delete-then-store.
- YouTube search is discovery only, with no watching, downloading, transcription or video inspection. The uploaded source uses `https://pipedapi.ducks.party` and `https://api.piped.private.coffee`, limits results to 6–10, and normalizes titles, channels, duration, views, publication date, description and YouTube URL.
- Image input supports still image attachments with MIME/size checks. GIF support is deferred; video handling is not required.
- Historical message creation time and the time context was logged must remain distinct.

## Specific regression protections in the Agno workspace

- No raw tool-call JSON is sent as a normal Discord response.
- YouTube search is a registered executable tool and validates the current turn or an immediate confirmation following Rem's own search offer.
- Piped `{ "items": [...] }` responses are supported and validated.
- Moderation classifier failures return `UNAVAILABLE`, not `SAFE`.
- Timeout deadline verification uses a 10-second tolerance to avoid treating an older active timeout as proof that a new timeout succeeded.
- Agno session IDs are user-scoped within the channel/thread to avoid database ownership collisions when multiple people speak in a shared Discord thread.

## Not yet complete

- The legacy Mem0/Neon persistent memory adapter and tools have not been migrated into the Agno project.
- The legacy ZIP omitted `uv.lock` and tests, so the exact legacy dependency resolution and regression suite could not be reproduced from the ZIP.
- Live tests still require a separate test Discord application/server and valid provider credentials. Do not change the production `rem.service` until the full smoke-test checklist passes and cutover is explicitly approved.
