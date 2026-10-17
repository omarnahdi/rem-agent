# Rem v2 — Agno Discord migration workspace

This is a **separate migration workspace**. It does not replace or modify the currently running `rem.service`. The project uses Agno's official `agno.integrations.discord.DiscordClient` as the Discord integration foundation, with a small, version-pinned extension of its message-event hook because the current public adapter does not expose pre-message middleware for Rem's DM, moderation, context, and multi-image requirements.

The extension deliberately reuses Agno's `DiscordClient` response pipeline (`_handle_response_in_thread`, confirmation handling, long-reply delivery, and `.serve()` entry point). It is not a separate hand-built `discord.py` bot architecture. The extension depends on Agno 3.1.3 internals; review `docs/agno_discord_extension_notes.md` before upgrading Agno.

## Implemented in this workspace

- **Official Agno Discord integration:** `RemAgnoDiscordClient` subclasses Agno's `DiscordClient` and uses the official `.serve()` entry point.
- **Ordinary server messages:** in explicitly allowed guild text channels/threads, every non-bot message is processed without requiring a mention. Mentions and replies to Rem work as well. As in Agno's default integration, top-level text-channel messages get a new thread. `REM_ALLOWED_CHANNEL_IDS` is blank by default (fail-closed for guild messages); use comma-separated channel IDs for a controlled beta, and set it to `all` only to opt into whole-server processing.
- **Master-only DMs:** only the exact Discord ID `992390617575477268` is accepted in DMs. The gate runs before moderation, tool construction for the run, or AI processing. Guild messages are additionally gated by `REM_ALLOWED_CHANNEL_IDS`. Usernames and display names cannot grant access.
- **Trusted per-run identity:** Master/Guest information is passed in per-run Agno dependencies and metadata. The shared Agent's `additional_context` is never mutated per message, preventing cross-user identity leakage.
- **Agno `DiscordTools`:** official send-message, channel history, channel information, channel list, and delete functions are exposed with scope checks. Deletion and full channel listing are Master-only. Outbound messages suppress mass/user mentions by default. API failures return structured `ok: false` results.
- **Additional Discord actions:** add/remove Rem's reactions; Master-only member timeouts and timeout removals with permission and role-hierarchy checks and a read-back verification; bounded current-channel/thread history retrieval.
- **Internet search:** Agno `WebSearchTools` with DDGS backends for current information. Use it for time-sensitive Minecraft version details and current news instead of answering as if stale model knowledge were current.
- **YouTube discovery:** a real callable async toolkit calls the previously documented public Piped search endpoints. It returns only actual result metadata and links; it never watches/downloads/transcribes/inspects videos. Search is restricted to a clear current-turn request or confirmation of an immediately preceding Rem offer. Search backend failures are returned honestly. It does not emit prompt-only fake tool schemas.
- **Modrinth mod discovery:** an async toolkit queries Modrinth's public API and includes version/loader filters when supplied.
- **Images:** up to eight supported static image attachments are sent to Agno as `Image` inputs, subject to per-file size checks. GIF input is intentionally deferred; video input is not part of the requirements.
- **AI moderation (required):** the saved legacy `moderation.py` has been carried over: Gemini classifier, Groq GPT-OSS 20B fallback, legacy `MASTER_TEXT_ALIASES` and `MASTER_FAST_ABUSE_PATTERNS`, Guest-to-Master immediate timeout, and Guest-to-Guest warn-then-timeout policy. Agno's optional guardrails are not required for this moderation path. If both classifier paths fail, classification is `UNAVAILABLE`, not falsely `SAFE`; no punishment is triggered and the message is blocked from entering the conversational agent until it can be classified. Unexpected moderation exceptions also fail closed. Automatic timeouts verify the resulting member state before claiming success.
- **Context awareness:** the handler includes up to 20 reply ancestors with a 6,000-character budget, and up to 20 nearby channel messages. For messages inside Agno-created threads, parent-channel discussion before the thread starter is included. Each message carries its original UTC time, name, and Discord ID. The agent also has a bounded history-fetch tool.
- **Tool-output safety:** raw tool-call JSON shaped like `{"name": "youtube_search", "arguments": {...}}` is blocked from being sent as a normal Discord reply. The agent instructions require registered tool execution and a real result before claiming success.
- **Concurrency:** calls are serialized per session to keep messages for one thread from overlapping, while independent sessions can run concurrently.
- **Session history:** PostgreSQL/Neon when configured; local SQLite for development. These Agno tables are kept separate from the legacy memory tables.
- **Proactive long-term memory:** a dedicated Agno memory-curator agent evaluates valuable messages in the background. The fast gate looks for explicit preferences/projects/events, sufficiently informative messages, and periodic message checkpoints to spot recurring patterns.
- **Mem0 OSS 2.2.1:** approved facts are stored in a beta-specific `rem_agno_beta_memory_index` collection using `infer=False`, because Agno has already selected a concise fact. The code only considers a store successful when Mem0 returns a confirmed inserted ID.
- **Memory scope:** personal facts are searched by the current speaker's Discord ID; community facts are separately scoped by Discord guild ID. SQL metadata is checked after Mem0 search, so stale, superseded, expired, or untracked vector results are not sent to the model.
- **Memory lifecycle:** duplicate facts are suppressed; corrections are inserted and verified before the old memory is superseded; stale-vector deletion is best-effort, but SQL scope/status checks prevent stale facts from reaching the model. Temporary facts may expire.
- **Event provenance and retention:** candidate decisions reference the original Discord message ID and timestamp. Raw message events are retained for a configurable period (30 days by default), then deleted; curated memories live separately.
- **Background curation:** the curation pass is launched as a tracked task so it doesn't block the normal response. Curation/provider errors do not stop normal chat and never become memory writes.

