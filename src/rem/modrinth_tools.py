"""Search current Modrinth projects for Minecraft mod recommendations."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from agno.run import RunContext
from agno.tools import Toolkit

logger = logging.getLogger(__name__)


class ModrinthSearchTools(Toolkit):
    def __init__(self, *, timeout: float = 12.0) -> None:
        self.timeout = timeout
        super().__init__(
            name="modrinth_search",
            tools=[],
            async_tools=[(self.search_modrinth_projects, "search_modrinth_projects")],
            instructions=(
                "Use this tool when the user asks to find or recommend Minecraft mods on Modrinth. "
                "Respect Minecraft edition/version and mod-loader constraints. Only present projects returned by the tool."
            ),
        )

    async def search_modrinth_projects(
        self,
        query: str,
        game_version: str = "",
        loader: str = "",
        max_results: int = 8,
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Search Modrinth's public API for Minecraft mods.

        Args:
            query: What the user wants the mod to do or the name of the mod.
            game_version: Optional exact Minecraft version, such as 1.21.1 or 26.1.
            loader: Optional mod loader, e.g. fabric, forge, neoforge, quilt or quilted_fabric.
            max_results: Number of projects (1-10) to return.
        """
        query = " ".join((query or "").split())
        if len(query) < 2:
            return {"ok": False, "error": "Provide a mod name or feature to search for.", "results": []}
        limit = max(1, min(int(max_results), 10))
        facets: list[list[str]] = [["project_type:mod"]]
        if game_version.strip():
            facets.append([f"versions:{game_version.strip()}"])
        normalized_loader = loader.strip().casefold()
        allowed_loaders = {"fabric", "forge", "neoforge", "quilt", "quilted_fabric"}
        if normalized_loader:
            if normalized_loader not in allowed_loaders:
                return {"ok": False, "error": "Unsupported loader filter. Use Fabric, Forge, NeoForge or Quilt.", "results": []}
            facets.append([f"categories:{normalized_loader}"])

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": "RemAgnoDiscordBot/0.1.0 (Minecraft mod discovery)"},
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    "https://api.modrinth.com/v2/search",
                    params={"query": query, "limit": limit, "facets": json.dumps(facets)},
                )
                response.raise_for_status()
                payload = response.json()
            projects = payload.get("hits", []) if isinstance(payload, dict) else []
            results = []
            for hit in projects[:limit]:
                if not isinstance(hit, dict):
                    continue
                slug = str(hit.get("slug") or "").strip()
                project_type = str(hit.get("project_type") or "mod")
                if not slug:
                    continue
                results.append({
                    "title": hit.get("title") or slug,
                    "summary": hit.get("description") or "",
                    "project_type": project_type,
                    "url": f"https://modrinth.com/{project_type}/{slug}",
                    "downloads": hit.get("downloads"),
                    "followers": hit.get("follows"),
                    "latest_indexed_update": hit.get("date_modified"),
                    "client_side": hit.get("client_side"),
                    "server_side": hit.get("server_side"),
                    "categories": hit.get("categories", []),
                    "game_versions": hit.get("versions", []),
                })
            return {"ok": True, "query": query, "results": results, "source": "Modrinth public API"}
        except Exception as exc:
            logger.exception("Modrinth search failed")
            return {"ok": False, "error": f"Modrinth search failed: {type(exc).__name__}: {exc}", "results": []}
