"""Tests for async Agno session database configuration."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# Tests can import this module without installing the full Agno stack because
# Agno database classes are intentionally imported only by the factory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rem.database import (
    POSTGRES_SESSION_SCHEMA,
    build_session_db,
    normalize_postgres_url,
    require_async_session_db,
)


class NormalizePostgresUrlTests(unittest.TestCase):
    def test_postgres_scheme_is_converted_to_async_psycopg(self) -> None:
        self.assertEqual(
            normalize_postgres_url("postgres://user:pass@host/db?sslmode=require"),
            "postgresql+psycopg_async://user:pass@host/db?sslmode=require",
        )

    def test_postgresql_scheme_is_converted_to_async_psycopg(self) -> None:
        self.assertEqual(
            normalize_postgres_url("postgresql://user:pass@host/db?sslmode=require"),
            "postgresql+psycopg_async://user:pass@host/db?sslmode=require",
        )

    def test_sync_psycopg_scheme_is_converted_to_async_psycopg(self) -> None:
        self.assertEqual(
            normalize_postgres_url("postgresql+psycopg://user:pass@host/db"),
            "postgresql+psycopg_async://user:pass@host/db",
        )

    def test_already_async_driver_url_is_preserved(self) -> None:
        url = "postgresql+psycopg_async://user:pass@host/db?sslmode=require"
        self.assertEqual(normalize_postgres_url(url), url)

    def test_asyncpg_url_is_preserved(self) -> None:
        url = "postgresql+asyncpg://user:pass@host/db?ssl=require"
        self.assertEqual(normalize_postgres_url(url), url)

    def test_empty_and_unknown_urls_are_safe(self) -> None:
        self.assertEqual(normalize_postgres_url("  "), "")
        self.assertEqual(normalize_postgres_url("custom-db-url"), "custom-db-url")


class BuildSessionDbTests(unittest.TestCase):
    @staticmethod
    def fake_agno_db_modules() -> tuple[dict[str, types.ModuleType], type, type]:
        class FakeAsyncPostgresDb:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeAsyncSqliteDb:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        agno = types.ModuleType("agno")
        agno.__path__ = []
        agno_db = types.ModuleType("agno.db")
        agno_db.__path__ = []
        postgres = types.ModuleType("agno.db.postgres")
        postgres.AsyncPostgresDb = FakeAsyncPostgresDb
        sqlite = types.ModuleType("agno.db.sqlite")
        sqlite.AsyncSqliteDb = FakeAsyncSqliteDb
        fake_modules = {
            "agno": agno,
            "agno.db": agno_db,
            "agno.db.postgres": postgres,
            "agno.db.sqlite": sqlite,
        }
        return fake_modules, FakeAsyncPostgresDb, FakeAsyncSqliteDb

    def test_postgres_factory_uses_async_adapter_and_existing_schema(self) -> None:
        fake_modules, FakeAsyncPostgresDb, _ = self.fake_agno_db_modules()
        settings = types.SimpleNamespace(
            database_url="postgresql://user:pass@host/db?sslmode=require"
        )
        with patch.dict(sys.modules, fake_modules):
            result = build_session_db(settings)
        self.assertIsInstance(result, FakeAsyncPostgresDb)
        self.assertEqual(
            result.kwargs["db_url"],
            "postgresql+psycopg_async://user:pass@host/db?sslmode=require",
        )
        self.assertEqual(result.kwargs["db_schema"], POSTGRES_SESSION_SCHEMA)
        self.assertEqual(result.kwargs["db_schema"], "ai")

    def test_sqlite_factory_uses_async_adapter_and_same_file(self) -> None:
        fake_modules, _, FakeAsyncSqliteDb = self.fake_agno_db_modules()
        settings = types.SimpleNamespace(
            database_url="",
            sqlite_db_path=lambda: Path("data/rem_agno_sessions.db"),
        )
        with patch.dict(sys.modules, fake_modules):
            result = build_session_db(settings)
        self.assertIsInstance(result, FakeAsyncSqliteDb)
        # Compare normalized paths, not their platform-specific string spelling.
        # pathlib represents this as backslashes on Windows and forward slashes
        # on POSIX; either spelling points to the same relative database file.
        self.assertEqual(
            Path(result.kwargs["db_file"]),
            Path("data/rem_agno_sessions.db"),
        )


class RequireAsyncSessionDbTests(unittest.TestCase):
    @staticmethod
    def fake_base_module() -> tuple[dict[str, types.ModuleType], type]:
        class FakeAsyncBaseDb:
            pass

        agno = types.ModuleType("agno")
        agno.__path__ = []
        agno_db = types.ModuleType("agno.db")
        agno_db.__path__ = []
        base_module = types.ModuleType("agno.db.base")
        base_module.AsyncBaseDb = FakeAsyncBaseDb
        return (
            {"agno": agno, "agno.db": agno_db, "agno.db.base": base_module},
            FakeAsyncBaseDb,
        )

    def test_accepts_async_agno_adapter(self) -> None:
        fake_modules, FakeAsyncBaseDb = self.fake_base_module()

        class FakeAsyncPostgresDb(FakeAsyncBaseDb):
            pass

        db = FakeAsyncPostgresDb()
        with patch.dict(sys.modules, fake_modules):
            self.assertIs(require_async_session_db(db), db)

    def test_rejects_sync_agno_adapter_with_actionable_error(self) -> None:
        fake_modules, _ = self.fake_base_module()

        class FakePostgresDb:
            pass

        db = FakePostgresDb()
        with patch.dict(sys.modules, fake_modules):
            with self.assertRaisesRegex(
                TypeError,
                r"require an asynchronous Agno session database.*FakePostgresDb.*AsyncPostgresDb",
            ):
                require_async_session_db(db)


if __name__ == "__main__":
    unittest.main()
