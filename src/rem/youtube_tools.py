"""Real YouTube discovery through public Piped APIs; never inspect video contents."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from agno.run import RunContext
from agno.tools import Toolkit

from .context import search_authorized_by_current_turn
from .youtube_utils import extract_piped_items, normalize_piped_results

logger = logging.getLogger(__name__)

PIPED_INSTANCES = (
    "https://pipedapi.ducks.party",
    "https://api.piped.private.coffee",
)
MIN_RESULTS = 6
MAX_RESULTS = 10


class YouTubeSearchTools(Toolkit):
    def __init__(self, *, timeout: float = 12.0) -> None:
        self.timeout = timeout
        super().__init__(
            name="youtube_search",
            tools=[],
            async_tools=[(self.search_youtube, "search_youtube")],
            instructions=(
                "Search YouTube only for video discovery. This tool does not watch, download, "
                "transcribe, or verify video contents. Recommend only titles/URLs/metadata actually "
                "returned by this tool. If it reports an outage, say search is temporarily unavailable."
            ),
        )

    async def search_youtube(
        self,
        query: str,
        max_results: int = 8,
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Search YouTube videos and return real result metadata and URLs.

        Args:
            query: A focused YouTube search query, including Minecraft version/edition/farm constraints when known.
            max_results: Number of videos to return (6-10, matching the legacy tool's limits).
        """
        deps = getattr(run_context, "dependencies", None) or {}
        current_message = str(deps.get("trusted_current_message", ""))
        pending = bool(deps.get("trusted_youtube_confirmation_pending", False))
        if not search_authorized_by_current_turn(current_message, pending):
            return {
                "ok": False,
                "error": "The current user turn did not request or confirm YouTube discovery. Do not search based only on historical context.",
                "results": [],
            }

        query = " ".join((query or "").split())
        if len(query) < 3:
            return {"ok": False, "error": "Please provide a more specific YouTube query.", "results": []}
        try:
            limit = max(MIN_RESULTS, min(int(max_results), MAX_RESULTS))
        except (ValueError, TypeError):
            limit = 8
        errors: list[str] = []

        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": "Rem-Piped-Search/1.0", "Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            for base_url in PIPED_INSTANCES:
                try:
                    response = await client.get(
                        f"{base_url.rstrip('/')}/search",
                        params={"q": query, "filter": "videos"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    # Preserve the response shape used by the verified legacy tool:
                    # Piped normally returns an object with an `items` list, not a bare list.
                    items = extract_piped_items(payload)
                    results = normalize_piped_results(items, limit)
                    if results:
                        logger.info("YouTube search succeeded | endpoint=%s | results=%s", base_url, len(results))
                        return {
                            "ok": True,
                            "query": query,
                            "source": base_url,
                            "count": len(results),
                            "results": results,
                            "note": "Search metadata only; video contents were not inspected.",
                        }
                    errors.append(f"{base_url}: returned no usable video results")
                except Exception as exc:
                    detail = f"{base_url}: {type(exc).__name__}: {exc}"
                    errors.append(detail)
                    logger.warning("YouTube search endpoint failed: %s", detail)

        logger.error("All YouTube search endpoints failed | query=%r | errors=%s", query, errors)
        return {
            "ok": False,
            "query": query,
            "error": "YouTube search is temporarily unavailable; all configured public search instances failed.",
            "diagnostics": errors,
            "results": [],
        }

