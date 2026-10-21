"""Rem built on Agno's official DiscordClient, with a version-pinned extension layer."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from agno.agent import Agent
from agno.integrations.discord import DiscordClient
from agno.media import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Ensure moderation.py reads GROQ_API_KEY after .env is loaded.
load_dotenv()

from .agent import build_agent, build_engagement_evaluator
from .database import build_session_db
from .commands import apply_and_verify_timeout, parse_timeout_command
from .context import build_message_context, detect_pending_youtube_confirmation
from .identity import identify_speaker, session_id_for
from .engagement import ConversationEngagementGate, is_direct_rem_request
from .moderation import moderate_message
from .memory_service import RemMemoryService
from .session_locks import SessionLockPool
from .settings import Settings
from .text_utils import collect_verified_project_links, looks_like_raw_tool_call_payload, normalize_project_markdown_links
from .observability import (
    configure_observability,
    install_asyncio_exception_handler,
    shutdown_observability,
    set_span_input_output,
    trace_span,
)

logger = logging.getLogger("rem.discord")
SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
MAX_IMAGES_PER_MESSAGE = 8
MAX_IMAGE_ATTACHMENT_BYTES = 7 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 7 * 1024 * 1024


class RemAgnoDiscordClient(DiscordClient):
    """Agno DiscordClient with Rem policies and tested feature hooks.

    This deliberately subclasses Agno's official adapter and uses its response/HITL
    machinery. `_setup_events` is version-pinned because upstream currently has no
    public pre-message middleware hook; the copied event flow mirrors Agno 3.1.3,
    while adding Master-only DMs, legacy moderation, bounded context and multi-image input.
    """

    def __init__(
        self,
        *,
        agent: Agent,
        vision_agent: Agent,
        client: discord.Client,
        settings: Settings,
        moderation_client: Any,
        memory_service: RemMemoryService | None = None,
        engagement_evaluator: Agent | None = None,
    ) -> None:
        self.settings = settings
        self.moderation_client = moderation_client
        self.vision_agent = vision_agent
        self.memory_service = memory_service
        self._session_locks = SessionLockPool()
        self.engagement_gate = ConversationEngagementGate(
            evaluator=engagement_evaluator,
            context_loader=build_message_context,
            response_callback=self._handle_spontaneous_response,
            enabled=bool(getattr(settings, "engagement_enabled", True)),
            batch_window_ms=int(getattr(settings, "engagement_batch_window_ms", 1500)),
            min_eval_interval_seconds=int(getattr(settings, "engagement_min_eval_interval_seconds", 8)),
        )
        super().__init__(agent=agent, client=client)

    async def _handle_spontaneous_response(self, message: discord.Message, history_context: str) -> None:
        """Re-enter the shared policy/agent pipeline after the evaluator approves a burst.

        Moderation already passed before the message was queued. The gate injects
        the freshly collected prior history, and this call retrieves Mem0 memories
        only now, when the full conversational agent is actually going to run.
        """
        await self._handle_rem_message(
            message,
            _skip_moderation=True,
            _auto_approved=True,
            _context_override=history_context,
        )

    def _setup_events(self) -> None:
        @self.client.event
        async def on_message(message: discord.Message) -> None:
            await self._handle_rem_message(message)

        @self.client.event
        async def on_ready() -> None:
            # The runtime creates its event loop inside Agno's serve(); on_ready
            # is the first safe point to capture otherwise-unretrieved task errors.
            install_asyncio_exception_handler(asyncio.get_running_loop())

    async def _handle_rem_message(
        self,
        message: discord.Message,
        *,
        _skip_moderation: bool = False,
        _auto_approved: bool = False,
        _context_override: str | None = None,
    ) -> None:
        bot_user = self.client.user
        if bot_user is None or message.author.bot or message.author.id == bot_user.id or message.webhook_id is not None:
            return

        observed_at = datetime.now(timezone.utc).isoformat()
        source_created_at = message.created_at.astimezone(timezone.utc).isoformat()
        is_master = int(message.author.id) == self.settings.master_discord_id

        # Policy gate happens before moderation, context collection or Agent execution.
        # Non-Master DMs never enter the model, tools or session database.
        if message.guild is None and not is_master:
            logger.info("Ignored non-Master DM | author_id=%s | message_id=%s", message.author.id, message.id)
            return

        # Every accessible guild text channel/thread is eligible. Channel allowlists
        # were deliberately removed: Rem must not silently ignore messages because
        # a channel ID was omitted from a beta allowlist. Discord permissions still
        # determine which channels and actions the bot can actually access.

        # The old AI moderation runs on ordinary server messages, not only messages that address Rem.
        if message.guild is not None and not is_master and not _skip_moderation:
            try:
                with trace_span(
                    "rem.discord.moderation",
                    {
                        "discord.message.id": str(message.id),
                        "discord.guild.id": str(message.guild.id),
                        "discord.channel.id": str(getattr(message.channel, "id", "")),
                        "rem.user.role": "GUEST",
                        "rem.component": "moderation",
                    },
                ) as moderation_span:
                    set_span_input_output(
                        moderation_span,
                        input_value={
                            "message_content": message.content,
                            "attachment_count": len(message.attachments),
                            "author_id": str(message.author.id),
                        },
                    )
                    handled = await moderate_message(
                        gemini=self.moderation_client,
                        message=message,
                        master_id=self.settings.master_discord_id,
                        bot_user_id=bot_user.id,
                    )
                    moderation_span.set_attribute("rem.moderation.handled", bool(handled))
                    set_span_input_output(moderation_span, output_value={"handled": bool(handled)})
                if handled:
                    logger.info(
                        "Message stopped by moderation policy (action taken or fail-closed) | message_id=%s",
                        message.id,
                    )
                    return
            except Exception:
                # Moderation is mandatory. Unexpected errors fail closed: do not
                # send an unchecked guest message to the conversational agent.
                logger.exception(
                    "Moderation pipeline failed | message_id=%s; skipping agent run and taking no automatic punishment",
                    message.id,
                )
                return

        is_reply_to_rem, referenced_message = await self._resolve_reply(message)
        # Timeout actions stay explicit and Master-only. A clear textual address such
        # as "Rem give @user a 6 minute timeout" is equivalent to a real @Rem mention;
        # ordinary messages merely discussing Rem still fail the timeout parser.
        explicitly_addressed_by_name = is_direct_rem_request(message.content or "")
        if is_master and message.guild is not None and (
            bot_user in message.mentions or is_reply_to_rem or explicitly_addressed_by_name
        ):
            parsed_timeout = parse_timeout_command(
                message,
                author_id=message.author.id,
                bot_id=bot_user.id,
                referenced_message=referenced_message,
            )
            if parsed_timeout:
                with trace_span(
                    "rem.discord.timeout_action",
                    {
                        "discord.message.id": str(message.id),
                        "discord.guild.id": str(message.guild.id),
                        "discord.channel.id": str(getattr(message.channel, "id", "")),
                        "rem.action.target_user_id": str(parsed_timeout.get("user_id", "")),
                        "rem.action.requested_seconds": int(parsed_timeout.get("seconds", 0) or 0),
                        "rem.action.type": "member_timeout",
                    },
                ) as timeout_span:
                    set_span_input_output(
                        timeout_span,
                        input_value={
                            "command": message.content,
                            "target_user_id": str(parsed_timeout.get("user_id", "")),
                            "duration_seconds": int(parsed_timeout.get("seconds", 0) or 0),
                        },
                    )
                    await self._apply_explicit_timeout(message, parsed_timeout)
                    timeout_span.set_attribute("rem.action.processed", True)
                    set_span_input_output(
                        timeout_span,
                        output_value={"processed": True, "target_user_id": str(parsed_timeout.get("user_id", ""))},
                    )
                return

        message_text = (message.content or "").strip()
        image_inputs, ignored_media = await self._collect_images(message)
        if not message_text and image_inputs:
            message_text = "Please inspect the attached image(s) and help me understand what they show."
        elif not message_text and ignored_media:
            message_text = "The user sent attachment(s) that aren't supported by visual input yet. Explain briefly that you couldn't inspect those attachment(s), and ask for supported still images or a text description."
        elif not message_text:
            message_text = "The user sent an empty Discord message. Respond briefly and naturally if appropriate."
        if ignored_media and message.content.strip():
            message_text += "\n\n[Application note: one or more attachments were not passed to visual analysis. Do not claim to have seen those attachments.]"

        original_channel = message.channel
        message_channel_id = getattr(original_channel, "id", None)
        # For a message inside a thread, retain both the thread and its parent
        # text channel so history/search tools can see the relevant conversation.
        if isinstance(original_channel, discord.Thread):
            source_channel_id = getattr(original_channel, "parent_id", None) or message_channel_id
        else:
            source_channel_id = message_channel_id
        # Do not create a thread for ordinary channel messages. Reply in the
        # channel where the user wrote; if the message is already in a thread,
        # continue that existing thread. DMs continue in their DM channel.
        if isinstance(original_channel, (discord.Thread, discord.DMChannel, discord.TextChannel)):
            thread: Any = original_channel
        else:
            logger.info(
                "Ignored unsupported Discord channel type=%s id=%s",
                type(original_channel).__name__,
                getattr(original_channel, "id", None),
            )
            return

        conversation_channel_id = getattr(thread, "id", None)
        actual_thread_id = thread.id if isinstance(thread, discord.Thread) else None
        guild_id = message.guild.id if message.guild else None
        identity = identify_speaker(
            discord_user_id=message.author.id,
            display_name=message.author.display_name,
            username=message.author.name,
            master_discord_id=self.settings.master_discord_id,
        )
        # Keep a bounded in-process observation buffer for every eligible message.
        # The returned text represents earlier observed messages and costs no model tokens.
        context_before = self.engagement_gate.observe(message)
        direct_request = _auto_approved or is_direct_rem_request(
            message_text,
            mentioned=bool(bot_user in message.mentions),
            replied_to_rem=is_reply_to_rem,
            master_dm=(message.guild is None and is_master),
        )
        if direct_request:
            if not _auto_approved:
                self.engagement_gate.cancel_pending_for(message)
            if _context_override is not None:
                context = _context_override
            else:
                with trace_span(
                    "rem.discord.build_context",
                    {
                        "discord.message.id": str(message.id),
                        "discord.guild.id": str(message.guild.id) if message.guild else "dm",
                        "discord.channel.id": str(getattr(message.channel, "id", "")),
                        "rem.component": "discord_context",
                    },
                ) as context_span:
                    set_span_input_output(
                        context_span,
                        input_value={
                            "message_content": (message.content or "").strip(),
                            "message_id": str(message.id),
                            "referenced_message_id": str(getattr(getattr(message, "reference", None), "message_id", "") or ""),
                        },
                    )
                    context = await build_message_context(message)
                    set_span_input_output(
                        context_span,
                        output_value={"context": context, "context_chars": len(context or "")},
                    )
        else:
            # Ordinary messages use only the cheap in-memory buffer. Discord
            # history is fetched after the pre-filter selects a candidate burst.
            context = _context_override if _context_override is not None else context_before

        pending_youtube_confirmation = detect_pending_youtube_confirmation(message_text, context)

        # Long-term memory observation is independent from conversation response.
        # For ordinary chat, record eligible events and launch curation when useful,
        # but do not perform semantic Mem0 searches unless the agent will reply.
        memory_context = ""
        if self.memory_service is not None and message.content.strip():
            try:
                memory_args = {
                    "message_id": message.id,
                    "user_id": message.author.id,
                    "guild_id": message.guild.id if message.guild else None,
                    "channel_id": message_channel_id or source_channel_id or 0,
                    "display_name": message.author.display_name,
                    "content": message.content,
                    "source_created_at": message.created_at.astimezone(timezone.utc),
                    "channel_context": context,
                    "retrieve_memories": direct_request,
                }
                if direct_request:
                    with trace_span(
                        "rem.memory.prepare_message",
                        {
                            "discord.message.id": str(message.id),
                            "discord.guild.id": str(guild_id) if guild_id else "dm",
                            "discord.channel.id": str(message_channel_id or source_channel_id or ""),
                            "rem.memory.retrieve_requested": True,
                        },
                    ) as memory_span:
                        set_span_input_output(
                            memory_span,
                            input_value={
                                "message_content": message.content,
                                "channel_context": context,
                                "retrieve_memories": True,
                                "user_id": str(message.author.id),
                            },
                        )
                        memory_context = await self.memory_service.prepare_message(**memory_args)
                        memory_span.set_attribute("rem.memory.context_available", bool(memory_context))
                        set_span_input_output(
                            memory_span,
                            output_value={"memory_context": memory_context, "context_available": bool(memory_context)},
                        )
                else:
                    memory_context = await self.memory_service.prepare_message(**memory_args)
            except Exception:
                logger.exception(
                    "Long-term memory preparation failed; continuing without memory context | message_id=%s",
                    message.id,
                )

        if not direct_request:
            queued = self.engagement_gate.consider(
                message,
                has_attachments=bool(message.attachments),
                recent_context=context_before,
            )
            logger.debug(
                "Observed guild conversation message | message_id=%s | engagement_candidate=%s | message_chars=%d",
                message.id,
                queued,
                len(message.content or ""),
            )
            return

        # A separate metadata dictionary is injected into RunContext for tools; trusted
        # dependencies are added to model context. The Agent itself is never mutated per run.
        dependencies = {
            "trusted_discord_user_id": str(identity.discord_user_id),
            "trusted_discord_display_name": identity.display_name,
            "trusted_discord_username": identity.username,
            "trusted_discord_role": identity.role,
            "trusted_discord_is_master": "true" if identity.is_master else "false",
            "trusted_current_message": message_text,
            "trusted_youtube_confirmation_pending": pending_youtube_confirmation,
            "discord_context": context,
            "discord_message_id": str(message.id),
            "discord_message_url": message.jump_url,
            "discord_source_channel_id": str(source_channel_id) if source_channel_id is not None else "",
            "discord_message_channel_id": str(message_channel_id) if message_channel_id is not None else "",
            "discord_thread_id": str(actual_thread_id) if actual_thread_id is not None else "",
            "discord_guild_id": str(guild_id) if guild_id is not None else "",
            "discord_current_channel_id": str(conversation_channel_id or message_channel_id or source_channel_id or ""),
            "discord_source_created_at": source_created_at,
            "discord_observed_at": observed_at,
            "discord_mentioned_rem": bot_user in message.mentions,
            "discord_is_reply_to_rem": is_reply_to_rem,
            "discord_mentions": [
                {"id": str(member.id), "display_name": member.display_name, "username": member.name}
                for member in message.mentions if not member.bot
            ],
        }
        if memory_context:
            dependencies["rem_long_term_memory_context"] = memory_context
        run_metadata = {
            "trusted_discord_user_id": str(identity.discord_user_id),
            "trusted_discord_is_master": identity.is_master,
            "discord_source_channel_id": str(source_channel_id) if source_channel_id is not None else "",
            "discord_message_channel_id": str(message_channel_id) if message_channel_id is not None else "",
            "discord_thread_id": str(actual_thread_id) if actual_thread_id is not None else "",
            "discord_current_channel_id": str(conversation_channel_id or message_channel_id or source_channel_id or ""),
            "discord_guild_id": str(guild_id) if guild_id is not None else "",
            "discord_message_id": str(message.id),
        }
        # Agno databases associate a session with one user ID. A Discord thread
        # is shared by multiple humans, so reusing the bare thread ID with each
        # author's distinct Agno user_id can cause ownership conflicts. Keep each
        # person's durable Agent session isolated and build shared context from
        # Discord history instead.
        session_channel_id = conversation_channel_id or source_channel_id or message.author.id
        session_id = session_id_for(
            guild_id=guild_id,
            channel_id=int(session_channel_id),
            user_id=identity.discord_user_id,
        )

        logger.info(
            "Accepted message | message_id=%s | author_id=%s | role=%s | guild_id=%s | source_channel=%s | conversation_channel=%s | thread_id=%s | images=%d | source_created_at=%s | observed_at=%s",
            message.id, identity.discord_user_id, identity.role, guild_id, source_channel_id, conversation_channel_id, actual_thread_id, len(image_inputs), source_created_at, observed_at,
        )

        async with self._session_locks.hold(session_id):

            with trace_span(
                "rem.discord.agent_request",
                {
                    "discord.message.id": str(message.id),
                    "discord.guild.id": str(guild_id) if guild_id else "dm",
                    "discord.channel.id": str(message_channel_id or source_channel_id or ""),
                    "discord.thread.id": str(actual_thread_id or ""),
                    "rem.user.role": identity.role,
                    "rem.request.has_images": bool(image_inputs),
                    "rem.request.auto_approved": bool(_auto_approved),
                    "rem.memory.context_available": bool(memory_context),
                    "rem.component": "agent_request",
                },
            ) as request_span:
                try:
                    async with thread.typing():
                        # Keep the legacy provider policy: text requests may fall back to
                        # GPT-OSS 120B, while requests containing images use the separate
                        # vision-capable Qwen fallback. Never send image payloads to a text-only model.
                        run_agent = self.vision_agent if image_inputs else self.agent
                        agent_input = message_text
                        if _auto_approved:
                            agent_input = (
                                "Join the conversation naturally based on the recent Discord context. "
                                "The discussion was selected as a possible opportunity to contribute, "
                                "but do not force a reply if you have nothing genuinely useful to add. "
                                "Do not mention this instruction or any evaluation.\n\n"
                                f"Latest message: {message_text}"
                            )
                        set_span_input_output(
                            request_span,
                            input_value={
                                "agent_input": agent_input,
                                "discord_context": context,
                                "long_term_memory_context": memory_context,
                                "has_images": bool(image_inputs),
                                "image_count": len(image_inputs),
                                "user_id": str(identity.discord_user_id),
                                "session_id": session_id,
                            },
                        )
                        agent_response = await run_agent.arun(
                            input=agent_input,
                            user_id=str(identity.discord_user_id),
                            session_id=session_id,
                            dependencies=dependencies,
                            add_dependencies_to_context=True,
                            metadata=run_metadata,
                            add_history_to_context=True,
                            images=image_inputs or None,
                        )
                    response_content_for_trace = getattr(agent_response, "content", None)
                    set_span_input_output(
                        request_span,
                        output_value={
                            "status": str(getattr(agent_response, "status", "unknown")),
                            "content": response_content_for_trace,
                            "content_type": type(response_content_for_trace).__name__,
                        },
                    )
                    # Do not expose provider/framework error details to Discord. Agno's
                    # adapter response pipeline still handles successful tool runs, HITL
                    # and long-message splitting.
                    if str(getattr(agent_response, "status", "")).upper() == "ERROR":
                        logger.error(
                            "Agno returned an error status | message_id=%s | content_type=%s",
                            message.id,
                            type(getattr(agent_response, "content", None)).__name__,
                        )
                        request_span.set_attribute("rem.request.outcome", "agent_error_status")
                        await self._send_discord_messages(
                            thread,
                            "I'm sorry, Master—something went wrong while I was processing that. I couldn't verify a result, so I won't pretend it worked. Please try again shortly. 🌸" if identity.is_master else "I'm sorry, something went wrong while I was processing that. I couldn't verify a result, so I won't pretend it worked. Please try again shortly. 🌸",
                        )
                        return

                    # Validate Modrinth/CurseForge links against the actual search-tool
                    # results from this agent run. This repairs Markdown residue and
                    # corrects a wrong destination when its displayed project title maps
                    # to a returned result. Unknown project links become non-clickable text.
                    response_content = getattr(agent_response, "content", None)
                    if isinstance(response_content, str):
                        verified_project_links = collect_verified_project_links(agent_response)
                        normalized_content = normalize_project_markdown_links(
                            response_content,
                            verified_links=verified_project_links,
                        )
                        if normalized_content != response_content:
                            logger.warning(
                                "Normalized/validated Minecraft project links | message_id=%s | "
                                "verified_project_aliases=%d",
                                message.id,
                                len(verified_project_links),
                            )
                            agent_response.content = normalized_content

                    # Use Agno's response/HITL/long-message handling. This is where real
                    # registered tools complete before a final natural-language response.
                    await self._handle_response_in_thread(agent_response, thread)
                    request_span.set_attribute("rem.request.outcome", "delivered")
                    logger.info("Agent response delivered | message_id=%s | role=%s", message.id, identity.role)
                except Exception as exc:
                    request_span.set_attribute("rem.request.outcome", "exception")
                    set_span_input_output(
                        request_span,
                        output_value={
                            "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                            "error": str(exc),
                        },
                    )
                    logger.exception("Agno Discord message processing failed | message_id=%s", message.id)
                    try:
                        await thread.send(
                            "I'm sorry, I couldn't complete that properly just now. Please try again in a moment.",
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except discord.HTTPException:
                        logger.exception("Could not send processing error message | message_id=%s", message.id)

    async def _send_discord_messages(self, thread: Any, message: str, italics: bool = False) -> None:
        """Keep Agno response handling while blocking raw function-call JSON from chat."""
        content = message or ""
        if not content.strip():
            logger.warning("Agno returned an empty user-facing response")
            content = "I couldn't form a proper reply this time. Please try again in a moment. 🌸"
        if looks_like_raw_tool_call_payload(content):
            logger.error("Blocked raw tool-call payload from Discord output; tool execution/result flow was not represented as a user-facing answer.")
            content = "I couldn't complete that request properly, so I can't give you verified results yet. Please try again in a moment. 🌸"
            italics = False
        chunks = [content[i:i + 1500] for i in range(0, len(content), 1500)] if len(content) >= 1500 else [content]
        for index, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                chunk = f"[{index}/{len(chunks)}] {chunk}"
            if italics:
                chunk = "\n".join(f"_{line}_" for line in chunk.split("\n"))
            await thread.send(chunk, allowed_mentions=discord.AllowedMentions.none())

    async def _collect_images(self, message: discord.Message) -> tuple[list[Image], bool]:
        image_inputs: list[Image] = []
        ignored_media = False
        total_image_bytes = 0
        for attachment in message.attachments:
            mime = (attachment.content_type or mimetypes.guess_type(attachment.filename)[0] or "").casefold()
            if mime == "image/gif" or (attachment.filename or "").casefold().endswith(".gif"):
                # GIF input is explicitly deferred to a later phase; video input is not required.
                ignored_media = True
                continue
            if mime.startswith("video/"):
                # Video input is deliberately not part of this migration.
                ignored_media = True
                continue
            if mime not in SUPPORTED_IMAGE_MIMES:
                ignored_media = True
                continue
            if len(image_inputs) >= MAX_IMAGES_PER_MESSAGE:
                logger.warning("Image attachment limit reached | message_id=%s", message.id)
                ignored_media = True
                break
            if attachment.size and attachment.size > MAX_IMAGE_ATTACHMENT_BYTES:
                logger.warning("Skipping oversized image %s (%d bytes)", attachment.filename, attachment.size)
                ignored_media = True
                continue
            if attachment.size and total_image_bytes + attachment.size > MAX_TOTAL_IMAGE_BYTES:
                logger.warning("Image payload budget reached; skipping %s", attachment.filename)
                ignored_media = True
                continue
            image_inputs.append(Image(url=attachment.url))
            total_image_bytes += int(attachment.size or 0)
        return image_inputs, ignored_media

    async def _resolve_reply(self, message: discord.Message) -> tuple[bool, Any | None]:
        reference = message.reference
        if reference is None or reference.message_id is None:
            return False, None
        referenced: Any = reference.resolved
        if not isinstance(referenced, discord.Message):
            try:
                referenced = await message.channel.fetch_message(reference.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False, None
        return bool(self.client.user and referenced.author.id == self.client.user.id), referenced

    async def _apply_explicit_timeout(self, message: discord.Message, request: dict[str, Any]) -> None:
        guild = message.guild
        if guild is None:
            return
        target_id = int(request["user_id"])
        bot_user = self.client.user
        if bot_user and target_id == bot_user.id:
            await message.reply("I can't timeout myself, Master. 🥺", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            return
        try:
            member = guild.get_member(target_id) or await guild.fetch_member(target_id)
            me = guild.me
            if me is None or not me.guild_permissions.moderate_members:
                await message.reply("Master, I don't have the Timeout Members permission. 🥺", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                return
            if member.bot:
                await message.reply("I won't timeout another bot, Master. 🥺", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                return
            if member.top_role >= me.top_role:
                await message.reply("I can't timeout that member because their highest role is equal to or higher than mine, Master. 🥺", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                return
            seconds = int(request["seconds"])
            if seconds <= 0 or seconds > 28 * 24 * 3600:
                await message.reply("Please give me a timeout duration between 1 second and 28 days, Master. 🥺", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                return
            # Apply an exact UTC deadline and verify the version-compatible
            # member timeout property with brief retries for transient stale reads.
            timeout_result = await apply_and_verify_timeout(
                guild,
                member,
                seconds,
                reason="Manual timeout requested by Master via Rem",
            )
            expected_until = timeout_result["expected_until"]
            until = timeout_result["actual_until"]
            if not timeout_result["verified"]:
                await message.reply(
                    "Discord didn't confirm the requested timeout deadline, Master, "
                    "so I won't claim it succeeded. 🥺",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                logger.error(
                    "Timeout deadline mismatch | target_id=%s | expected=%s | actual=%s | "
                    "attempts=%s | verification_error=%r",
                    target_id,
                    expected_until.isoformat(),
                    until.isoformat() if until else None,
                    timeout_result["attempts_used"],
                    timeout_result["verification_error"],
                )
                return
            logger.info(
                "TIMEOUT SUCCESS | target_id=%s | seconds=%s | until=%s",
                target_id,
                seconds,
                until.isoformat(),
            )
            await message.reply(
                f"Right away, Master. 🌸 {member.display_name} has been timed out "
                f"for {request['amount']} {request['unit']}.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            logger.exception("Discord forbade timeout | target_id=%s", target_id)
            await message.reply("Discord refused the timeout, Master. Please check my permissions and role hierarchy. 🥺", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.exception("Discord timeout API error | target_id=%s", target_id)
            await message.reply("The timeout request failed in Discord, Master. I haven't marked it as successful. 🥺", mention_author=False, allowed_mentions=discord.AllowedMentions.none())


def create_discord_client(settings: Settings) -> RemAgnoDiscordClient:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = discord.Client(intents=intents)
    # Both Agent instances share the same session DB and ID so they use one
    # history store, while preserving distinct text-only and vision fallbacks.
    session_db = build_session_db(settings)
    memory_service = RemMemoryService(settings)
    agent = build_agent(
        settings, discord_client=client, session_db=session_db, vision_mode=False,
        memory_service=memory_service,
    )
    vision_agent = build_agent(
        settings, discord_client=client, session_db=session_db, vision_mode=True,
        memory_service=memory_service,
    )
    moderation_client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=180000),
    )
    if not memory_service.enabled:
        logger.warning(
            "Long-term memory is disabled: set REM_MEMORY_DATABASE_URL (or NEON_DATABASE_URL) and GEMINI_API_KEY in .env"
        )
    engagement_evaluator = build_engagement_evaluator(settings) if settings.engagement_enabled else None
    if settings.engagement_enabled and engagement_evaluator is None:
        logger.warning("Spontaneous engagement is enabled but no evaluator could be built")
    return RemAgnoDiscordClient(
        agent=agent, vision_agent=vision_agent, client=client, settings=settings,
        moderation_client=moderation_client, memory_service=memory_service,
        engagement_evaluator=engagement_evaluator,
    )


def run_bot() -> None:
    """Run through Agno DiscordClient.serve(), keeping the official entry point."""
    load_dotenv()
    settings = Settings.from_env(load_env_file=False)
    settings.validate_runtime()
    configured_log_level = getattr(logging, settings.log_level, logging.INFO)
    logging.basicConfig(
        level=configured_log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Agno's Agent(debug_mode=True) emits detailed diagnostics through its own
    # logger hierarchy. Keep ordinary application logs at LOG_LEVEL while
    # allowing Agno DEBUG records when explicitly enabled for beta diagnosis.
    logging.getLogger("agno").setLevel(
        logging.DEBUG if settings.agno_debug_mode else configured_log_level
    )
    logger.info(
        "Logging configured | root_level=%s | agno_debug_mode=%s | agno_debug_level=%s",
        logging.getLevelName(configured_log_level),
        settings.agno_debug_mode,
        settings.agno_debug_level,
    )
    # Register OpenTelemetry before constructing agents so spans share a provider.
    configure_observability(service_name="rem-agent")
    # Agno 3.1.3's serve() reads this standard name. Preserve compatibility with
    # the legacy DISCORD_TOKEN setting accepted by Settings.
    if settings.discord_token:
        os.environ["DISCORD_BOT_TOKEN"] = settings.discord_token
    try:
        with trace_span("rem.application.startup", {"service.name": "rem-agent"}) as startup_span:
            set_span_input_output(
                startup_span,
                input_value={
                    "service": "rem-agent",
                    "database_configured": bool(settings.database_url),
                    "memory_database_configured": bool(settings.memory_database_url),
                    "langfuse_configured": bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")),
                },
            )
            integration = create_discord_client(settings)
            set_span_input_output(
                startup_span,
                output_value={"discord_client_created": integration is not None, "status": "initialized"},
            )
        integration.serve()
    finally:
        shutdown_observability()


def main() -> None:
    try:
        run_bot()
    except KeyboardInterrupt:
        logger.info("Rem stopped by keyboard interrupt.")


if __name__ == "__main__":
    main()
