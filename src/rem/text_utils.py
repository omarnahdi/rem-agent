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


_MODRINTH_MARKDOWN_LINK_RE = re.compile(
    r"\[(?P<label>[^\n]*?)\]"
    r"\((?P<href>https?://(?:www\.)?modrinth\.com/mod/[^\s)]*)\)"
    r"(?P<tail>[^\s]*)",
    re.IGNORECASE,
)
_MODRINTH_PROJECT_PATH_RE = re.compile(
    r"https?://(?:www\.)?modrinth\.com/mod/([a-z0-9_-]+)",
    re.IGNORECASE,
)


def normalize_modrinth_markdown_links(content: str) -> str:
    """Repair malformed Markdown links to Modrinth project pages.

    LLMs occasionally leak closing parentheses or formatting markers into a
    project URL, for example ``[https://modrinth.com/mod/foo)\\*\\*]`` or
    ``](https://modrinth.com/mod/foo\\)**)``. Discord then embeds a URL that
    does not exist. This guard rebuilds only links whose project URL or URL
    label is malformed; unrelated Markdown and ordinary links are unchanged.
    """
    if not isinstance(content, str) or not content:
        return content or ""

    def repair(match: re.Match[str]) -> str:
        label = match.group("label")
        href = match.group("href")
        tail = match.group("tail")
        project_match = _MODRINTH_PROJECT_PATH_RE.search(href)
        if not project_match:
            return match.group(0)

        canonical_url = f"https://modrinth.com/mod/{project_match.group(1)}"
        label_url_match = _MODRINTH_PROJECT_PATH_RE.search(label)
        label_is_url = label_url_match is not None
        label_is_canonical_url = label.strip() == canonical_url
        href_is_canonical = href.rstrip("/") == canonical_url

        # A suffix immediately following a Markdown link can be legitimate
        # prose punctuation. We only repair the link when its href or URL label
        # is already malformed, then drop the formatting residue attached to it.
        malformed = (
            not href_is_canonical
            or (label_is_url and not label_is_canonical_url)
        )
        if not malformed:
            return match.group(0)

        clean_label = canonical_url if label_is_url else label
        # Remove only common stray closing Markdown markers (e.g. **)) while
        # retaining ordinary punctuation such as a following period or comma.
        clean_tail = re.sub(
            r"^(?:(?:\\?[*_~`]+)|[)\]]+)+",
            "",
            tail,
        )
        return f"[{clean_label}]({canonical_url}){clean_tail}"

    return _MODRINTH_MARKDOWN_LINK_RE.sub(repair, content)
