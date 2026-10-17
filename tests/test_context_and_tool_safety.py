import unittest

from rem.context import history_query_score, search_authorized_by_current_turn
from rem.text_utils import looks_like_raw_tool_call_payload


class YouTubeIntentTests(unittest.TestCase):
    def test_explicit_youtube_search_is_authorized(self):
        self.assertTrue(search_authorized_by_current_turn(
            "Can you search YouTube for a Minecraft slime farm tutorial?", False
        ))

    def test_confirmation_is_authorized_only_when_context_detected_pending_offer(self):
        self.assertTrue(search_authorized_by_current_turn("I want the one which uses Oozing potions", True))
        self.assertFalse(search_authorized_by_current_turn("I want the one which uses Oozing potions", False))

    def test_bare_youtube_reference_does_not_trigger_search(self):
        self.assertFalse(search_authorized_by_current_turn("What does YouTube mean?", False))

    def test_negative_search_request_does_not_trigger_search(self):
        self.assertFalse(search_authorized_by_current_turn("Don't search YouTube for this", False))

    def test_recommend_tutorial_request_can_trigger_search(self):
        self.assertTrue(search_authorized_by_current_turn("Recommend a tutorial for this farm", False))


class HistoryQueryScoringTests(unittest.TestCase):
    def test_topic_search_matches_overlapping_terms_not_only_exact_phrase(self):
        self.assertGreater(history_query_score("Minecraft slime farm Oozing potion", "We built an Oozing farm for slimes in Minecraft"), 0)

    def test_unrelated_history_does_not_match(self):
        self.assertEqual(history_query_score("Minecraft slime farm", "Recipe for lemon cake"), 0)


class RawToolCallOutputTests(unittest.TestCase):
    def test_screenshot_shape_is_detected(self):
        payload = '''{
          "name": "youtube_search",
          "arguments": {"query": "Minecraft slime farm Oozing potions tutorial"}
        }'''
        self.assertTrue(looks_like_raw_tool_call_payload(payload))

    def test_fenced_screenshot_shape_is_detected(self):
        payload = '```json\n{"name":"youtube_search","arguments":{"query":"slime farm"}}\n```'
        self.assertTrue(looks_like_raw_tool_call_payload(payload))

    def test_normal_json_is_not_flagged(self):
        payload = '{"results": [{"title": "Oozing potion farm", "url": "https://example.test"}]}'
        self.assertFalse(looks_like_raw_tool_call_payload(payload))

    def test_normal_text_is_not_flagged(self):
        self.assertFalse(looks_like_raw_tool_call_payload("Here are three useful videos."))


if __name__ == "__main__":
    unittest.main()
