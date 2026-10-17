"""Message routing policies for the Agno Discord adapter."""


def should_accept_dm(*, author_id: int, master_id: int) -> bool:
    """Only the configured Master may use Rem through direct messages."""
    return int(author_id) == int(master_id)


def should_accept_guild_message(*, is_mentioned: bool = False, is_reply_to_rem: bool = False) -> bool:
    """Agno default requested by the project: process ordinary guild messages too."""
    return True


def should_accept_guild_channel(
    *,
    channel_id: int,
    parent_channel_id: int | None,
    allowed_channel_ids: frozenset[int] | set[int],
    allow_all_channels: bool = False,
) -> bool:
    """Fail closed unless a guild channel or its parent is explicitly allowed."""
    if allow_all_channels:
        return True
    allowed = {int(value) for value in allowed_channel_ids}
    return int(channel_id) in allowed or (
        parent_channel_id is not None and int(parent_channel_id) in allowed
    )
