"""Pure text and project-link helpers, importable without Discord/Agno installed."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

DISCORD_MESSAGE_LIMIT = 1900
_SUPPORTED_PROJECT_HOSTS = {"modrinth.com", "www.modrinth.com", "curseforge.com", "www.curseforge.com"}


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
    """Identify common raw function/tool-call JSON accidentally exposed as chat."""
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
    if isinstance(value.get("name"), str) and isinstance(value.get("arguments"), (dict, list)):
        return True
    return isinstance(value.get("function_call"), dict) or isinstance(value.get("tool_calls"), list)


def _clean_url_candidate(url: str) -> str:
    """Remove Markdown residue/punctuation without changing a returned project path."""
    value = (url or "").strip().strip("<>")
    value = re.sub(r"(?:\\?[*_~`]+|[)\]}.,;!?]+)+$", "", value)
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold()
    if host not in _SUPPORTED_PROJECT_HOSTS or parts.scheme not in {"http", "https"}:
        return ""
    path = re.sub(r"\\+$", "", parts.path).rstrip("/ ")
    if not path:
        return ""
    # Strip query/fragment. Search tools return stable project pages, not tracking URLs.
    return urlunsplit(("https", host, path, "", ""))


_PROJECT_MARKDOWN_LINK_RE = re.compile(
    r"\[(?P<label>(?:[^\]\n]|\](?!\())*?)\]"
    r"\((?P<href>https?://[^\s)]*)\)"
    r"(?P<tail>[^\s]*)",
    re.IGNORECASE,
)
_PROJECT_URL_IN_TEXT_RE = re.compile(
    r"(?P<url>https?://(?:www\.)?(?:modrinth\.com|curseforge\.com)/[^\s<>()\[\]\\*_~`]+)"
    # Capture common stray Markdown residues after a bare URL so they can be
    # removed instead of becoming part of Discord's clickable URL target.
    r"(?P<tail>(?:\)\*{1,2}|[*_~`]+)*)",
    re.IGNORECASE,
)
def normalize_project_markdown_links(content: str, verified_links: dict[str, str] | None = None) -> str:
    """Repair Markdown residue in links already written by the Agent.

    ``verified_links`` is accepted for backward compatibility but deliberately not
    used to create, append, relabel, redirect, or remove project recommendations.
    Search results must be selected by the registered tool and described by the Agent.
    This helper only removes formatting residue such as a trailing ``)**`` from an
    existing Modrinth/CurseForge link so Discord does not include it in the URL.
    """
    del verified_links  # Explicitly prevent catalog-driven response synthesis here.
    if not isinstance(content, str) or not content:
        return content or ""

    def clean_tail(tail: str, prefix: str) -> str:
        if not tail:
            return ""
        # Keep genuine emphasis only when its opening marker is immediately before
        # this URL/link. Otherwise drop orphaned Markdown suffixes.
        for marker in ("**", "__", "~~", "*", "_", "`"):
            if tail == marker and prefix.endswith(marker):
                return tail
        if tail.startswith(")**"):
            return tail[3:]
        return re.sub(r"^(?:(?:\\?[*_~`]+)|[)\]]+)+", "", tail)

    def clean_url_label(label: str) -> str:
        """Remove escaped Markdown residue from a URL used as a link label.

        Some model outputs duplicate the destination into the label and emit
        ``https://modrinth.com/mod/foo)\\*\\*`` before the closing bracket.
        Only normalize that very specific residue on URL-shaped labels; keep
        human-authored labels and intentional emphasis untouched.
        """
        pattern = re.compile(
            r"^(?P<url>https?://(?:www\.)?(?:modrinth\.com|curseforge\.com)/[^\s()\[\]]*)"
            r"\)(?:(?:\\?[*_~`]){1,})$",
            re.IGNORECASE,
        )
        match = pattern.match(label)
        return match.group("url") if match else label

    def repair_link(match: re.Match[str]) -> str:
        original_label = match.group("label")
        label = clean_url_label(original_label)
        href = match.group("href")
        tail = match.group("tail") or ""
        prefix = content[:match.start()]
        # Markdown residue can leak into the destination itself as escaped stars.
        href_without_residue = re.sub(r"(?:\\?[*_~`]+)+$", "", href)
        cleaned_href = _clean_url_candidate(href_without_residue)
        if not cleaned_href:
            return match.group(0)
        host = (urlsplit(cleaned_href).hostname or "").casefold()
        if not (host.endswith("modrinth.com") or host.endswith("curseforge.com")):
            return match.group(0)
        cleaned_tail = clean_tail(tail, prefix)
        if label == original_label and href == cleaned_href and cleaned_tail == tail:
            return match.group(0)
        # This only removes syntactic residue. It does not select a result,
        # invent a link, append search results, or redirect the Agent's href.
        return f"[{label}]({cleaned_href}){cleaned_tail}"

    normalized = _PROJECT_MARKDOWN_LINK_RE.sub(repair_link, content)

    def clean_plain_url(match: re.Match[str]) -> str:
        # Don't process the URL inside a Markdown destination a second time.
        prefix = normalized[:match.start()]
        if prefix.rfind("](") == prefix.rfind("]") and prefix.rfind("](") >= 0:
            return match.group(0)
        raw = match.group("url")
        tail = match.group("tail") or ""
        href_without_residue = re.sub(r"(?:\\?[*_~`]+)+$", "", raw)
        cleaned_url = _clean_url_candidate(href_without_residue)
        if not cleaned_url:
            return match.group(0)
        host = (urlsplit(cleaned_url).hostname or "").casefold()
        if not (host.endswith("modrinth.com") or host.endswith("curseforge.com")):
            return match.group(0)
        cleaned_tail = clean_tail(tail, prefix)
        return cleaned_url + cleaned_tail

    return _PROJECT_URL_IN_TEXT_RE.sub(clean_plain_url, normalized)


def normalize_modrinth_markdown_links(content: str) -> str:
    """Backward-compatible helper for legacy callers/tests."""
    return normalize_project_markdown_links(content)
