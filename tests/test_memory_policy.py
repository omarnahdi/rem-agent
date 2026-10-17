import unittest
from types import SimpleNamespace

from rem.memory_policy import (
    count_words,
    normalize_fact,
    render_memory_context,
    should_consider_for_memory,
    should_count_for_memory_review,
    should_record_raw_event,
    validate_candidate,
)


class MemoryPolicyTests(unittest.TestCase):
    def test_explicit_preference_triggers_even_when_short(self):
        self.assertTrue(
            should_consider_for_memory(
                "I prefer Fabric", message_count_24h=1, review_every=5
            )
        )

    def test_generic_message_waits_for_periodic_batch_review(self):
        text = "The server was busy yesterday and everyone played together for hours"
        self.assertFalse(should_consider_for_memory(text, message_count_24h=2, messages_since_review=2))
        self.assertTrue(should_consider_for_memory(text, message_count_24h=10, messages_since_review=10))

    def test_short_current_message_can_trigger_review_of_prior_indirect_context(self):
        # The batch curator will inspect previous author/channel messages; the
        # latest message itself need not contain the fact or meet min_words.
        self.assertTrue(should_consider_for_memory(
            "yeah", message_count_24h=12, messages_since_review=10, review_every=10
        ))

    def test_indirect_preference_is_caught_by_periodic_batch_not_only_explicit_cues(self):
        self.assertTrue(should_consider_for_memory(
            "I keep coming back to Fabric because it feels easier",
            message_count_24h=10, messages_since_review=10, review_every=10
        ))
        self.assertFalse(should_consider_for_memory(
            "This is an ordinary comment", message_count_24h=3, messages_since_review=3, review_every=10
        ))

    def test_explicit_project_statement_triggers_immediately(self):
        self.assertTrue(
            should_consider_for_memory(
                "I am working on a Minecraft castle and want to finish it this weekend",
                message_count_24h=2,
            )
        )

    def test_periodic_review_allows_pattern_detection(self):
        text = "PvP is fun, but building and farming dominate my weekends"
        self.assertFalse(
            should_consider_for_memory(
                text, message_count_24h=4, review_every=5, messages_since_review=4
            )
        )
        self.assertTrue(
            should_consider_for_memory(
                text, message_count_24h=5, review_every=5, messages_since_review=5
            )
        )

    def test_min_words_controls_periodic_review_counting(self):
        self.assertFalse(should_count_for_memory_review("yeah okay", min_words=7))
        self.assertTrue(should_count_for_memory_review("The server was busy yesterday", min_words=5))
        # Explicit useful signals count even when short.
        self.assertTrue(should_count_for_memory_review("I prefer Fabric", min_words=7))
        self.assertFalse(should_count_for_memory_review("/status", min_words=1))

    def test_markdown_link_only_is_not_raw_memory_evidence(self):
        self.assertFalse(should_record_raw_event("[https://example.com](https://example.com)"))

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
