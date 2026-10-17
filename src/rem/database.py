"""Agno session database setup, isolated from the legacy Mem0 memory tables."""

from __future__ import annotations

from pathlib import Path

from agno.db.sqlite import SqliteDb

from .settings import Settings


def normalize_postgres_url(url: str) -> str:
    """Use psycopg 3 explicitly while preserving Neon URL query parameters."""
    trimmed = url.strip()
    if trimmed.startswith("postgres://"):
        return "postgresql+psycopg://" + trimmed[len("postgres://") :]
    if trimmed.startswith("postgresql://"):
        return "postgresql+psycopg://" + trimmed[len("postgresql://") :]
    return trimmed


def build_session_db(settings: Settings):
    """Prefer the existing Neon URL; use local SQLite only for dev bootstrap."""
    if settings.database_url:
        from agno.db.postgres import PostgresDb

        return PostgresDb(db_url=normalize_postgres_url(settings.database_url))

    db_path: Path = settings.sqlite_db_path()
    return SqliteDb(db_file=str(db_path))
