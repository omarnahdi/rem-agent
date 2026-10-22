"""Deterministic, schema-validated Modrinth search for explicit Discord requests.

For a clear request to find/recommend Minecraft mods, Rem performs a real catalog
search and formats the final links from validated tool results. The main language
model cannot omit links or invent substitute projects on this path.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from .schemas import ModrinthSearchIntent, ModrinthSearchOutput

_ACTION_RE = re.compile(
    r"\b(?:find|search|look\s+for|look\s+up|recommend|suggest|list|show(?:\s+me)?|"
    r"give\s+me|discover|fetch|best|top|highest[-\s]+rated|most\s+popular)\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(
    r"\b(?:minecraft\s*(?:game\s*)?(?:version\s*)?|mc\s*(?:version\s*)?|version\s*)?"
    r"(?P<version>\d+\.\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_LOADER_RE = re.compile(r"\b(fabric|forge|neoforge|quilted\s+fabric|quilt)\b", re.IGNORECASE)


def parse_modrinth_search_intent(content: str) -> ModrinthSearchIntent | None:
    """Parse clear mod-catalog requests into a Pydantic-validated search intent."""
    raw = " ".join((content or "").split())
    if len(raw) < 4 or not _ACTION_RE.search(raw):
        return None
    lowered = raw.casefold()
    # Honour an explicit CurseForge-only request; do not silently search another site.
    if "curseforge" in lowered and "modrinth" not in lowered:
        return None

    if re.search(r"\b(?:shaders?|shader\s+packs?)\b", lowered):
        project_type = "shader"
    elif re.search(r"\b(?:resource\s+packs?|resourcepacks?)\b", lowered):
        project_type = "resourcepack"
    elif re.search(r"\b(?:data\s+packs?|datapacks?)\b", lowered):
        project_type = "datapack"
    elif re.search(r"\bmodpacks?\b", lowered):
        project_type = "modpack"
    elif re.search(r"\bmods?\b", lowered) or "modrinth" in lowered:
        project_type = "mod"
    else:
        return None

    version_match = _VERSION_RE.search(raw)
    game_version = version_match.group("version") if version_match else None
    loader_match = _LOADER_RE.search(raw)
    loader = loader_match.group(1).casefold().replace(" ", "_") if loader_match else None
    if loader and not game_version:
        # Modrinth loader filtering without a version often excludes useful results;
        # only apply the loader filter when the request specifies a version too.
        loader = None

    query = raw
    # Discord normally exposes mentions as <@id>, but logs/copied message text
    # can contain the rendered display form, e.g. "@Rem(beta)". Strip both forms
    # so UI text never becomes part of the Modrinth query.
    query = re.sub(r"<@!?\d+>", " ", query)
    query = re.sub(
        r"^\s*(?:(?:hey|hi|yo)\s+)?@?rem(?:\s*\([^)]{1,40}\))?(?=$|[\s,:;!?.,—-])[\s,:;!?.,—-]*",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    query = _VERSION_RE.sub(" ", query)
    query = _LOADER_RE.sub(" ", query)
    query = re.sub(
        r"\b(?:find|search|look\s+for|look\s+up|recommend|suggest|list|show|give|discover|fetch|"
        r"rem|me|the|a|an|some|any|best|top|highest[-\s]+rated|most\s+popular|popular|available|"
        r"minecraft|mc|modrinth|mods?|modpacks?|resource\s+packs?|resourcepacks?|data\s+packs?|"
        r"datapacks?|shaders?|shader\s+packs?|version|versions|game|compatible|compatibility|"
        r"supporting|support|to|when|for|on|in|with|using|please|can\s+you|could\s+you|java\s+edition)\b",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"[^\w\s'-]", " ", query, flags=re.UNICODE)
    query = " ".join(query.split()).strip(" -_'\t")
    if len(query) < 2:
        # A category-only request such as "find shaders for 1.21" is still a
        # valid catalog query. Use a broad search term rather than skipping links.
        query = "popular"

    try:
        return ModrinthSearchIntent(
            query=query[:120],
            project_type=project_type,
            game_version=game_version,
            loader=loader,
            max_results=6,
        )
    except ValidationError:
        return None


def _escape_link_label(value: str) -> str:
    """Escape Markdown delimiter characters so titles remain a safe link label."""
    return re.sub(r"([\\\[\]()])", r"\\\1", value.strip())


def format_modrinth_search_reply(
    raw_result: Any,
    intent: ModrinthSearchIntent,
) -> str:
    """Format only Pydantic-validated project records into clickable canonical links."""
    try:
        result = ModrinthSearchOutput.model_validate(raw_result)
    except ValidationError:
        return "I couldn't validate Modrinth's search response, so I won't send unverified project links. Please try again shortly."

    if not result.ok:
        reason = (result.error or "Modrinth search did not complete.").strip()
        return f"I couldn't verify current Modrinth results for **{intent.query}**. {reason}"

    rows = result.results
    if intent.game_version:
        rows = [row for row in rows if intent.game_version in row.game_versions]

    if not rows:
        version_clause = f" for Minecraft **{intent.game_version}**" if intent.game_version else ""
        return (
            f"Modrinth returned no verified {intent.project_type} results for **{intent.query}**{version_clause}. "
            "I won't invent project names or links. Try a nearby version or a more specific search term."
        )

    filters: list[str] = []
    if intent.game_version:
        filters.append(f"Minecraft {intent.game_version}")
    if intent.loader:
        filters.append(intent.loader.replace("_", " ").title())
    filter_text = f" ({', '.join(filters)})" if filters else ""
    lines = [f"Verified Modrinth results for **{intent.query}**{filter_text}:"]
    for project in rows[: intent.max_results]:
        title = _escape_link_label(project.title)
        url = project.url  # canonicalized and host/path-validated by Pydantic
        line = f"• [{title}]({url})"
        summary = " ".join((project.summary or "").split())
        if summary:
            summary = summary[:260].rstrip()
            if len(project.summary) > 260:
                summary += "…"
            line += f" — {summary}"
        lines.append(line)
    lines.append("Links are taken directly from Modrinth's verified search results.")
    return "\n".join(lines)
