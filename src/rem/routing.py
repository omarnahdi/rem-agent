"""Message routing policies for the Agno Discord adapter."""


def should_accept_dm(*, author_id: int, master_id: int) -> bool:
    """Only the configured Master may use Rem through direct messages."""
    return int(author_id) == int(master_id)


def should_accept_guild_message(*, is_mentioned: bool = False, is_reply_to_rem: bool = False) -> bool:
    """Allow normal guild messages through; engagement decides when Rem joins."""
    return True


def should_accept_guild_channel(
    *,
    channel_id: int,
    parent_channel_id: int | None,
    allowed_channel_ids: frozenset[int] | set[int],
    allow_all_channels: bool = True,
) -> bool:
    """Do not limit Rem to an allowlist; actual Discord visibility still applies.

    Legacy arguments remain to preserve the old call signature. They no longer
    block a channel, because the owner requested Rem to work in every accessible
    channel and thread on the active server.
    """
    return True
