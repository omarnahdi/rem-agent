"""Async Agno session database setup for Rem's asyncio-based Discord bot.

The production Discord adapter processes messages on an asyncio event loop, so
session persistence must use Agno's async database adapters. The PostgreSQL
schema name remains ``ai`` to preserve compatibility with the existing Agno
session/history tables created by the previous synchronous adapter.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings

# Agno 3.1.3's PostgresDb and AsyncPostgresDb both default to this schema.
# Specify it explicitly so a future default change cannot silently fork history.
POSTGRES_SESSION_SCHEMA = "ai"


def normalize_postgres_url(url: str) -> str:
    """Normalize common PostgreSQL URLs for Agno's async session adapter.

    Existing URL query parameters (including Neon SSL options) are preserved.
    URLs that already select an asynchronous driver are left unchanged.
    """
    trimmed = (url or "").strip()
    replacements = (
        ("postgres://", "postgresql+psycopg_async://"),
        ("postgresql://", "postgresql+psycopg_async://"),
        ("postgresql+psycopg://", "postgresql+psycopg_async://"),
    )
    for old_prefix, new_prefix in replacements:
        if trimmed.startswith(old_prefix):
            return new_prefix + trimmed[len(old_prefix) :]
    return trimmed


def build_session_db(settings: "Settings") -> Any:
    """Build an async Agno database for use with ``Agent.arun``.

    PostgreSQL/Neon is preferred whenever configured. Local SQLite remains the
    development fallback, but it is async as well so it cannot block the Discord
    event loop. The PostgreSQL ``ai`` schema and default Agno table names are
    preserved; this function does not migrate, delete, or rename any tables.
    """
    if settings.database_url:
        from agno.db.postgres import AsyncPostgresDb

        return AsyncPostgresDb(
            db_url=normalize_postgres_url(settings.database_url),
            db_schema=POSTGRES_SESSION_SCHEMA,
        )

    from agno.db.sqlite import AsyncSqliteDb

    db_path: Path = settings.sqlite_db_path()
    return AsyncSqliteDb(db_file=str(db_path))


def require_async_session_db(db: Any) -> Any:
    """Reject synchronous Agno adapters before they can block an async bot.

    ``Agent.arun()`` runs on Discord's asyncio event loop. Passing ``PostgresDb``
    or ``SqliteDb`` here allows synchronous SQLAlchemy reflection/queries to block
    that loop (the failure pattern seen in the production heartbeat traceback).
    Keep the import local so URL/factory unit tests do not require Agno installed.
    """
    from agno.db.base import AsyncBaseDb

    if not isinstance(db, AsyncBaseDb):
        received = f"{type(db).__module__}.{type(db).__qualname__}"
        raise TypeError(
            "Rem's Discord agents require an asynchronous Agno session database "
            "because they call Agent.arun(); received "
            f"{received}. Configure AsyncPostgresDb or AsyncSqliteDb. "
            "A synchronous PostgresDb/SqliteDb can block Discord's event loop."
        )
    return db
