import unittest

from rem.moderation_policy import must_block_conversation_agent


class ModerationPolicyTests(unittest.TestCase):
    def test_unavailable_blocks_conversation_agent(self):
        self.assertTrue(must_block_conversation_agent("UNAVAILABLE"))

    def test_none_and_empty_values_fail_closed(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertTrue(must_block_conversation_agent(value))

    def test_valid_results_are_not_blocked(self):
        for label in ("SAFE", "MILD_INSULT", "ABUSE"):
            with self.subTest(label=label):
                self.assertFalse(must_block_conversation_agent(label))

    def test_valid_results_are_case_and_whitespace_tolerant(self):
        for label in (" safe ", "mild_insult", " abuse\n"):
            with self.subTest(label=label):
                self.assertFalse(must_block_conversation_agent(label))

    def test_unknown_or_malformed_labels_fail_closed(self):
        for label in ("UNKNOWN", "SAFE-ish", "ERROR", "WARN", "null"):
            with self.subTest(label=label):
                self.assertTrue(must_block_conversation_agent(label))


if __name__ == "__main__":
    unittest.main()
