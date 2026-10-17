import unittest
from types import SimpleNamespace

from rem.memory_policy import (
    count_words,
    normalize_fact,
    render_memory_context,
    should_consider_for_memory,
    should_record_raw_event,
    validate_candidate,
)


class MemoryPolicyTests(unittest.TestCase):
    def test_explicit_preference_triggers_even_when_short(self):
        self.assertTrue(
            should_consider_for_memory(
                "I prefer Fabric", message_count_24h=1, min_words=7, review_every=5
            )
        )

    def test_long_message_triggers_curator(self):
        self.assertTrue(
            should_consider_for_memory(
                "I am working on a Minecraft castle and want to finish it this weekend",
                message_count_24h=2,
            )
        )

    def test_periodic_review_allows_pattern_detection(self):
        self.assertTrue(
            should_consider_for_memory(
                "PvP is fun", message_count_24h=10, min_words=7, review_every=5
            )
        )

    def test_commands_and_empty_messages_do_not_trigger(self):
        self.assertFalse(should_consider_for_memory("/status", message_count_24h=5))
        self.assertFalse(should_consider_for_memory("  ", message_count_24h=5))

    def test_raw_event_gate_rejects_secrets_and_sensitive_details(self):
        self.assertFalse(should_record_raw_event("my api_key: this-is-a-secret-value"))
        self.assertFalse(should_record_raw_event("my medication is ExampleMed"))
        self.assertFalse(should_record_raw_event("https://example.com"))
        self.assertFalse(should_record_raw_event("/status"))
        self.assertTrue(should_record_raw_event("I prefer Fabric mods over Forge"))

    def test_credential_like_messages_do_not_trigger(self):
        self.assertFalse(
            should_consider_for_memory(
                "my api_key: this-is-a-secret-value", message_count_24h=7
            )
        )

    def test_candidate_threshold_and_scope_checks(self):
        valid_user = SimpleNamespace(
            fact="The member prefers Fabric mods over Forge", scope="user",
            operation="create", category="preference", confidence=0.9, importance=0.8,
        )
        self.assertEqual(validate_candidate(valid_user, guild_id=123), (True, "accepted"))

        low_confidence = SimpleNamespace(
            fact="The member prefers Fabric mods over Forge", scope="user",
            operation="create", category="preference", confidence=0.5, importance=0.8,
        )
        self.assertEqual(validate_candidate(low_confidence, guild_id=123)[1], "confidence_below_threshold")

        community_in_dm = SimpleNamespace(
            fact="The server's weekly event is on Saturday", scope="community",
            operation="create", category="community_event", confidence=0.95, importance=0.9,
        )
        self.assertEqual(validate_candidate(community_in_dm, guild_id=None)[1], "community_memory_requires_guild")

    def test_credential_like_fact_is_rejected(self):
        candidate = SimpleNamespace(
            fact="Member's api_key: something-secret", scope="user",
            operation="create", category="general", confidence=0.99, importance=0.99,
        )
        self.assertEqual(validate_candidate(candidate, guild_id=123)[1], "credential_like_content")

    def test_context_labels_private_and_community_memory(self):
        block = render_memory_context(
            [{"fact": "The member prefers Fabric mods"}],
            [{"fact": "The server runs Java Edition"}],
        )
        self.assertIn("current speaker", block)
        self.assertIn("Discord community", block)
        self.assertIn("The member prefers Fabric mods", block)
        self.assertIn("The server runs Java Edition", block)

    def test_empty_memory_context_is_empty(self):
        self.assertEqual(render_memory_context([], []), "")

    def test_duplicate_normalization(self):
        self.assertEqual(
            normalize_fact("  The Member   prefers Fabric mods! "),
            normalize_fact("the member prefers fabric mods"),
        )

    def test_word_count(self):
        self.assertEqual(count_words("I’m building a large castle"), 5)


if __name__ == "__main__":
    unittest.main()
