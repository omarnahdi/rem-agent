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


class AgnoDebugSettingsTests(unittest.TestCase):
    def load_settings(self, **values) -> Settings:
        env = {
            "AGNO_DEBUG_MODE": "true",
            "AGNO_DEBUG_LEVEL": "1",
            **values,
        }
        with patch.dict(os.environ, env, clear=True):
            return Settings.from_env(load_env_file=False)

    def test_agno_debug_enabled_by_default_for_beta(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env(load_env_file=False)
        self.assertTrue(settings.agno_debug_mode)
        self.assertEqual(settings.agno_debug_level, 1)

    def test_agno_debug_can_be_disabled(self):
        settings = self.load_settings(AGNO_DEBUG_MODE="false")
        self.assertFalse(settings.agno_debug_mode)

    def test_agno_debug_level_two_is_supported(self):
        settings = self.load_settings(AGNO_DEBUG_LEVEL="2")
        self.assertEqual(settings.agno_debug_level, 2)

    def test_invalid_agno_debug_level_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "AGNO_DEBUG_LEVEL"):
            self.load_settings(AGNO_DEBUG_LEVEL="3")

    def test_invalid_agno_debug_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "AGNO_DEBUG_MODE"):
            self.load_settings(AGNO_DEBUG_MODE="sometimes")

    def test_curseforge_api_key_is_optional_and_loaded_when_set(self):
        with patch.dict(os.environ, {}, clear=True):
            missing = Settings.from_env(load_env_file=False)
        self.assertIsNone(missing.curseforge_api_key)

        with patch.dict(os.environ, {"CURSEFORGE_API_KEY": "test-key"}, clear=True):
            configured = Settings.from_env(load_env_file=False)
        self.assertEqual(configured.curseforge_api_key, "test-key")


if __name__ == "__main__":
    unittest.main()