## Still to test / extend — do not assume completed parity

- **Legacy memory migration:** this build uses a fresh beta collection and beta-prefixed SQL tables. Existing legacy memories and curated profiles are not imported automatically; old data is left untouched.
- **Explicit memory management tools:** end-user search/store/update/forget tools and Master-only community profile management need to be ported onto the new adapter and tested before claiming legacy memory-tool parity. The proactive curator currently creates and updates; automatic deletion is intentionally disabled.
- GIF image-sequence extraction is not implemented by request; video analysis remains deliberately excluded.
- The current `youtube_search.py` source was provided separately and reviewed. The Agno `YouTubeSearchTools` port keeps both documented Piped endpoints, video-only search, 6–10 result limits, metadata normalization, current-turn authorization, fallback across endpoints, and the no-video-inspection boundary. `youtube_utils.py` includes tests for the real Piped object-with-`items` response shape that caused a previous search failure.
- A full live test against Discord, Gemini/Groq, Modrinth, Piped and the web search backend is still required. Unit tests and syntax checks do not prove external services are reachable.

## Setup (Windows PowerShell / `uv`)

```powershell
cd rem-agno-v2
uv sync
Copy-Item .env.example .env
```

Edit `.env` and set at minimum:

```dotenv
DISCORD_BOT_TOKEN=your_discord_bot_token
MASTER_DISCORD_ID=992390617575477268
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash-lite
GROQ_API_KEY=your_groq_api_key
GROQ_REM_MODEL=openai/gpt-oss-120b
```

Groq reply fallback is optional for the agent, but the old moderation classifier also uses `GROQ_API_KEY` for `openai/gpt-oss-20b` as its fallback. When no fallback key is set and Gemini moderation fails, the classifier records `UNAVAILABLE` and does not automatically punish a member.

For vision fallback, `GROQ_VISION_MODEL` defaults to `qwen/qwen3.8-27b`; keep the text fallback and vision fallback separate because the normal GPT-OSS 120B configuration is not the image fallback. For persistent session history, set `AGNO_DATABASE_URL` or `NEON_DATABASE_URL` to a PostgreSQL/Neon connection URL. If neither is set, a local SQLite file is created for development. Do not point a new Agno session database at the legacy memory tables.

## Discord Developer Portal and permissions

Enable the **Message Content Intent** and **Server Members Intent** for the bot. Configure server/channel permissions according to what you want Rem to do:

- View Channels, Send Messages, Read Message History
- Create Public Threads and Send Messages in Threads (Agno's default top-level message behaviour)
- Add Reactions
- Manage Messages if you want Master to be able to use deletion tools
- Moderate Members, with Rem's role below targets, for timeouts

Avoid granting Administrator solely to make the bot work. If a capability is not permitted, Rem's tool should report the actual Discord failure rather than pretend it succeeded.

## Long-term memory beta and data retention

The beta uses Agno to curate facts proactively and Mem0 OSS 2.2.1 to store/retrieve those facts. It does not enable Agno's separate native memory manager. To recognize recurring patterns, eligible, non-sensitive messages may be retained as raw evidence for `REM_MEMORY_EVENT_RETENTION_DAYS` (30 days by default); curated memories can persist longer until they expire, are superseded, or are explicitly forgotten. Obvious credentials and selected sensitive-detail patterns are filtered before evidence storage, but these filters are heuristic and cannot guarantee detection of every sensitive message. For a public beta, announce this behavior to server members. The beta can reuse the existing Neon branch: it uses beta-prefixed SQL tables and a separate Mem0 collection. Leave `REM_MEMORY_DATABASE_URL` blank to use `NEON_DATABASE_URL`, and keep the legacy collection untouched. More details: [`docs/proactive_memory_design.md`](docs/proactive_memory_design.md).

## Tests

```powershell
uv run python -m unittest discover -s tests -v
```

Run `uv sync` and then `uv run python -m unittest discover -s tests -v`. The suite covers identity spoofing prevention, message routing, reply/context retrieval, YouTube authorization/result parsing, timeout verification, session serialization, moderation failure policy, and the new memory policy/scope/result-validation helpers. Unit tests do not replace an isolated beta test against a real PostgreSQL database, Gemini, Mem0's pgvector store, and a test Discord server. See [`docs/live_smoke_test.md`](docs/live_smoke_test.md).

## Safe rollout procedure

1. Keep your old bot and VPS service unchanged.
2. Use a separate beta bot application/token, but it may join the existing community Discord server and reuse the existing Neon branch. Start with one channel in `REM_ALLOWED_CHANNEL_IDS`; widen access only after live memory tests pass.
3. Test messages, threads, DMs, image handling, web/YouTube/Modrinth searches, reactions, moderation, and timeouts individually.
4. Compare moderation decisions to the old bot and keep its files as a rollback path.
5. Do not switch `/home/ubuntu/rem` or restart `rem.service` until you explicitly approve a cutover after the regression checklist.

## Official documentation used

- Agno Discord client: https://docs.agno.com/integrations/discord/overview
- Basic Discord integration: https://docs.agno.com/integrations/discord/usage/basic
- Official Discord tools: https://docs.agno.com/tools/toolkits/social/discord
- Agno web search: https://docs.agno.com/tools/toolkits/search/websearch
- Agno custom toolkits: https://docs.agno.com/tools/creating-tools/toolkits
- Agno RunContext: https://docs.agno.com/reference/run/run-context
- Agno model fallbacks: https://docs.agno.com/models/fallback-models
