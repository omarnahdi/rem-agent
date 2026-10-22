"""Pure text and project-link helpers, importable without Discord/Agno installed."""
from __future__ import annotations

import ast
import json
import re
from urllib.parse import urlsplit, urlunsplit
from typing import Any

DISCORD_MESSAGE_LIMIT = 1900
_SUPPORTED_PROJECT_HOSTS = {"modrinth.com", "www.modrinth.com", "curseforge.com", "www.curseforge.com"}
_SUPPORTED_MODRINTH_TYPES = {"mod", "modpack", "resourcepack", "datapack", "shader"}


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


def _project_slug(url: str) -> str:
    cleaned = _clean_url_candidate(url)
    if not cleaned:
        return ""
    parts = urlsplit(cleaned)
    path_parts = [part for part in parts.path.split("/") if part]
    if len(path_parts) < 2:
        return ""
    host = (parts.hostname or "").casefold()
    if host.endswith("modrinth.com"):
        if path_parts[0].casefold() not in _SUPPORTED_MODRINTH_TYPES:
            return ""
        return path_parts[1]
    if host.endswith("curseforge.com") and len(path_parts) >= 3 and path_parts[0].casefold() == "minecraft":
        return path_parts[-1]
    return ""


def _link_key(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[`*_~]", "", text)
    text = re.sub(r"\s+", " ", text)
    # Preserve balanced parentheses within real project titles, e.g.
    # "The Mimicer (horror)"; only remove common outer Markdown brackets.
    text = text.strip()
    text = text.strip("[]{}.,;:!?")
    return text.casefold()


def _iter_mapping_nodes(value: Any):
    """Yield dictionaries nested inside tool results, decoding JSON strings when possible."""
    if isinstance(value, str):
        # Agno/tool traces may retain a dict as Python repr (single quotes,
        # True/False/None) instead of JSON. literal_eval parses data literals
        # safely; never use eval for model/tool content.
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            try:
                decoded = ast.literal_eval(value)
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                return
        if isinstance(decoded, (dict, list, tuple)):
            yield from _iter_mapping_nodes(decoded)
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list, tuple, str)):
                yield from _iter_mapping_nodes(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_mapping_nodes(child)


def _read_value(value: Any, *names: str) -> Any:
    """Read a field from either an Agno/Pydantic object or a mapping."""
    for name in names:
        if isinstance(value, dict):
            candidate = value.get(name)
        else:
            candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def _tool_source_name(value: Any) -> str:
    """Extract the registered function name from Agno tool/event/message shapes."""
    direct = _read_value(value, "tool_name", "name", "function_name")
    nested = _read_value(value, "tool", "function", "tool_call")
    if nested is not None:
        direct = direct or _read_value(nested, "tool_name", "name", "function_name")
        function = _read_value(nested, "function")
        if function is not None:
            direct = direct or _read_value(function, "name")
    return str(direct or "").casefold()


def collect_verified_project_links(run_output: Any) -> dict[str, str]:
    """Build label/slug -> canonical URL mappings from actual search-tool results.

    Agno versions differ in where completed tool results are exposed: some store
    them in ``RunOutput.tools``, others expose them through tool-completed events
    or role=tool messages. Collect all three. Never trust the model's own prose
    as evidence that a project URL exists.
    """
    aliases: dict[str, str] = {}
    tool_entries: list[tuple[str, Any]] = []

    def add_entry(name: Any, payload: Any) -> None:
        lowered = str(name or "").casefold()
        if payload is None:
            return
        if "modrinth" not in lowered and "curseforge" not in lowered:
            # If an Agno version omits the function name from a tool-result
            # message, identify the source from the registered tool's structured
            # result metadata. Do not infer from the assistant's own prose.
            for node in _iter_mapping_nodes(payload):
                source = str(node.get("source") or "").casefold()
                if "modrinth" in source or "curseforge" in source:
                    lowered = source
                    break
        if "modrinth" not in lowered and "curseforge" not in lowered:
            return
        tool_entries.append((lowered, payload))

    # Agno's aggregated tool execution records.
    for tool in (getattr(run_output, "tools", None) or []):
        lowered = _tool_source_name(tool)
        for field in ("result", "content", "output", "tool_result", "result_content"):
            payload = _read_value(tool, field)
            if payload is not None:
                add_entry(lowered, payload)

    # Agno's streaming/completed event representations.
    for event in (getattr(run_output, "events", None) or []):
        event_name = str(_read_value(event, "event", "event_type") or "").casefold()
        tool = _read_value(event, "tool")
        lowered = _tool_source_name(tool) or _tool_source_name(event)
        if "toolcallcompleted" not in event_name and "tool_call_completed" not in event_name:
            continue
        payload = _read_value(event, "content", "result", "output")
        if payload is None and tool is not None:
            payload = _read_value(tool, "result", "content", "output")
        add_entry(lowered, payload)

    # Important: some versions only retain the tool call and its result as
    # separate messages, leaving RunOutput.tools empty. Tool messages are a
    # trusted execution channel; the user's/assistant's ordinary prose is not.
    for message in (getattr(run_output, "messages", None) or []):
        role = str(_read_value(message, "role") or "").casefold()
        if role not in {"tool", "function", "tool_result"}:
            continue
        lowered = _tool_source_name(message)
        content = _read_value(message, "content", "result", "output")
        add_entry(lowered, content)

    for tool_name, payload in tool_entries:
        for node in _iter_mapping_nodes(payload):
            status = node.get("ok")
            if status is False or str(status).casefold() == "false":
                continue
            raw_url = node.get("url") or node.get("website_url") or node.get("websiteUrl")
            if not isinstance(raw_url, str):
                continue
            url = _clean_url_candidate(raw_url)
            if not url:
                continue
            host = (urlsplit(url).hostname or "").casefold()
            if "modrinth" in tool_name and not host.endswith("modrinth.com"):
                continue
            if "curseforge" in tool_name and not host.endswith("curseforge.com"):
                continue
            title = node.get("title") or node.get("name") or node.get("project_name")
            slug = node.get("slug") or _project_slug(url)
            for alias in (title, slug, str(slug).replace("-", " ") if slug else None, raw_url, url):
                key = _link_key(alias)
                if key:
                    aliases[key] = url
    return aliases


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
_MODRINTH_PATH_RE = re.compile(
    r"https?://(?:www\.)?modrinth\.com/(?P<kind>mod|modpack|resourcepack|datapack|shader)/(?P<slug>[a-z0-9_-]+)",
    re.IGNORECASE,
)


_EXISTING_LINK_OR_URL_RE = re.compile(
    r"\[[^\]\n]+\]\(https?://[^)\s]+\)|https?://[^\s<>]+",
    re.IGNORECASE,
)


def _display_label_for_url(catalog: dict[str, str], url: str) -> str:
    """Choose a readable title alias for a verified canonical URL."""
    aliases = [
        key for key, candidate in catalog.items()
        if candidate == url and not key.startswith("http")
        and "/" not in key and len(key) >= 4
    ]
    if aliases:
        # Multi-word names are more likely to be the API title than a slug.
        label = max(aliases, key=lambda item: (" " in item, len(item)))
        return label.title()
    slug = _project_slug(url)
    return slug.replace("-", " ").replace("_", " ").title() or url


def _link_verified_names(content: str, catalog: dict[str, str]) -> str:
    """Turn known result titles mentioned as plain text into clickable links.

    Existing Markdown links and bare URLs are temporarily protected so labels
    or URLs are never nested or rewritten. Only aliases from real tool results
    are eligible for auto-linking.
    """
    aliases = [
        (alias, url) for alias, url in catalog.items()
        if len(alias) >= 4 and "/" not in alias and not alias.startswith("http")
        and _clean_url_candidate(url)
    ]
    aliases.sort(key=lambda item: len(item[0]), reverse=True)
    if not aliases:
        return content

    protected: list[str] = []
    def stash(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00REM_LINK_{len(protected)-1}\x00"

    working = _EXISTING_LINK_OR_URL_RE.sub(stash, content)
    for alias, url in aliases:
        canonical = _clean_url_candidate(url)
        if not canonical:
            continue
        pattern = re.compile(rf"(?<![\w/])({re.escape(alias)})(?![\w])", re.IGNORECASE)
        working = pattern.sub(lambda match, target=canonical: f"[{match.group(1)}]({target})", working)
    for index, original in enumerate(protected):
        working = working.replace(f"\x00REM_LINK_{index}\x00", original)
    return working


def _append_missing_verified_links(content: str, catalog: dict[str, str]) -> str:
    """Append actual tool results omitted by the model, without inventing projects."""
    unique_urls: list[str] = []
    for url in catalog.values():
        canonical = _clean_url_candidate(url)
        if canonical and canonical not in unique_urls:
            unique_urls.append(canonical)
    if not unique_urls:
        return content

    present_urls = {
        canonical for raw in re.findall(r"https?://[^\s)<>]+", content, flags=re.IGNORECASE)
        if (canonical := _clean_url_candidate(raw))
    }
    missing = [url for url in unique_urls if url not in present_urls]
    if not missing:
        return content

    lines = ["Verified project links returned by the search tool:"]
    for url in missing:
        lines.append(f"• [{_display_label_for_url(catalog, url)}]({url})")
    return f"{content.rstrip()}\n\n" + "\n".join(lines)


def normalize_project_markdown_links(content: str, verified_links: dict[str, str] | None = None) -> str:
    """Repair malformed project links and prefer exact URLs returned by search tools.

    If successful Modrinth/CurseForge search results are present, project links that
    cannot be matched to those results are rendered as plain labels instead of sending
    users to an unverified or unrelated destination. Other hyperlinks are untouched.
    """
    if not isinstance(content, str) or not content:
        return content or ""
    # Only synthesize an extra verified-results section when the model omitted
    # project URLs completely. If it tried to format links, repair those links
    # in place and avoid adding a duplicate catalog beneath them.
    had_project_link_attempt = bool(re.search(
        r"(?:\]\(https?://(?:www\.)?(?:modrinth\.com|curseforge\.com)/|"
        r"https?://(?:www\.)?(?:modrinth\.com|curseforge\.com)/)",
        content,
        flags=re.IGNORECASE,
    ))
    catalog = {key: value for key, value in (verified_links or {}).items() if _clean_url_candidate(value)}
    # A provided catalog means the caller has the current run's tool results, so
    # enforce that links resolve to those results. None means a legacy caller with
    # no catalog: repair formatting only and preserve valid URLs for compatibility.
    strict = bool(catalog)

    def resolve(label: str, href: str) -> str | None:
        for value in (label, href, _project_slug(href), _project_slug(label)):
            key = _link_key(value)
            if key and key in catalog:
                return catalog[key]
        # Without search evidence, repair Markdown contamination while retaining the
        # slug the model supplied; this fixes syntax, but does not claim validation.
        if not strict:
            clean_href = _clean_url_candidate(href)
            if clean_href:
                if (urlsplit(clean_href).hostname or "").casefold().endswith("modrinth.com"):
                    match = _MODRINTH_PATH_RE.match(clean_href)
                    if match:
                        return f"https://modrinth.com/{match.group('kind').casefold()}/{match.group('slug')}"
                return clean_href
        return None

    def repair(match: re.Match[str]) -> str:
        label = match.group("label").strip()
        href = match.group("href").strip()
        tail = match.group("tail") or ""
        host = ""
        try:
            host = (urlsplit(_clean_url_candidate(href)).hostname or "").casefold()
        except ValueError:
            pass
        is_supported = host.endswith("modrinth.com") or host.endswith("curseforge.com")
        if not is_supported and not any(hostname in href.casefold() for hostname in ("modrinth.com", "curseforge.com")):
            return match.group(0)

        resolved = resolve(label, href)
        label_as_url = bool(_project_slug(label))
        prefix = content[:match.start()]
        cleaned_tail = re.sub(r"^(?:(?:\\?[*_~`]+)|[)\]]+)+", "", tail)
        # Preserve balanced emphasis that genuinely wraps the link, e.g.
        # **[Sodium](https://modrinth.com/mod/sodium)**. Do not preserve an
        # unmatched trailing ** after a complete sentence/link: that is the
        # malformed suffix seen in Discord's "Leaving Discord" URL preview.
        if tail and any(tail == marker and prefix.endswith(marker) for marker in ("**", "__", "~~", "*", "_", "`")):
            cleaned_tail = tail
        if resolved is None:
            # A search succeeded but the model returned an unknown project link.
            # Keep the human-readable name and discard the bad target.
            return (label if not label_as_url else "unverified project link") + cleaned_tail
        if label_as_url:
            label = resolved
        # Return the original only when both the canonical target and Markdown
        # suffix are already clean. In particular, don't bypass suffix repair
        # just because the URL itself matches the search result.
        if resolved == _clean_url_candidate(href) and href.rstrip("/") == resolved and cleaned_tail == tail:
            return match.group(0)
        return f"[{label}]({resolved}){cleaned_tail}"

    normalized = _PROJECT_MARKDOWN_LINK_RE.sub(repair, content)

    # If the model emitted a plain malformed supported-platform URL, clean only the
    # markdown residue. Avoid changing links to unrelated platforms.
    def clean_plain(match: re.Match[str]) -> str:
        # Markdown destinations have already been handled above. Do not process
        # their inner URL a second time: its closing `)` belongs to the Markdown
        # link and must not be mistaken for stray punctuation.
        prefix = normalized[:match.start()]
        if prefix.rfind("](") == prefix.rfind("]") and prefix.rfind("](") >= 0:
            return match.group(0)
        raw = match.group("url")
        tail = match.group("tail") or ""
        cleaned = _clean_url_candidate(raw)
        if not cleaned:
            return match.group(0)
        verified = catalog.get(_link_key(cleaned)) or catalog.get(_link_key(_project_slug(cleaned)))
        # A plain URL can be emphasized intentionally. Keep the closing emphasis
        # only when its opener is immediately before the URL; strip orphaned
        # Markdown suffixes such as `)**` from the actual destination.
        clean_tail = tail
        if tail.startswith(")**"):
            clean_tail = tail[3:]
        elif tail and any(tail == marker and prefix.endswith(marker) for marker in ("**", "__", "~~", "*", "_", "`")):
            clean_tail = tail
        else:
            clean_tail = re.sub(r"^(?:[*_~`]+)+", "", tail)
        if not verified:
            if not strict:
                # Legacy/format-only mode: keep a valid URL, but strip malformed
                # suffixes so Discord does not include Markdown residue in its URL.
                return cleaned + clean_tail
            host = (urlsplit(cleaned).hostname or "").casefold()
            platform = "Modrinth" if host.endswith("modrinth.com") else "CurseForge"
            return f"[unverified {platform} project link]{clean_tail}"
        return verified + clean_tail

    normalized = _PROJECT_URL_IN_TEXT_RE.sub(clean_plain, normalized)
    if catalog and not had_project_link_attempt:
        normalized = _link_verified_names(normalized, catalog)
        normalized = _append_missing_verified_links(normalized, catalog)
    return normalized


def normalize_modrinth_markdown_links(content: str) -> str:
    """Backward-compatible helper for legacy callers/tests."""
    return normalize_project_markdown_links(content)
