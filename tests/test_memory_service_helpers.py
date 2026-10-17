import unittest
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
)


class MemoryServiceHelperTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
