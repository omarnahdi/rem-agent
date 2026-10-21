import unittest

from rem.routing import should_accept_dm, should_accept_guild_message, should_accept_guild_channel
from rem.constants import DEFAULT_MASTER_DISCORD_ID


class RoutingTests(unittest.TestCase):
    def test_only_master_dm_is_accepted(self):
        self.assertTrue(should_accept_dm(author_id=DEFAULT_MASTER_DISCORD_ID, master_id=DEFAULT_MASTER_DISCORD_ID))
        self.assertFalse(should_accept_dm(author_id=710733071993012237, master_id=DEFAULT_MASTER_DISCORD_ID))

    def test_guild_mention_is_accepted(self):
        self.assertTrue(should_accept_guild_message(is_mentioned=True, is_reply_to_rem=False))

    def test_reply_to_rem_is_accepted(self):
        self.assertTrue(should_accept_guild_message(is_mentioned=False, is_reply_to_rem=True))

    def test_ordinary_guild_message_is_also_accepted(self):
        self.assertTrue(should_accept_guild_message(is_mentioned=False, is_reply_to_rem=False))

    def test_channel_allowlist_never_blocks_any_guild_channel(self):
        self.assertTrue(should_accept_guild_channel(
            channel_id=123, parent_channel_id=None,
            allowed_channel_ids=frozenset(),
        ))
        self.assertTrue(should_accept_guild_channel(
            channel_id=456, parent_channel_id=999,
            allowed_channel_ids=frozenset({123}),
        ))

    def test_all_channels_are_always_enabled(self):
        self.assertTrue(should_accept_guild_channel(
            channel_id=456, parent_channel_id=None,
            allowed_channel_ids=frozenset(), allow_all_channels=False,
        ))


if __name__ == "__main__":
    unittest.main()
