import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from rem.commands import parse_timeout_command, timeout_deadline_matches


class TimeoutCommandTests(unittest.TestCase):
    def setUp(self):
        self.rem = SimpleNamespace(id=1551213690798211182, bot=True)
        self.rohit = SimpleNamespace(id=710733071993012237, bot=False)
        self.master_id = 992390617575477268

    def message(self, content, mentions=None, guild=None):
        return SimpleNamespace(content=content, mentions=mentions or [], guild=guild)

    def test_parses_mentioned_member_and_duration(self):
        request = parse_timeout_command(
            self.message("<@1551213690798211182> give <@710733071993012237> 15 minutes timeout", [self.rem, self.rohit]),
            author_id=self.master_id,
            bot_id=self.rem.id,
        )
        self.assertEqual(request["user_id"], self.rohit.id)
        self.assertEqual(request["seconds"], 900)

    def test_does_not_infer_target_from_display_name(self):
        guild = SimpleNamespace(members=[self.rohit])
        request = parse_timeout_command(
            self.message("<@1551213690798211182> timeout him 15 minutes", [self.rem], guild),
            author_id=self.master_id,
            bot_id=self.rem.id,
            referenced_message=None,
        )
        self.assertIsNone(request)

    def test_rejects_durations_over_discord_limit(self):
        request = parse_timeout_command(
            self.message("<@1551213690798211182> timeout <@710733071993012237> 5 weeks", [self.rem, self.rohit]),
            author_id=self.master_id,
            bot_id=self.rem.id,
        )
        self.assertIsNone(request)

    def test_requires_explicit_timeout_language(self):
        request = parse_timeout_command(
            self.message("<@1551213690798211182> Rohit needs a break for 15 minutes", [self.rem, self.rohit]),
            author_id=self.master_id,
            bot_id=self.rem.id,
        )
        self.assertIsNone(request)

    def test_timeout_verification_requires_matching_deadline(self):
        expected = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(timeout_deadline_matches(expected, expected + timedelta(seconds=3)))
        self.assertFalse(timeout_deadline_matches(expected, expected + timedelta(seconds=30)))
        self.assertFalse(timeout_deadline_matches(expected, expected + timedelta(minutes=15)))
        self.assertFalse(timeout_deadline_matches(expected, None))


if __name__ == "__main__":
    unittest.main()
