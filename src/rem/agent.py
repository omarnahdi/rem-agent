"""Agno Rem agent, with real registered tools (not prompt-only tool schemas)."""
from __future__ import annotations

import logging
from typing import Any

from agno.agent import Agent
from agno.models.google import Gemini
from agno.models.groq import Groq
from agno.tools.websearch import WebSearchTools

from .database import build_session_db, require_async_session_db
from .discord_tools import RemDiscordActionTools, ScopedDiscordTools
from .curseforge_tools import CurseForgeSearchTools
from .modrinth_tools import ModrinthSearchTools
from .memory_tools import RemMemoryTools
from .memory_service import RemMemoryService
from .persona import SYSTEM_INSTRUCTIONS
from .settings import Settings
from .youtube_tools import YouTubeSearchTools
from .engagement import ENGAGEMENT_INSTRUCTIONS
from .schemas import EngagementDecision
from .observability import langfuse_tool_hook

logger = logging.getLogger(__name__)


def _build_web_search_tools(settings: Settings) -> list[Any]:
    """Prefer Brave's authenticated Search API for general web results.

    Agno's Brave toolkit currently exposes web results; retain Agno's news-only
    search tool so the existing news capability is not silently removed. If the
    optional Brave dependency is missing, fall back to the existing general +
    news search toolkit rather than preventing Rem from starting.
    """
    if settings.brave_api_key:
        try:
            from agno.tools.bravesearch import BraveSearchTools
        except ImportError:
            logger.warning(
                "BRAVE_API_KEY is set but agno.tools.bravesearch could not be imported; "
                "install the brave-search dependency. Falling back to Agno WebSearchTools."
            )
        else:
            logger.info(
                "Brave Search API enabled for general web search; Agno news search remains enabled."
            )
            return [
                BraveSearchTools(
                    api_key=settings.brave_api_key,
                    fixed_max_results=5,
                    fixed_language="en",
                ),
                WebSearchTools(
                    backend="auto",
                    enable_search=False,
                    enable_news=True,
                    timeout=15,
                ),
            ]

    if not settings.brave_api_key:
        logger.info(
            "BRAVE_API_KEY not configured; using Agno WebSearchTools auto backend for web and news search."
        )
    return [WebSearchTools(backend="auto", enable_search=True, enable_news=True, timeout=15)]


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
    db = require_async_session_db(
        session_db if session_db is not None else build_session_db(settings)
    )
    engine = getattr(db, "db_engine", None)
    engine_url = getattr(engine, "url", None)
    driver_name = getattr(engine_url, "drivername", "unknown")
    logger.info(
        "Agno session DB adapter=%s.%s | driver=%s | async=true | backend=%s",
        type(db).__module__,
        type(db).__qualname__,
        driver_name,
        "PostgreSQL" if settings.database_url else "local SQLite development fallback",
    )

    tools = [
        # First-party Agno toolkit, narrowed with runtime scope checks.
        ScopedDiscordTools(bot_token=settings.discord_token or ""),
        # Discord.py actions absent from the built-in toolkit (reactions, timeout and context retrieval).
        RemDiscordActionTools(discord_client),
        # Prefer the official Brave Search API when BRAVE_API_KEY is configured;
        # preserve news search using Agno's news-only WebSearchTools.
        *_build_web_search_tools(settings),
        # Dedicated YouTube discovery, not video watching/transcription/inspection.
        YouTubeSearchTools(timeout=12),
        # Current Modrinth catalog search for mods, resource packs, data packs, shaders, and modpacks.
        # The toolkit injects its own detailed usage instructions into Rem's context.
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
        # Add explicit input/output fields to existing OpenInference TOOL spans;
        # create rem.tool.* spans only when automatic instrumentation has none.
        tool_hooks=[langfuse_tool_hook],
        # We intentionally do not mutate Agent.additional_context per message.
    )



def build_engagement_evaluator(settings: Settings) -> Agent:
    """Build a stateless Gemini structured-output classifier for engagement.

    Do not route this task through Groq GPT-OSS: it has repeatedly returned an
    assistant message with no content in production. Gemini is selected explicitly
    even when GROQ_API_KEY is configured. The Pydantic schema is sent through
    Agno's native structured-output path; JSON-mode prompting is intentionally off.
    """
    if not settings.gemini_api_key:
        raise RuntimeError("Engagement evaluation requires GEMINI_API_KEY.")
    if not settings.gemini_engagement_model:
        raise RuntimeError("GEMINI_ENGAGEMENT_MODEL must not be empty.")

    model = Gemini(
        id=settings.gemini_engagement_model,
        api_key=settings.gemini_api_key,
        temperature=0,
        max_output_tokens=512,
    )
    logger.info(
        "Conversation engagement evaluator enabled | provider=Gemini | model=%s | schema=EngagementDecision",
        settings.gemini_engagement_model,
    )
    return Agent(
        id="rem-engagement-evaluator",
        name="Rem Engagement Evaluator",
        model=model,
        instructions=ENGAGEMENT_INSTRUCTIONS,
        # Gemini supports native structured outputs with Pydantic schemas.
        # Do not set use_json_mode=True: that is prompt-based JSON mode, not the
        # provider's native constrained structured-output interface.
        output_schema=EngagementDecision,
        markdown=False,
        add_history_to_context=False,
        debug_mode=False,
    )
