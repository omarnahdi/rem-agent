import unittest
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import uuid
from types import SimpleNamespace

from rem.memory_service import (
    DEFAULT_MEM0_COLLECTION,
    _asyncpg_dsn,
    _first_inserted_mem0_id,
    _has_explicit_forget_request,
    _has_explicit_remember_request,
    _has_explicit_update_request,
    _extract_requested_fact,
    _mem0_scope_id,
    RemMemoryService,
)


class MemoryServiceHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_scope_ids_are_distinct_and_stable(self):
        self.assertEqual(_mem0_scope_id("user", "123"), "rem-beta:user:123")
        self.assertEqual(_mem0_scope_id("community", "123"), "rem-beta:guild:123")
        self.assertNotEqual(_mem0_scope_id("user", "123"), _mem0_scope_id("community", "123"))

    def test_default_collection_is_beta_specific(self):
        self.assertEqual(DEFAULT_MEM0_COLLECTION, "rem_agno_beta_memory_index")
        self.assertNotEqual(DEFAULT_MEM0_COLLECTION, "rem_memory_index")

    def test_driver_suffix_removed_for_asyncpg(self):
        self.assertEqual(
            _asyncpg_dsn("postgresql+psycopg://user:pass@host/db?sslmode=require"),
            "postgresql://user:pass@host/db?sslmode=require",
        )

    def test_requires_confirmed_mem0_result_id(self):
        valid_id = uuid.uuid4()
        self.assertEqual(
            _first_inserted_mem0_id({"results": [{"id": str(valid_id), "event": "ADD"}]}),
            valid_id,
        )
        self.assertIsNone(_first_inserted_mem0_id({"results": [{"id": str(valid_id), "event": "DELETE"}]}))
        self.assertIsNone(_first_inserted_mem0_id({"results": [{"id": str(valid_id)}]}))
        self.assertIsNone(_first_inserted_mem0_id({"results": [{"id": str(valid_id), "event": "UPDATE"}]}))
        self.assertIsNone(_first_inserted_mem0_id({"results": []}))
        self.assertIsNone(_first_inserted_mem0_id({"results": [{"event": "ADD", "id": "not-a-uuid"}]}))

    def test_explicit_memory_request_detection_and_fact_cleanup(self):
        self.assertTrue(_has_explicit_remember_request("Rem, remember that I prefer Fabric mods"))
        self.assertFalse(_has_explicit_remember_request("I prefer Fabric mods"))
        self.assertEqual(_extract_requested_fact("Rem, remember that I prefer Fabric mods!"), "I prefer Fabric mods")
        self.assertTrue(_has_explicit_update_request("Actually, I prefer Forge now"))
        self.assertTrue(_has_explicit_update_request("Please update that memory"))
        self.assertFalse(_has_explicit_update_request("Can you give me an update about the server?"))
        self.assertTrue(_has_explicit_forget_request("Please forget this memory"))
        self.assertFalse(_has_explicit_forget_request("I forgot where I put the sword"))


    async def test_observation_without_response_skips_mem0_searches(self):
        settings = SimpleNamespace(
            memory_database_url="postgresql://example",
            memory_collection=DEFAULT_MEM0_COLLECTION,
            gemini_api_key="test-key",
            memory_min_words=7,
            memory_review_every=10,
            memory_batch_window_seconds=2,
            memory_event_retention_days=30,
        )
        service = RemMemoryService(settings)
        service._initialized = True
        service.pool = object()
        service.mem0 = object()
        service._last_cleanup_monotonic = time.monotonic()
        service.initialize = AsyncMock(return_value=True)
        service._record_event = AsyncMock(return_value=(1, 1, True))
        service._search_scope = AsyncMock(return_value=[])

        with patch("rem.memory_service.should_record_raw_event", return_value=True), \
             patch("rem.memory_service.should_consider_for_memory", return_value=False):
            result = await service.prepare_message(
                message_id=555,
                user_id=123,
                guild_id=789,
                channel_id=456,
                display_name="TestUser",
                content="This is ordinary conversation with no memory candidate.",
                source_created_at=datetime.now(timezone.utc),
                channel_context="recent context",
                retrieve_memories=False,
            )

        self.assertEqual(result, "")
        service._search_scope.assert_not_awaited()

    async def test_curation_candidates_are_coalesced_into_one_batch(self):
        settings = SimpleNamespace(
            memory_database_url="postgresql://example",
            memory_collection=DEFAULT_MEM0_COLLECTION,
            gemini_api_key="test-key",
            memory_batch_window_seconds=2,
        )
        service = RemMemoryService(settings)
        service._curate_message = AsyncMock(return_value=True)
        service._last_reviewed_event_id = AsyncMock(return_value=0)

        async def no_wait(_seconds):
            return None

        with patch("rem.memory_service.asyncio.sleep", side_effect=no_wait):
            service._queue_curation_candidate({
                "message_id": "100", "event_id": 100, "user_id": 123, "guild_id": 789,
                "channel_id": 456, "display_name": "Omar", "content": "I prefer Fabric",
                "source_created_at": datetime.now(timezone.utc), "channel_context": "topic context",
            })
            service._queue_curation_candidate({
                "message_id": "101", "event_id": 101, "user_id": 123, "guild_id": 789,
                "channel_id": 456, "display_name": "Omar", "content": "It is easier for my mod setup",
                "source_created_at": datetime.now(timezone.utc), "channel_context": "richer topic context",
            })
            task = service._curation_batch_tasks[("789", "123")]
            await task

        service._curate_message.assert_awaited_once()
        call = service._curate_message.await_args.kwargs
        self.assertEqual(call["message_id"], "101")
        self.assertEqual([item["message_id"] for item in call["batch_candidates"]], ["100", "101"])
        self.assertEqual(call["channel_context"], "richer topic context")

    async def test_direct_reply_can_still_retrieve_mem0_memories(self):
        settings = SimpleNamespace(
            memory_database_url="postgresql://example",
            memory_collection=DEFAULT_MEM0_COLLECTION,
            gemini_api_key="test-key",
            memory_min_words=7,
            memory_review_every=10,
            memory_batch_window_seconds=2,
            memory_event_retention_days=30,
        )
        service = RemMemoryService(settings)
        service._initialized = True
        service.pool = object()
        service.mem0 = object()
        service._last_cleanup_monotonic = time.monotonic()
        service.initialize = AsyncMock(return_value=True)
        service._record_event = AsyncMock(return_value=(None, 0, False))
        service._search_scope = AsyncMock(return_value=[])

        with patch("rem.memory_service.should_record_raw_event", return_value=False):
            result = await service.prepare_message(
                message_id=556,
                user_id=123,
                guild_id=789,
                channel_id=456,
                display_name="TestUser",
                content="Rem, can you help me?",
                source_created_at=datetime.now(timezone.utc),
                channel_context="recent context",
                retrieve_memories=True,
            )

        self.assertEqual(result, "")
        self.assertEqual(service._search_scope.await_count, 2)


if __name__ == "__main__":
    unittest.main()
