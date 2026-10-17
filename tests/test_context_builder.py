import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from rem.context import build_message_context, detect_pending_youtube_confirmation


class FakeMessage:
    def __init__(self, message_id, content, author_name="Guest", author_id=111, created_at=None, attachments=None, reference=None):
        self.id = message_id
        self.content = content
        self.author = SimpleNamespace(id=author_id, display_name=author_name, name=author_name, bot=False)
        self.created_at = created_at or datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=message_id)
        self.attachments = attachments or []
        self.reference = reference
        self.channel = None
        self.jump_url = f"https://discord.com/channels/1/2/{message_id}"


class FakeChannel:
    def __init__(self, channel_id, messages=None, parent=None):
        self.id = channel_id
        self.messages = messages or []
        self.parent = parent
        self.parent_id = parent.id if parent else None

    async def fetch_message(self, message_id):
        for message in self.messages:
            if message.id == int(message_id):
                return message
        raise LookupError(message_id)

    async def history(self, limit=100, before=None, oldest_first=False):
        before_id = getattr(before, "id", None)
        items = [m for m in self.messages if before_id is None or m.id < before_id]
        items.sort(key=lambda m: (m.created_at, m.id), reverse=not oldest_first)
        for item in items[:limit]:
            yield item


class ContextBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_channel_context_includes_original_timestamp_and_author(self):
        first = FakeMessage(10, "We need an Oozing slime farm", "Rohit", 710)
        second = FakeMessage(11, "It must work on Java", "NightxWolf", 992)
        current = FakeMessage(12, "What was the version again?", "Rohit", 710)
        channel = FakeChannel(2, [first, second, current])
        for item in channel.messages:
            item.channel = channel
        context = await build_message_context(current)
        self.assertIn("We need an Oozing slime farm", context)
        self.assertIn("It must work on Java", context)
        self.assertIn("Discord ID 710", context)
        self.assertIn("2026-10-17", context)

    async def test_thread_context_includes_parent_channel_discussion(self):
        parent_old = FakeMessage(90, "We are discussing a slime farm", "Rohit", 710)
        starter = FakeMessage(100, "Let's continue this in a thread", "Rem", 155)
        parent = FakeChannel(2, [parent_old, starter])
        thread_message = FakeMessage(101, "Which potion does it need?", "Rohit", 710)
        current = FakeMessage(102, "The Oozing potion", "Rohit", 710)
        thread = FakeChannel(100, [thread_message, current], parent=parent)
        starter.channel = parent
        parent_old.channel = parent
        for item in thread.messages:
            item.channel = thread
        context = await build_message_context(current)
        self.assertIn("We are discussing a slime farm", context)
        self.assertIn("Let's continue this in a thread", context)
        self.assertIn("Which potion does it need?", context)

    def test_youtube_confirmation_requires_recent_rem_offer(self):
        prior = "RECENT CHANNEL DISCUSSION:\n[2026-10-17 08:00 UTC] Rem (Discord ID 155): Let me know if you need me to search YouTube for a tutorial."
        self.assertTrue(detect_pending_youtube_confirmation("I want the one which uses Oozing potions", prior))
        self.assertFalse(detect_pending_youtube_confirmation("I want the one which uses Oozing potions", "Older topic: maybe search YouTube someday"))
        stale_offer = prior + "\n[2026-10-17 08:01 UTC] Rohit (Discord ID 710): Actually, I have another question."
        self.assertFalse(detect_pending_youtube_confirmation("I want the one which uses Oozing potions", stale_offer))


if __name__ == "__main__":
    unittest.main()
