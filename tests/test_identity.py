import unittest

from rem.constants import DEFAULT_MASTER_DISCORD_ID
from rem.identity import build_trusted_dependencies, identify_speaker, session_id_for


class IdentityTests(unittest.TestCase):
    def test_master_is_identified_by_exact_discord_id(self):
        person = identify_speaker(
            discord_user_id=DEFAULT_MASTER_DISCORD_ID,
            display_name="NightxWolf",
            username="omarnahdi",
            master_discord_id=DEFAULT_MASTER_DISCORD_ID,
        )
        self.assertEqual(person.role, "MASTER")
        self.assertTrue(person.is_master)

    def test_display_name_cannot_spoof_master(self):
        person = identify_speaker(
            discord_user_id=710733071993012237,
            display_name="NightxWolf",
            username="someone_else",
            master_discord_id=DEFAULT_MASTER_DISCORD_ID,
        )
        self.assertEqual(person.role, "GUEST")
        self.assertFalse(person.is_master)

    def test_username_cannot_spoof_master(self):
        person = identify_speaker(
            discord_user_id=710733071993012237,
            display_name="Guest",
            username="omarnahdi",
            master_discord_id=DEFAULT_MASTER_DISCORD_ID,
        )
        self.assertEqual(person.role, "GUEST")

    def test_run_dependencies_are_authoritative_and_per_speaker(self):
        person = identify_speaker(
            discord_user_id=DEFAULT_MASTER_DISCORD_ID,
            display_name="NightxWolf",
            username="omarnahdi",
            master_discord_id=DEFAULT_MASTER_DISCORD_ID,
        )
        context = build_trusted_dependencies(person, guild_id=123, channel_id=456)
        self.assertEqual(context["trusted_discord_role"], "MASTER")
        self.assertEqual(context["trusted_discord_user_id"], str(DEFAULT_MASTER_DISCORD_ID))
        self.assertEqual(context["trusted_discord_is_master"], "true")
        self.assertEqual(context["trusted_discord_guild_id"], "123")

    def test_sessions_are_isolated_per_user_and_channel(self):
        first = session_id_for(guild_id=1, channel_id=2, user_id=3)
        same_user_other_channel = session_id_for(guild_id=1, channel_id=4, user_id=3)
        other_user = session_id_for(guild_id=1, channel_id=2, user_id=5)
        self.assertNotEqual(first, same_user_other_channel)
        self.assertNotEqual(first, other_user)


if __name__ == "__main__":
    unittest.main()
