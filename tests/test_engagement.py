import asyncio
import unittest
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from rem.engagement import (
    ConversationEngagementGate,
    is_direct_rem_request,
    parse_engagement_decision,
    build_evaluator_input,
    should_consider_spontaneous_reply,
)


class FakeMessage:
    def __init__(self, message_id, content, *, channel_id=123, user_id=456, guild_id=789):
        self.id = message_id
        self.content = content
        self.created_at = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        self.author = SimpleNamespace(id=user_id, display_name=f"User{user_id}", name=f"user{user_id}", bot=False)
        self.channel = SimpleNamespace(id=channel_id, parent_id=None)
        self.guild = SimpleNamespace(id=guild_id)
        self.attachments = []


class FakeEvaluator:
    def __init__(self, decision):
        self.decision = decision
        self.inputs = []

    async def arun(self, **kwargs):
        self.inputs.append(kwargs["input"])
        return SimpleNamespace(content=self.decision)


class EngagementHeuristicTests(unittest.TestCase):
    def test_mentions_replies_and_master_dm_bypass_evaluator(self):
        self.assertTrue(is_direct_rem_request("hello", mentioned=True))
        self.assertTrue(is_direct_rem_request("hello", replied_to_rem=True))
        self.assertTrue(is_direct_rem_request("hello", master_dm=True))

    def test_explicit_name_request_bypasses_evaluator(self):
        self.assertTrue(is_direct_rem_request("Rem, can you find a mod?"))
        self.assertTrue(is_direct_rem_request("Hey Rem"))
        self.assertTrue(is_direct_rem_request("Hey! rem I need to tell you something"))
        self.assertTrue(is_direct_rem_request("Rem??? I need you to find a mod"))
        self.assertTrue(is_direct_rem_request("What's up, Rem!?"))
        self.assertTrue(is_direct_rem_request("Rem give @dac 6 minute timeout"))

    def test_rem_mentioned_as_subject_is_not_automatically_direct(self):
        self.assertFalse(is_direct_rem_request("I think Rem would like this."))

    def test_ordinary_banter_does_not_trigger_candidate(self):
        for text in ("lol", "LMAO 😂", "ggs", "nice", "true"):
            self.assertFalse(should_consider_spontaneous_reply(text), text)

    def test_questions_and_help_requests_are_candidates(self):
        self.assertTrue(should_consider_spontaneous_reply("Anyone know a mod for this crash?"))
        self.assertTrue(should_consider_spontaneous_reply("I'm looking for a Fabric mod that fixes this."))

    def test_indirect_pronoun_reference_is_candidate_when_recent_context_mentions_rem(self):
        self.assertTrue(
            should_consider_spontaneous_reply(
                "Maybe she knows a fix for this problem",
                recent_context="[18:30 UTC] Omar: Rem might know the answer to this.",
            )
        )
        self.assertFalse(should_consider_spontaneous_reply("Maybe she is already home"))

    def test_observation_buffer_orders_by_original_timestamp(self):
        gate = ConversationEngagementGate(
            evaluator=FakeEvaluator("IGNORE"), context_loader=self._unused_context_loader,
            response_callback=self._unused_response_callback,
        )
        later = FakeMessage(2, "later event")
        earlier = FakeMessage(1, "earlier event")
        earlier.created_at = datetime(2026, 10, 17, 11, 59, 59, tzinfo=timezone.utc)
        gate.observe(later)
        prior = gate.observe(earlier)
        self.assertNotIn("later event", prior)
        self.assertEqual([item.id for item in gate._recent[(789, 123)]], [1, 2])

    async def _unused_context_loader(self, message):
        return ""

    async def _unused_response_callback(self, message, context):
        return None

    def test_decision_parser_fails_closed_on_unexpected_output(self):
        self.assertTrue(parse_engagement_decision("JOIN"))
        self.assertFalse(parse_engagement_decision("IGNORE"))
        self.assertFalse(parse_engagement_decision("JOIN because this is helpful"))
        self.assertFalse(parse_engagement_decision("maybe JOIN"))
        self.assertFalse(parse_engagement_decision(None))


class EngagementGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_ignore_does_not_call_response_callback(self):
        evaluator = FakeEvaluator("IGNORE")
        responses = []

        async def load_context(message):
            return "Recent context before message"

        async def respond(message, context):
            responses.append((message.id, context))

        gate = ConversationEngagementGate(
            evaluator=evaluator, context_loader=load_context, response_callback=respond,
            batch_window_ms=250, min_eval_interval_seconds=0,
        )
        message = FakeMessage(1, "Anyone know a mod for this crash?")
        gate.observe(message)
        self.assertTrue(gate.consider(message))
        task = gate._tasks[(789, 123)]
        await task
        self.assertEqual(len(evaluator.inputs), 1)
        self.assertEqual(responses, [])
        self.assertIn("Anyone know a mod for this crash?", evaluator.inputs[0])

    async def test_join_calls_response_callback_once_with_context(self):
        evaluator = FakeEvaluator("JOIN")
        responses = []

        async def load_context(message):
            return "We were talking about Minecraft mods."

        async def respond(message, context):
            responses.append((message.id, context))

        gate = ConversationEngagementGate(
            evaluator=evaluator, context_loader=load_context, response_callback=respond,
            batch_window_ms=250, min_eval_interval_seconds=0,
        )
        earlier = FakeMessage(1, "My game keeps crashing.")
        latest = FakeMessage(2, "Anyone know a mod for this crash?")
        gate.observe(earlier)
        gate.observe(latest)
        self.assertTrue(gate.consider(latest))
        task = gate._tasks[(789, 123)]
        await task
        self.assertEqual(responses, [(2, "We were talking about Minecraft mods.")])
        self.assertIn("My game keeps crashing.", evaluator.inputs[0])
        self.assertIn("Anyone know a mod for this crash?", evaluator.inputs[0])

    async def test_candidate_is_not_replaced_by_later_banter(self):
        evaluator = FakeEvaluator("JOIN")
        responses = []

        async def load_context(message):
            return f"history before {message.id}"

        async def respond(message, context):
            responses.append((message.id, context))

        gate = ConversationEngagementGate(
            evaluator=evaluator, context_loader=load_context, response_callback=respond,
            batch_window_ms=250, min_eval_interval_seconds=0,
        )
        question = FakeMessage(1, "Anyone know a mod for this crash?")
        banter = FakeMessage(2, "lol", user_id=789)
        banter.created_at = datetime(2026, 10, 17, 12, 0, 1, tzinfo=timezone.utc)
        gate.observe(question)
        self.assertTrue(gate.consider(question))
        gate.observe(banter)
        await gate._tasks[(789, 123)]

        self.assertEqual(responses[0][0], 1)
        self.assertIn("lol", responses[0][1])

    async def test_minimum_evaluator_interval_defers_instead_of_dropping(self):
        evaluator = FakeEvaluator("IGNORE")
        responses = []

        async def load_context(message):
            return "Context"

        async def respond(message, context):
            responses.append(message.id)

        gate = ConversationEngagementGate(
            evaluator=evaluator, context_loader=load_context, response_callback=respond,
            batch_window_ms=250, min_eval_interval_seconds=0.35,
        )
        key = (789, 123)
        gate._last_evaluated_at[key] = time.monotonic()
        candidate = FakeMessage(1, "Anyone know a mod for this crash?")
        gate.observe(candidate)
        self.assertTrue(gate.consider(candidate))
        await gate._tasks[key]
        self.assertEqual(len(evaluator.inputs), 1)
        self.assertEqual(responses, [])


    async def test_empty_evaluator_output_does_not_suppress_direct_rem_address(self):
        evaluator = FakeEvaluator(None)
        responses = []

        async def load_context(message):
            return ""

        async def respond(message, context):
            responses.append((message.id, context))

        gate = ConversationEngagementGate(
            evaluator=evaluator, context_loader=load_context, response_callback=respond,
            batch_window_ms=250, min_eval_interval_seconds=0,
        )
        message = FakeMessage(1, "What's up, Rem!?")
        gate.observe(message)
        self.assertTrue(gate.consider(message))
        await gate._tasks[(789, 123)]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][0], 1)

    async def test_undefined_evaluator_output_uses_deterministic_fallback_for_direct_address(self):
        evaluator = FakeEvaluator("undefined")
        responses = []

        async def load_context(message):
            return ""

        async def respond(message, context):
            responses.append((message.id, context))

        gate = ConversationEngagementGate(
            evaluator=evaluator, context_loader=load_context, response_callback=respond,
            batch_window_ms=250, min_eval_interval_seconds=0,
        )
        message = FakeMessage(1, "Hey! Rem I need to tell you something")
        gate.observe(message)
        self.assertTrue(gate.consider(message))
        await gate._tasks[(789, 123)]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][0], 1)

    async def test_evaluator_response_can_be_recovered_from_assistant_message(self):
        class MessageOnlyEvaluator:
            async def arun(self, **kwargs):
                return SimpleNamespace(content=None, messages=[SimpleNamespace(role="assistant", content="JOIN")])

        responses = []

        async def load_context(message):
            return "Useful context"

        async def respond(message, context):
            responses.append(message.id)

        gate = ConversationEngagementGate(
            evaluator=MessageOnlyEvaluator(), context_loader=load_context, response_callback=respond,
            batch_window_ms=250, min_eval_interval_seconds=0,
        )
        message = FakeMessage(1, "Anyone know a mod for this crash?")
        gate.observe(message)
        self.assertTrue(gate.consider(message))
        await gate._tasks[(789, 123)]
        self.assertEqual(responses, [1])

    async def test_recent_reply_does_not_block_new_topic_candidate(self):
        evaluator = FakeEvaluator("JOIN")
        responses = []
        contexts = [
            "No previous Rem response in this topic.",
            "Rem answered a Minecraft mod question, then the channel moved to a new Fabric issue.",
        ]

        async def load_context(message):
            return contexts[min(message.id - 1, len(contexts) - 1)]

        async def respond(message, context):
            responses.append((message.id, context))

        gate = ConversationEngagementGate(
            evaluator=evaluator, context_loader=load_context, response_callback=respond,
            batch_window_ms=250, min_eval_interval_seconds=0,
        )
        first = FakeMessage(1, "Anyone know a mod for this crash?")
        gate.observe(first)
        self.assertTrue(gate.consider(first))
        await gate._tasks[(789, 123)]

        # No fixed channel-wide cooldown: a later, different topic can be evaluated.
        second = FakeMessage(2, "What mod should I use for Fabric compatibility?")
        second.created_at = datetime(2026, 10, 17, 12, 0, 10, tzinfo=timezone.utc)
        gate.observe(second)
        self.assertTrue(gate.consider(second))
        task = gate._tasks.get((789, 123))
        if task is not None:
            await task
        self.assertEqual(len(evaluator.inputs), 2)
        self.assertEqual([message_id for message_id, _ in responses], [1, 2])

    def test_evaluator_instructions_use_context_instead_of_fixed_reply_cooldown(self):
        from rem.engagement import ENGAGEMENT_INSTRUCTIONS
        self.assertIn("no fixed channel-wide reply cooldown", ENGAGEMENT_INSTRUCTIONS)
        self.assertIn("new topic", ENGAGEMENT_INSTRUCTIONS)

    def test_evaluator_input_requests_structured_json_not_bare_token(self):
        prompt = build_evaluator_input("", [FakeMessage(10, "Anyone know a mod?")])
        self.assertIn('Return a JSON object matching the Pydantic schema', prompt)
        self.assertIn('{"decision":"JOIN"}', prompt)
        self.assertNotIn('Output exactly JOIN or IGNORE.', prompt)


if __name__ == "__main__":
    unittest.main()
