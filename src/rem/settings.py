"""Environment configuration, including compatibility with legacy Rem names."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .constants import DEFAULT_GEMINI_MODEL, DEFAULT_GROQ_REM_MODEL, DEFAULT_GROQ_VISION_MODEL, DEFAULT_MASTER_DISCORD_ID


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str | None
    master_discord_id: int
    gemini_api_key: str | None
    gemini_model: str
    curseforge_api_key: str | None
    groq_api_key: str | None
    groq_rem_model: str
    groq_vision_model: str
    database_url: str | None
    sqlite_session_db: str
    history_runs: int
    log_level: str
    agno_debug_mode: bool
    agno_debug_level: int
    memory_database_url: str | None
    memory_collection: str
    memory_min_words: int
    memory_review_every: int
    memory_event_retention_days: int
    allowed_guild_channel_ids: frozenset[int]
    allow_all_guild_channels: bool
    engagement_enabled: bool
    groq_engagement_model: str
    engagement_batch_window_ms: int
    engagement_min_eval_interval_seconds: int
    memory_batch_window_seconds: int

    @classmethod
    def from_env(cls, *, load_env_file: bool = True) -> "Settings":
        if load_env_file:
            load_dotenv()

        master_raw = os.getenv("MASTER_DISCORD_ID", str(DEFAULT_MASTER_DISCORD_ID)).strip()
        try:
            master_id = int(master_raw)
        except ValueError as exc:
            raise ValueError("MASTER_DISCORD_ID must be a numeric Discord user ID.") from exc
        if master_id <= 0:
            raise ValueError("MASTER_DISCORD_ID must be a positive Discord user ID.")

        history_raw = os.getenv("AGNO_HISTORY_RUNS", "20").strip()
        try:
            history_runs = int(history_raw)
        except ValueError as exc:
            raise ValueError("AGNO_HISTORY_RUNS must be an integer.") from exc
        if not 1 <= history_runs <= 100:
            raise ValueError("AGNO_HISTORY_RUNS must be between 1 and 100.")

        def positive_int(name: str, default: str, minimum: int, maximum: int) -> int:
            raw = os.getenv(name, default).strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer.") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}.")
            return value

        debug_mode_raw = os.getenv("AGNO_DEBUG_MODE", "true").strip().casefold()
        if debug_mode_raw not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise ValueError("AGNO_DEBUG_MODE must be true/false, yes/no, on/off, or 1/0.")
        agno_debug_mode = debug_mode_raw in {"1", "true", "yes", "on"}
        agno_debug_level = positive_int("AGNO_DEBUG_LEVEL", "1", 1, 2)

        engagement_enabled_raw = os.getenv("REM_ENGAGEMENT_ENABLED", "true").strip().casefold()
        if engagement_enabled_raw not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise ValueError("REM_ENGAGEMENT_ENABLED must be true/false, yes/no, on/off, or 1/0.")
        engagement_enabled = engagement_enabled_raw in {"1", "true", "yes", "on"}

        raw_channel_ids = os.getenv("REM_ALLOWED_CHANNEL_IDS", "").strip()
        allow_all_guild_channels = raw_channel_ids.casefold() == "all"
        parsed_channel_ids: set[int] = set()
        if raw_channel_ids and not allow_all_guild_channels:
            for raw_id in raw_channel_ids.split(","):
                raw_id = raw_id.strip()
                if not raw_id:
                    continue
                try:
                    channel_id = int(raw_id)
                except ValueError as exc:
                    raise ValueError(
                        "REM_ALLOWED_CHANNEL_IDS must be comma-separated numeric channel IDs, or 'all'."
                    ) from exc
                if channel_id <= 0:
                    raise ValueError("REM_ALLOWED_CHANNEL_IDS entries must be positive numeric IDs.")
                parsed_channel_ids.add(channel_id)

        return cls(
            # The prior bot used DISCORD_TOKEN; accept either name during migration.
            discord_token=(os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or "").strip() or None,
            master_discord_id=master_id,
            gemini_api_key=(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip() or None,
            gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip(),
            curseforge_api_key=os.getenv("CURSEFORGE_API_KEY", "").strip() or None,
            groq_api_key=os.getenv("GROQ_API_KEY", "").strip() or None,
            groq_rem_model=os.getenv("GROQ_REM_MODEL", DEFAULT_GROQ_REM_MODEL).strip(),
            groq_vision_model=os.getenv("GROQ_VISION_MODEL", DEFAULT_GROQ_VISION_MODEL).strip(),
            database_url=(os.getenv("AGNO_DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or "").strip() or None,
            sqlite_session_db=os.getenv("SQLITE_SESSION_DB", "data/rem_agno_sessions.db").strip(),
            history_runs=history_runs,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper().strip(),
            agno_debug_mode=agno_debug_mode,
            agno_debug_level=agno_debug_level,
            # Memory has its own namespace and Mem0 collection. Falling back to the
            # existing Neon URL is safe for this code because beta table/collection
            # names are unique, so the existing Neon branch may be reused.
            memory_database_url=(os.getenv("REM_MEMORY_DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or "").strip() or None,
            memory_collection=(os.getenv("REM_MEMORY_COLLECTION") or "rem_agno_beta_memory_index").strip(),
            memory_min_words=positive_int("REM_MEMORY_MIN_WORDS", "7", 1, 50),
            memory_review_every=positive_int("REM_MEMORY_REVIEW_EVERY", "10", 1, 100),
            memory_batch_window_seconds=positive_int("REM_MEMORY_BATCH_WINDOW_SECONDS", "8", 2, 60),
            memory_event_retention_days=positive_int("REM_MEMORY_EVENT_RETENTION_DAYS", "30", 1, 365),
            allowed_guild_channel_ids=frozenset(parsed_channel_ids),
            allow_all_guild_channels=allow_all_guild_channels,
            engagement_enabled=engagement_enabled,
            groq_engagement_model=os.getenv("GROQ_ENGAGEMENT_MODEL", "openai/gpt-oss-20b").strip(),
            engagement_batch_window_ms=positive_int("REM_ENGAGEMENT_BATCH_WINDOW_MS", "1500", 250, 5000),
            engagement_min_eval_interval_seconds=positive_int("REM_ENGAGEMENT_MIN_EVAL_INTERVAL_SECONDS", "2", 0, 120),
        )

    def validate_runtime(self) -> None:
        if not self.discord_token:
            raise RuntimeError("Missing Discord token. Set DISCORD_BOT_TOKEN (or legacy DISCORD_TOKEN) in .env.")
        if not self.gemini_api_key:
            raise RuntimeError("Missing Gemini API key. Set GEMINI_API_KEY (or GOOGLE_API_KEY) in .env.")
        if not self.gemini_model:
            raise RuntimeError("GEMINI_MODEL must not be empty.")
        if not self.groq_rem_model:
            raise RuntimeError("GROQ_REM_MODEL must not be empty.")
        if not self.groq_vision_model:
            raise RuntimeError("GROQ_VISION_MODEL must not be empty.")
        if not self.groq_engagement_model:
            raise RuntimeError("GROQ_ENGAGEMENT_MODEL must not be empty.")

    def sqlite_db_path(self) -> Path:
        path = Path(self.sqlite_session_db).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
