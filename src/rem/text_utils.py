"""Pure text helpers, intentionally importable without Discord/Agno installed."""

from __future__ import annotations

import re

DISCORD_MESSAGE_LIMIT = 1900


def split_discord_message(content: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split long output below Discord's 2000-character limit."""
    if limit < 1:
        raise ValueError("limit must be positive")
    text = (content or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
        chunk = text[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        text = text[split_at:].strip()
    if text:
        chunks.append(text)
    return chunks


def strip_bot_mention(content: str, bot_id: int) -> str:
    """Remove only Rem's mention, keeping mentions of other users intact."""
    cleaned = re.sub(rf"<@!?{int(bot_id)}>", "", content or "")
    return cleaned.strip()


def looks_like_raw_tool_call_payload(content: str) -> bool:
    """Identify common raw function/tool-call JSON accidentally exposed as chat.

    This is a last-resort output guard, not a replacement for correctly registering
    tools with Agno and allowing the framework to execute them.
    """
    import json

    text = (content or "").strip()
    if not text or len(text) > 20_000:
        return False
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(value, dict):
        return False
    # Agno/tool-call JSON shape seen in the old bot's screenshot.
    if isinstance(value.get("name"), str) and isinstance(value.get("arguments"), (dict, list)):
        return True
    if isinstance(value.get("function_call"), dict) or isinstance(value.get("tool_calls"), list):
        return True
    return False
