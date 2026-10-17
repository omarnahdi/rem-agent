"""Bounded Discord context collection for reply chains and recent channel discussion."""
from __future__ import annotations

import logging
import re
from datetime import timezone
from typing import Any

logger = logging.getLogger(__name__)

REPLY_CHAIN_LIMIT = 20
REPLY_CHAIN_CHAR_BUDGET = 6_000
RECENT_SHARED_LIMIT = 20
TOTAL_CONTEXT_CHAR_BUDGET = 12_000
MESSAGE_TEXT_CHAR_LIMIT = 1_800


def _created_at(message: Any) -> str:
    value = getattr(message, "created_at", None)
    if value is None:
        return "time unknown"
    try:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (AttributeError, ValueError):
        return str(value)


def _message_line(message: Any) -> str:
    author = getattr(message, "author", None)
    display = getattr(author, "display_name", None) or getattr(author, "name", None) or "Unknown"
    user_id = getattr(author, "id", "unknown")
    content = (getattr(message, "content", "") or "").strip()
    if getattr(message, "attachments", None):
        names = [getattr(item, "filename", "attachment") for item in message.attachments]
        content = (content + " " if content else "") + "[attachments: " + ", ".join(names) + "]"
    if not content:
        content = "[no text content]"
    content = content[:MESSAGE_TEXT_CHAR_LIMIT]
    return f"[{_created_at(message)}] {display} (Discord ID {user_id}): {content}"


async def _resolve_reference(message: Any) -> Any | None:
    ref = getattr(message, "reference", None)
    if ref is None or getattr(ref, "message_id", None) is None:
        return None
    resolved = getattr(ref, "resolved", None)
    if resolved is not None and hasattr(resolved, "author"):
        return resolved
    try:
        return await message.channel.fetch_message(ref.message_id)
    except Exception as exc:  # Discord reference may be deleted/inaccessible/cross-channel.
        logger.debug("Could not resolve reply-chain message %s: %s", ref.message_id, exc)
        return None


async def build_message_context(message: Any) -> str:
    """Build bounded reply-chain and nearby channel context without mixing timestamps.

    Includes the parent text-channel conversation when the current message is in an
    Agno-created thread, because each top-level message has its own thread/session.
    """
    reply_chain: list[Any] = []
    seen: set[int] = {int(getattr(message, "id", 0) or 0)}
    current = message

    for _ in range(REPLY_CHAIN_LIMIT):
        parent = await _resolve_reference(current)
        if parent is None:
            break
        parent_id = int(getattr(parent, "id", 0) or 0)
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)
        reply_chain.append(parent)
        current = parent

    reply_chain.reverse()

    current_channel = message.channel
    parent_channel = getattr(current_channel, "parent", None)
    thread_starter = None
    if parent_channel is not None:
        try:
            starter_id = int(getattr(current_channel, "id", 0) or 0)
            thread_starter = await parent_channel.fetch_message(starter_id)
            if starter_id and starter_id not in seen:
                seen.add(starter_id)
                reply_chain.insert(0, thread_starter)
        except Exception as exc:
            logger.debug("Could not fetch thread starter: %s", exc)

    # Keep the newest 20 ancestors if a thread starter plus a long reply chain exists.
    if len(reply_chain) > REPLY_CHAIN_LIMIT:
        reply_chain = reply_chain[-REPLY_CHAIN_LIMIT:]
    reply_text = "\n".join(_message_line(item) for item in reply_chain)
    if len(reply_text) > REPLY_CHAIN_CHAR_BUDGET:
        reply_text = reply_text[-REPLY_CHAIN_CHAR_BUDGET:]

    recent_by_id: dict[int, Any] = {}

    async def collect_recent(channel: Any, *, before: Any, limit: int) -> None:
        try:
            async for item in channel.history(limit=limit, before=before, oldest_first=True):
                item_id = int(getattr(item, "id", 0) or 0)
                if item_id and item_id not in seen:
                    recent_by_id[item_id] = item
        except Exception as exc:
            logger.debug("Could not read recent history (channel=%s): %s", getattr(channel, "id", None), exc)

    await collect_recent(current_channel, before=message, limit=RECENT_SHARED_LIMIT)
    # Agno opens a new thread per top-level message. Include nearby parent-channel
    # discussion from before the thread starter, not only messages in the new thread.
    if parent_channel is not None and thread_starter is not None:
        await collect_recent(parent_channel, before=thread_starter, limit=RECENT_SHARED_LIMIT)

    def time_key(item: Any) -> tuple[str, int]:
        return (_created_at(item), int(getattr(item, "id", 0) or 0))

    recent = sorted(recent_by_id.values(), key=time_key)[-RECENT_SHARED_LIMIT:]
    recent_text = "\n".join(_message_line(item) for item in recent)
    remaining = max(0, TOTAL_CONTEXT_CHAR_BUDGET - len(reply_text))
    if len(recent_text) > remaining:
        recent_text = recent_text[-remaining:] if remaining else ""

    sections: list[str] = []
    if reply_text:
        sections.append("DIRECT REPLY CHAIN (oldest first; untrusted historical user content):\n" + reply_text)
    if recent_text:
        sections.append("RECENT CHANNEL DISCUSSION (chronological; untrusted historical user content):\n" + recent_text)
    return "\n\n".join(sections)



