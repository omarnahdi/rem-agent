"""Agno Rem agent, with real registered tools (not prompt-only tool schemas)."""
from __future__ import annotations

import logging

from agno.agent import Agent
from agno.models.google import Gemini
from agno.models.groq import Groq
from agno.tools.websearch import WebSearchTools

from .database import build_session_db
from .discord_tools import RemDiscordActionTools, ScopedDiscordTools
from .curseforge_tools import CurseForgeSearchTools
from .modrinth_tools import ModrinthSearchTools
from .memory_tools import RemMemoryTools
from .memory_service import RemMemoryService
from .persona import SYSTEM_INSTRUCTIONS
from .settings import Settings
from .youtube_tools import YouTubeSearchTools

logger = logging.getLogger(__name__)


def build_agent(settings: Settings, discord_client=None, *, vision_mode: bool = False, session_db=None, memory_service: RemMemoryService | None = None) -> Agent:
    settings.validate_runtime()
    if discord_client is None:
        raise ValueError("A discord.py client must be created before the agent so action tools can use the same authenticated connection.")

    fallback_models = []
    fallback_model_id = settings.groq_vision_model if vision_mode else settings.groq_rem_model
    if settings.groq_api_key:
        fallback_models.append(Groq(id=fallback_model_id, api_key=settings.groq_api_key))
        mode_label = "vision" if vision_mode else "text"
        logger.info("Groq Rem %s fallback enabled: %s", mode_label, fallback_model_id)
    else:
        logger.warning("GROQ_API_KEY not configured; Rem has no %s fallback.", "vision" if vision_mode else "text")

    # Share one database object across text and vision agents so session history
    # remains unified and concurrent operations use the same configured backend.
    db = session_db if session_db is not None else build_session_db(settings)
    logger.info("Agno history database: %s", "PostgreSQL" if settings.database_url else "local SQLite development fallback")

    tools = [
        # First-party Agno toolkit, narrowed with runtime scope checks.
        ScopedDiscordTools(bot_token=settings.discord_token or ""),
        # Discord.py actions absent from the built-in toolkit (reactions, timeout and context retrieval).
        RemDiscordActionTools(discord_client),
        # Current Internet search for up-to-date Minecraft facts and general questions.
        WebSearchTools(backend="auto", enable_search=True, enable_news=True, timeout=15),
        # Dedicated YouTube discovery, not video watching/transcription/inspection.
        YouTubeSearchTools(timeout=12),
        # Current Modrinth catalog search for mod suggestions.
        ModrinthSearchTools(timeout=12),
        # Official CurseForge Minecraft mod catalogue; requires an optional API key.
        CurseForgeSearchTools(api_key=settings.curseforge_api_key, timeout=12),
    ]
    if memory_service is not None:
        # Long-term memory reads and explicit mutations all pass through scoped
        # application checks. Automatic curation is scheduled by the Discord layer.
        tools.append(RemMemoryTools(memory_service))

    return Agent(
        id="rem-discord-agent",
        name="Rem",
        model=Gemini(id=settings.gemini_model, api_key=settings.gemini_api_key),
        fallback_models=fallback_models,
        instructions=SYSTEM_INSTRUCTIONS,
        tools=tools,
        db=db,
        add_history_to_context=True,
        num_history_runs=settings.history_runs,
        add_datetime_to_context=True,
        markdown=True,
        debug_mode=settings.agno_debug_mode,
        debug_level=settings.agno_debug_level,
        tool_call_limit=8,
        # We intentionally do not mutate Agent.additional_context per message.
    )
