# Agno Discord adapter notes (pinned to Agno 3.1.3)

## What is official

`src/rem/discord_app.py` subclasses `agno.integrations.discord.DiscordClient`. The bot starts through the class's `.serve()` method and uses Agno's inherited response handling (`_handle_response_in_thread` / HITL continuation / long response pipeline). `src/rem/agent.py` uses Agno Agent, Gemini, Groq fallback models, Agno database sessions, and registered Agno toolkits.

## Why `_setup_events` is overridden

In Agno 3.1.3, the official `DiscordClient.__init__()` calls `self._setup_events()`. The default handler has several behaviours that don't meet Rem's requirements:

1. It only looks at `message.attachments[0]`.
2. It creates a thread for each message in a top-level text channel.
3. It writes per-message identity into shared `agent.additional_context` before awaiting `agent.arun()`.
4. It doesn't enforce the requirement that DMs are accepted only from the exact Master ID.
5. It doesn't run Rem's legacy moderation pipeline.
6. It doesn't build Rem's bounded reply-chain + parent-channel context.

There is no documented public pre-message middleware hook in the 3.1.3 integration. To keep the official adapter as the base, the subclass overrides only `_setup_events`, while keeping Agno's response handlers and `serve()` implementation. The override is a compatibility shim against an internal method, so it is **version-pinned and must be re-audited whenever Agno is upgraded**. This avoids two separate Discord event architectures but is not a zero-patch use of the upstream default event handler.

Source checked against: https://github.com/agno-agi/agno/blob/v3.1.3/libs/agno/agno/integrations/discord/client.py

## Official DiscordTools toolkit

Agno 3.1.3's `agno.tools.discord.DiscordTools` contains:

- `send_message`
- `get_channel_messages`
- `get_channel_info`
- `list_channels`
- `delete_message`

It does not currently implement reactions, timeouts, or removal of timeouts. `ScopedDiscordTools` subclasses it, adds tool-level scopes, caps history page size, adds request timeouts, suppresses message mentions, and normalizes success/error outcomes. Deletion and listing all channels are Master-only.

Additional methods in `RemDiscordActionTools` use the same authenticated `discord.py` client that Agno's integration owns, rather than a separate bot client. They implement reactions, member timeouts, timeout removal, and bounded context retrieval. This is needed because the official toolkit simply doesn't expose those APIs.

Source checked against: https://github.com/agno-agi/agno/blob/v3.1.3/libs/agno/agno/tools/discord.py

## Important validation boundary

Syntax checks and dependency-free unit tests can run without the bot token. They do not validate Agno's runtime construction, provider tool-calling, Discord gateway, REST permissions, or third-party search reachability. Live integration smoke tests must be run against a test server before production cutover. In particular, test that Gemini/Groq actually invoke registered toolkit functions and that no tool-call JSON is delivered as normal chat.