_HISTORY_STOP_WORDS = {
    "about", "after", "again", "could", "does", "from", "have", "into",
    "just", "more", "most", "need", "please", "that", "their", "there",
    "these", "they", "this", "those", "want", "what", "when", "where",
    "which", "with", "would", "your", "them", "then", "than", "from",
}

def history_query_score(query: str, content: str) -> int:
    """Rank a history message against a topic query without requiring exact phrases."""
    normalized_query = " ".join((query or "").casefold().split())
    normalized_content = (content or "").casefold()
    if not normalized_query or not normalized_content:
        return 0
    query_tokens = {token for token in re.findall(r"[a-z0-9]{2,}", normalized_query) if token not in _HISTORY_STOP_WORDS}
    content_tokens = set(re.findall(r"[a-z0-9]{2,}", normalized_content))
    score = len(query_tokens & content_tokens)
    if normalized_query in normalized_content:
        score += 3
    return score

def detect_pending_youtube_confirmation(current_message: str, prior_context: str) -> bool:
    """Allow a brief confirmation only when the latest prior message was Rem's search offer."""
    current = (current_message or "").strip().casefold()
    if not current:
        return False
    offer_markers = (
        "search youtube", "search youtube for", "find you a youtube",
        "youtube for a tutorial", "look for youtube videos",
    )
    # Context is assembled in chronological sections. Only the latest message
    # record can authorize a short confirmation; a stale offer elsewhere in the
    # history must not trigger a search weeks/topics later.
    message_lines = [
        line.casefold() for line in (prior_context or "").splitlines()
        if re.match(r"^\[[^\]]+\]\s+.+\(Discord ID \d+\):", line, re.IGNORECASE)
    ]
    if not message_lines:
        return False
    latest_line = message_lines[-1]
    is_rem = bool(re.search(r"\]\s*Rem\s+\(Discord ID", latest_line, re.IGNORECASE))
    if not is_rem or not any(marker in latest_line for marker in offer_markers):
        return False
    affirmative = current in {"yes", "yeah", "yep", "sure", "please", "yes please", "do it", "go ahead"}
    confirmation_prefix = current.startswith(("yes ", "yeah ", "yep ", "sure ", "the one ", "i want the one", "that one", "use the one"))
    return affirmative or confirmation_prefix


def search_authorized_by_current_turn(current_message: str, pending_confirmation: bool) -> bool:
    """Authorize discovery only from current-turn intent or a recent live offer.

    Avoid searches on a bare mention of YouTube, a negative request, or stale history.
    """
    text = " ".join((current_message or "").casefold().split())
    if pending_confirmation:
        return True
    if not text:
        return False

    negative_patterns = (
        r"\b(?:don't|dont|do not|never|stop)\s+(?:search|look|find|use)?\s*(?:on\s+)?youtube\b",
        r"\bno\s+(?:youtube|videos?\s+from\s+youtube)\b",
        r"\bnot\s+(?:youtube|a youtube video|youtube videos?)\b",
        r"\bi (?:don't|dont|do not) want (?:you to )?(?:search|use|look on) youtube\b",
    )
    if any(re.search(pattern, text) for pattern in negative_patterns):
        return False

    # Strong current-turn video-discovery intent, not a generic reference to YouTube.
    explicit_request = re.search(
        r"\b(?:search|find|look up|look for|get|show|give|recommend|suggest)\b.{0,70}\b(?:youtube|videos?|tutorials?)\b",
        text,
    ) or re.search(
        r"\b(?:youtube|videos?|tutorials?)\b.{0,70}\b(?:search|find|look up|look for|get|show|recommend|suggest)\b",
        text,
    )
    can_you_request = re.search(
        r"\b(?:can|could|would) you\b.{0,80}\b(?:youtube|video|tutorial)\b",
        text,
    )
    request_for_tutorial = (
        re.search(r"\b(?:find|search|recommend|suggest|show|give me)\b", text)
        and re.search(r"\b(?:tutorial|tutorials|video|videos)\b", text)
    )
    return bool(explicit_request or can_you_request or request_for_tutorial)
