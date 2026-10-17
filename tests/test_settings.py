import os
import unittest
from unittest.mock import patch

from rem.settings import Settings


class AllowedChannelSettingsTests(unittest.TestCase):
    def load_settings(self, channel_setting: str) -> Settings:
        env = {"REM_ALLOWED_CHANNEL_IDS": channel_setting}
        with patch.dict(os.environ, env, clear=True):
            return Settings.from_env(load_env_file=False)

    def test_empty_allowlist_fails_closed(self):
        settings = self.load_settings("")
        self.assertEqual(settings.allowed_guild_channel_ids, frozenset())
        self.assertFalse(settings.allow_all_guild_channels)

    def test_comma_separated_channel_ids_are_parsed(self):
        settings = self.load_settings("123456789, 987654321")
        self.assertEqual(
            settings.allowed_guild_channel_ids,
            frozenset({123456789, 987654321}),
        )
        self.assertFalse(settings.allow_all_guild_channels)

    def test_all_channels_requires_explicit_literal(self):
        settings = self.load_settings("all")
        self.assertTrue(settings.allow_all_guild_channels)
        self.assertEqual(settings.allowed_guild_channel_ids, frozenset())

    def test_invalid_channel_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "REM_ALLOWED_CHANNEL_IDS"):
            self.load_settings("123,not-a-channel")


if __name__ == "__main__":
    unittest.main()
