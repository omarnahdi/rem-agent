"""Agno tools for explicit long-term memory management.

Automatic curation is handled by RemMemoryService in the background. These tools
are for on-demand recall and explicit user requests to remember, correct, or forget.
"""
from __future__ import annotations

import logging
from typing import Any

from agno.run import RunContext
from agno.tools import Toolkit

from .memory_service import RemMemoryService

logger = logging.getLogger(__name__)


def _metadata(run_context: RunContext | None) -> dict[str, Any]:
    value = getattr(run_context, "metadata", None) or {}
    return value if isinstance(value, dict) else {}


def _dependencies(run_context: RunContext | None) -> dict[str, Any]:
    value = getattr(run_context, "dependencies", None) or {}
    return value if isinstance(value, dict) else {}


def _trusted_context(run_context: RunContext | None) -> dict[str, Any]:
    meta = _metadata(run_context)
    deps = _dependencies(run_context)

    def first(*keys: str) -> Any:
        for key in keys:
            value = meta.get(key)
            if value is None or value == "":
                value = deps.get(key)
            if value is not None and value != "":
                return value
        return None

    try:
        user_id = int(first("trusted_discord_user_id"))
    except (TypeError, ValueError):
        user_id = 0
    try:
        guild_value = first("discord_guild_id", "trusted_discord_guild_id")
        guild_id = int(guild_value) if guild_value not in (None, "") else None
    except (TypeError, ValueError):
        guild_id = None
    try:
        message_value = first("discord_message_id")
        message_id = int(message_value) if message_value not in (None, "") else None
    except (TypeError, ValueError):
        message_id = None
    is_master_value = first("trusted_discord_is_master")
    is_master = is_master_value is True or str(is_master_value).casefold() == "true"
    current_message = str(first("trusted_current_message") or "")
    return {
        "user_id": user_id,
        "guild_id": guild_id,
        "message_id": message_id,
        "is_master": is_master,
        "current_message": current_message,
    }


def _limit(value: Any, default: int = 5) -> int:
    try:
        return max(1, min(8, int(value)))
    except (TypeError, ValueError):
        return default


class RemMemoryTools(Toolkit):
    """Expose scoped Mem0 operations with trusted identity from Agno RunContext."""

    def __init__(self, service: RemMemoryService) -> None:
        self.service = service
        super().__init__(
            name="rem_long_term_memory",
            tools=[],
            async_tools=[
                (self.search_my_memories, "search_my_memories"),
                (self.search_community_memories, "search_community_memories"),
                (self.remember_fact, "remember_fact"),
                (self.update_memory, "update_memory"),
                (self.forget_memory, "forget_memory"),
            ],
            instructions=(
                "Use search_my_memories for the current speaker's private memories and "
                "search_community_memories for server-wide facts in the current guild. "
                "Never search or expose another member's private memories. Use remember_fact "
                "only when the current message explicitly asks Rem to remember something; "
                "automatic background curation handles other memory-worthy facts. Use "
                "update_memory only for an explicit correction/update request and only with a "
                "memory_id returned by a search. Use forget_memory only for an explicit "
                "forget/remove/delete request and only with a previously returned memory_id. "
                "Guests may write their own personal memory; only Master may explicitly mutate "
                "community-wide memories. A tool result with ok=false is a failure."
            ),
        )

    async def search_my_memories(
        self,
        query: str,
        max_results: int = 5,
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Search long-term memories that belong to the current speaker.

        Args:
            query: Focused semantic query for a past preference, project, or fact.
            max_results: Number of results (1-8).
        """
        context = _trusted_context(run_context)
        if context["user_id"] <= 0:
            return {"ok": False, "error": "Trusted Discord speaker ID is unavailable."}
        try:
            return await self.service.search_user_memories(
                user_id=context["user_id"], query=query, limit=_limit(max_results),
            )
        except Exception as exc:
            logger.exception("search_my_memories failed")
            return {"ok": False, "error": f"Memory search failed: {type(exc).__name__}"}

    async def search_community_memories(
        self,
        query: str,
        max_results: int = 5,
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Search shared, non-private memories scoped to this Discord guild.

        Args:
            query: Focused query about a shared server event, rule, or project.
            max_results: Number of results (1-8).
        """
        context = _trusted_context(run_context)
        if context["guild_id"] is None:
            return {"ok": False, "error": "Community memories are available only inside a Discord server."}
        try:
            return await self.service.search_community_memories(
                guild_id=context["guild_id"], query=query, limit=_limit(max_results),
            )
        except Exception as exc:
            logger.exception("search_community_memories failed")
            return {"ok": False, "error": f"Community memory search failed: {type(exc).__name__}"}

    async def remember_fact(
        self,
        fact: str,
        scope: str = "user",
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Store a fact only when the current message explicitly asks Rem to remember it.

        Args:
            fact: One concise fact grounded in the current conversation.
            scope: 'user' for the current speaker; 'community' for a shared server fact (Master-only).
        """
        context = _trusted_context(run_context)
        if context["user_id"] <= 0 or context["message_id"] is None:
            return {"ok": False, "error": "Trusted current-message identity is unavailable."}
        try:
            return await self.service.store_explicit_memory(
                user_id=context["user_id"], guild_id=context["guild_id"],
                message_id=context["message_id"], fact=fact,
                current_message=context["current_message"], scope=scope,
                is_master=context["is_master"],
            )
        except Exception as exc:
            logger.exception("remember_fact failed")
            return {"ok": False, "error": f"Memory store failed: {type(exc).__name__}"}

    async def update_memory(
        self,
        memory_id: int,
        fact: str,
        scope: str = "user",
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Replace a scoped memory after an explicit correction; old version is superseded only after the new vector is saved.

        Args:
            memory_id: SQL memory ID returned by a previous memory search.
            fact: Complete corrected fact in one concise sentence.
            scope: 'user' for the current speaker; 'community' for a shared server fact (Master-only).
        """
        context = _trusted_context(run_context)
        if context["user_id"] <= 0 or context["message_id"] is None:
            return {"ok": False, "error": "Trusted current-message identity is unavailable."}
        try:
            return await self.service.update_explicit_memory(
                user_id=context["user_id"], guild_id=context["guild_id"],
                message_id=context["message_id"], memory_id=int(memory_id), fact=fact,
                current_message=context["current_message"], scope=scope,
                is_master=context["is_master"],
            )
        except Exception as exc:
            logger.exception("update_memory failed")
            return {"ok": False, "error": f"Memory update failed: {type(exc).__name__}"}

    async def forget_memory(
        self,
        memory_id: int,
        scope: str = "user",
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Forget a memory only when the current message explicitly asks for it.

        Args:
            memory_id: SQL memory ID returned by a previous memory search.
            scope: 'user' for the current speaker; 'community' for a shared server fact (Master-only).
        """
        context = _trusted_context(run_context)
        if context["user_id"] <= 0:
            return {"ok": False, "error": "Trusted Discord speaker ID is unavailable."}
        try:
            return await self.service.forget_explicit_memory(
                user_id=context["user_id"], guild_id=context["guild_id"],
                memory_id=int(memory_id), current_message=context["current_message"],
                scope=scope, is_master=context["is_master"],
            )
        except Exception as exc:
            logger.exception("forget_memory failed")
            return {"ok": False, "error": f"Memory forget failed: {type(exc).__name__}"}
