import unittest

from rem.text_utils import split_discord_message, strip_bot_mention


class TextUtilsTests(unittest.TestCase):
    def test_only_rem_mention_is_removed(self):
        value = strip_bot_mention("<@!1551213690798211182> hi <@710733071993012237>", 1551213690798211182)
        self.assertEqual(value, "hi <@710733071993012237>")

    def test_long_messages_split_under_discord_limit(self):
        original = "\n".join(["abc"] * 1000)
        chunks = split_discord_message(original, limit=1900)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 1900 for chunk in chunks))
        self.assertEqual("\n".join(chunks), original)

    def test_blank_response_produces_no_chunks(self):
        self.assertEqual(split_discord_message("   "), [])

    def test_nonpositive_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            split_discord_message("abc", limit=0)


if __name__ == "__main__":
    unittest.main()
