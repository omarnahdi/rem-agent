"""Search CurseForge's official API for Minecraft mod recommendations."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from agno.run import RunContext
from agno.tools import Toolkit

logger = logging.getLogger(__name__)

CURSEFORGE_API_BASE = "https://api.curseforge.com/v1"
MINECRAFT_GAME_ID = 432
MINECRAFT_MOD_CLASS_ID = 6
LOADER_TYPES = {
    "forge": 1,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}
LOADER_NAMES = {value: key for key, value in LOADER_TYPES.items()}


class CurseForgeSearchTools(Toolkit):
    """Expose authenticated CurseForge mod search to Agno."""

    def __init__(self, *, api_key: str | None, timeout: float = 12.0) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        super().__init__(
            name="curseforge_search",
            tools=[],
            async_tools=[(self.search_curseforge_projects, "search_curseforge_projects")],
            instructions=(
                "Use this tool to search Minecraft mods on CurseForge. For broad mod recommendations, "
                "compare these results with Modrinth when that tool is available. Respect the exact "
                "Minecraft version and loader supplied by the user. Only recommend projects returned "
                "by search results; this tool discovers projects and does not download or install them. "
                "A CurseForge API key is required; if not configured, explain that and use Modrinth instead."
            ),
        )

    async def search_curseforge_projects(
        self,
        query: str,
        game_version: str = "",
        loader: str = "",
        max_results: int = 8,
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Search CurseForge Minecraft mods.

        Args:
            query: What the user wants the mod to do or the mod name.
            game_version: Optional Minecraft version, such as 1.21.1.
            loader: Optional loader: Forge, Fabric, Quilt or NeoForge. When set,
                game_version is also required by the CurseForge API.
            max_results: Number of projects to return (1-10).
        """
        query = " ".join((query or "").split())
        if len(query) < 2:
            return {"ok": False, "error": "Provide a mod name or feature to search for.", "results": []}
        if not self.api_key:
            return {
                "ok": False,
                "error": (
                    "CurseForge search is not configured because CURSEFORGE_API_KEY is missing. "
                    "Use Modrinth results for now. CurseForge's official API requires an API key."
                ),
                "results": [],
            }

        limit = max(1, min(int(max_results), 10))
        params: dict[str, Any] = {
            "gameId": MINECRAFT_GAME_ID,
            "classId": MINECRAFT_MOD_CLASS_ID,
            "searchFilter": query,
            "pageSize": limit,
            "index": 0,
        }
        version = (game_version or "").strip()
        if version:
            params["gameVersion"] = version

        normalized_loader = (loader or "").strip().casefold().replace(" ", "")
        if normalized_loader:
            if normalized_loader not in LOADER_TYPES:
                return {
                    "ok": False,
                    "error": "Unsupported CurseForge loader. Use Forge, Fabric, Quilt or NeoForge.",
                    "results": [],
                }
            if not version:
                return {
                    "ok": False,
                    "error": "Provide game_version when filtering CurseForge results by loader.",
                    "results": [],
                }
            params["modLoaderType"] = LOADER_TYPES[normalized_loader]

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Accept": "application/json",
                    "x-api-key": self.api_key,
                    "User-Agent": "RemAgnoDiscordBot/0.1.0 (Minecraft mod discovery)",
                },
                follow_redirects=True,
            ) as client:
                response = await client.get(f"{CURSEFORGE_API_BASE}/mods/search", params=params)
                response.raise_for_status()
                payload = response.json()

            mods = payload.get("data", []) if isinstance(payload, dict) else []
            results: list[dict[str, Any]] = []
            for mod in mods[:limit] if isinstance(mods, list) else []:
                if not isinstance(mod, dict):
                    continue
                slug = str(mod.get("slug") or "").strip()
                name = str(mod.get("name") or slug or "Untitled CurseForge project").strip()
                links = mod.get("links") if isinstance(mod.get("links"), dict) else {}
                website_url = str(links.get("websiteUrl") or "").strip()
                if not website_url and slug:
                    website_url = f"https://www.curseforge.com/minecraft/mc-mods/{slug}"
                if not website_url:
                    continue

                categories = mod.get("categories") if isinstance(mod.get("categories"), list) else []
                authors = mod.get("authors") if isinstance(mod.get("authors"), list) else []
                latest_indexes = mod.get("latestFilesIndexes")
                if not isinstance(latest_indexes, list):
                    latest_indexes = []
                versions = sorted({
                    str(item.get("gameVersion"))
                    for item in latest_indexes
                    if isinstance(item, dict) and item.get("gameVersion")
                })
                loaders = sorted({
                    LOADER_NAMES.get(int(item.get("modLoader", -1)), f"unknown:{item.get('modLoader')}")
                    for item in latest_indexes
                    if isinstance(item, dict) and item.get("modLoader") is not None
                    and int(item.get("modLoader", -1)) in LOADER_NAMES
                })
                logo = mod.get("logo") if isinstance(mod.get("logo"), dict) else {}
                results.append({
                    "project_id": mod.get("id"),
                    "slug": slug,
                    "title": name,
                    "summary": mod.get("summary") or "",
                    "url": website_url,
                    "downloads": mod.get("downloadCount"),
                    "rating": mod.get("rating"),
                    "updated_at": mod.get("dateModified"),
                    "categories": [
                        str(category.get("name")) for category in categories
                        if isinstance(category, dict) and category.get("name")
                    ],
                    "authors": [
                        str(author.get("name")) for author in authors
                        if isinstance(author, dict) and author.get("name")
                    ],
                    "game_versions": versions[:20],
                    "loaders": loaders,
                    "logo_url": logo.get("url") if logo else None,
                })

            return {
                "ok": True,
                "query": query,
                "results": results,
                "source": "CurseForge official API",
            }
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                message = "CurseForge rejected the API key (HTTP %s). Verify the key and that API access was approved." % status
            else:
                message = f"CurseForge API returned HTTP {status}."
            logger.warning("CurseForge search failed with HTTP status %s", status)
            return {"ok": False, "error": message, "results": []}
        except Exception as exc:
            logger.exception("CurseForge search failed")
            return {"ok": False, "error": f"CurseForge search failed: {type(exc).__name__}.", "results": []}
