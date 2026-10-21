import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from rem.commands import (
    apply_and_verify_timeout,
    get_member_timeout_until,
    parse_timeout_command,
    timeout_deadline_matches,
)


class _FakeMember:
    def __init__(self, member_id: int, *, bot: bool = False):
        self.id = member_id
        self.bot = bot
        self.display_name = "Rohit"
        self.timed_out_until = None
        self.last_reason = None

    async def timeout(self, until, *, reason=None):
        self.timed_out_until = until
        self.last_reason = reason


class _FakeGuild:
    def __init__(self, member, fetch_results=None):
        self.member = member
        self.fetch_results = list(fetch_results or [])
        self.fetch_calls = 0

    async def fetch_member(self, member_id):
        self.fetch_calls += 1
        if self.fetch_results:
            result = self.fetch_results.pop(0)
            if result is None:
                return SimpleNamespace(id=member_id, timed_out_until=None)
            return result
        return self.member


class TimeoutCommandTests(unittest.TestCase):
    def setUp(self):
        self.rem = SimpleNamespace(id=1551213690798211182, bot=True)
        self.rohit = SimpleNamespace(id=710733071993012237, bot=False)
        self.master_id = 992390617575477268

    def message(self, content, mentions=None, guild=None):
        return SimpleNamespace(content=content, mentions=mentions or [], guild=guild)

    def test_parses_mentioned_member_and_duration(self):
        request = parse_timeout_command(
            self.message(
                "<@1551213690798211182> give <@710733071993012237> 15 minutes timeout",
                [self.rem, self.rohit],
            ),
            author_id=self.master_id,
            bot_id=self.rem.id,
        )
        self.assertEqual(request["user_id"], self.rohit.id)
        self.assertEqual(request["seconds"], 900)

    def test_parses_two_hour_timeout_from_master_command(self):
        request = parse_timeout_command(
            self.message(
                "<@1551213690798211182> timeout <@710733071993012237> for 2 hours",
                [self.rem, self.rohit],
            ),
            author_id=self.master_id,
            bot_id=self.rem.id,
        )
        self.assertEqual(request["user_id"], self.rohit.id)
        self.assertEqual(request["seconds"], 7200)
        self.assertEqual(request["amount"], 2)
        self.assertEqual(request["unit"], "hours")

    def test_mentioned_target_takes_priority_over_incidental_me_word(self):
        request = parse_timeout_command(
            self.message(
                "<@1551213690798211182> timeout <@710733071993012237> for 2 hours and tell me when done",
                [self.rem, self.rohit],
            ),
            author_id=self.master_id,
            bot_id=self.rem.id,
        )
        self.assertEqual(request["user_id"], self.rohit.id)
        self.assertEqual(request["seconds"], 7200)

    def test_parses_hyphenated_duration(self):
        request = parse_timeout_command(
            self.message(
                "<@1551213690798211182> give <@710733071993012237> a 12-minute timeout",
                [self.rem, self.rohit],
            ),
            author_id=self.master_id,
            bot_id=self.rem.id,
        )
        self.assertEqual(request["seconds"], 720)

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
            self.message(
                "<@1551213690798211182> timeout <@710733071993012237> 5 weeks",
                [self.rem, self.rohit],
            ),
            author_id=self.master_id,
            bot_id=self.rem.id,
        )
        self.assertIsNone(request)

    def test_requires_explicit_timeout_language(self):
        request = parse_timeout_command(
            self.message(
                "<@1551213690798211182> Rohit needs a break for 15 minutes",
                [self.rem, self.rohit],
            ),
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

    def test_reads_current_discord_py_attribute_name(self):
        expected = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        member = SimpleNamespace(timed_out_until=expected)
        self.assertEqual(get_member_timeout_until(member), expected)

    def test_reads_legacy_discord_py_attribute_name(self):
        expected = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        member = SimpleNamespace(communication_disabled_until=expected)
        self.assertEqual(get_member_timeout_until(member), expected)


class TimeoutVerificationAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_stale_none_until_timeout_is_visible(self):
        member = _FakeMember(710733071993012237)
        stale = SimpleNamespace(id=member.id, timed_out_until=None)
        guild = _FakeGuild(member, fetch_results=[stale, member])

        result = await apply_and_verify_timeout(
            guild,
            member,
            10 * 60,
            "unit test",
            verification_attempts=4,
            retry_delays=(0, 0, 0, 0),
        )

        self.assertTrue(result["verified"])
        self.assertEqual(result["attempts_used"], 2)
        self.assertEqual(result["actual_until"], result["expected_until"])
        self.assertEqual(member.last_reason, "unit test")

    async def test_does_not_claim_success_if_discord_never_reports_deadline(self):
        member = _FakeMember(710733071993012237)
        guild = _FakeGuild(member, fetch_results=[None, None, None, None])

        result = await apply_and_verify_timeout(
            guild,
            member,
            20 * 60,
            "unit test",
            verification_attempts=4,
            retry_delays=(0, 0, 0, 0),
        )

        self.assertFalse(result["verified"])
        self.assertIsNone(result["actual_until"])
        self.assertEqual(result["attempts_used"], 4)


if __name__ == "__main__":
    unittest.main()
