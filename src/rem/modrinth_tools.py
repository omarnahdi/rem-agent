"""Search Modrinth for Minecraft mods and supported content projects."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from agno.run import RunContext
from agno.tools import Toolkit

logger = logging.getLogger(__name__)

_MODRINTH_SEARCH_URL = "https://api.modrinth.com/v2/search"
_MODRINTH_USER_AGENT = "RemAgnoDiscordBot/0.2.0 (Minecraft project discovery)"

# Modrinth's `project_type` facet supports these four primary project types.
# Data packs are exposed by the API through `all_project_types:datapack`.
_SUPPORTED_TYPES = {"mod", "resourcepack", "datapack", "shader", "modpack"}
_PRIMARY_TYPES = {"mod", "resourcepack", "shader", "modpack"}
_ALLOWED_LOADERS = {"fabric", "forge", "neoforge", "quilt", "quilted_fabric"}
_TYPE_ALIASES = {
    "mod": "mod",
    "mods": "mod",
    "resourcepack": "resourcepack",
    "resourcepacks": "resourcepack",
    "datapack": "datapack",
    "datapacks": "datapack",
    "shader": "shader",
    "shaders": "shader",
    "modpack": "modpack",
    "modpacks": "modpack",
    "all": "all",
    "any": "all",
    "alltypes": "all",
    "allcontent": "all",
}


def _normalize_project_type(value: str) -> str | None:
    """Normalize common singular/plural labels without accepting plugins/servers."""
    normalized = "".join(char for char in (value or "").casefold() if char.isalnum())
    project_type = _TYPE_ALIASES.get(normalized)
    if project_type in _SUPPORTED_TYPES or project_type == "all":
        return project_type
    return None


def _facets_for(project_type: str, game_version: str, loader: str) -> list[list[str]]:
    """Build Modrinth facets; facets within a group are OR, groups are AND."""
    if project_type == "all":
        # Explicit allowlist: plugins and server listings are deliberately omitted.
        facets: list[list[str]] = [[
            "project_type:mod",
            "project_type:modpack",
            "project_type:resourcepack",
            "project_type:shader",
            "all_project_types:datapack",
        ]]
    elif project_type == "datapack":
        # Datapacks can be attached to a project without being its primary type.
        facets = [["all_project_types:datapack"]]
    else:
        facets = [[f"project_type:{project_type}"]]

    if game_version:
        facets.append([f"versions:{game_version}"])
    if loader:
        facets.append([f"categories:{loader}"])
    return facets


class ModrinthSearchTools(Toolkit):
    """Search the official Modrinth API for Minecraft content projects."""

    def __init__(self, *, timeout: float = 12.0) -> None:
        self.timeout = max(1.0, float(timeout))
        instructions = (
            "Use search_modrinth_projects for current Minecraft content hosted on Modrinth: "
            "mods, resource packs, data packs, shaders, and modpacks. Set project_type to "
            "the requested category (mod, resourcepack, datapack, shader, or modpack); use "
            "all only when the user asks for broad Modrinth content or category is genuinely "
            "unclear. This tool intentionally does not search plugins or servers. "
            "Honor exact Minecraft game_version constraints. Use loader only for mods or "
            "modpacks, and only when the user specifies one. The API defaults to 10 results; "
            "never claim compatibility unless the returned version/loader metadata supports it. "
            "Only recommend projects and URLs returned by this tool."
        )
        super().__init__(
            name="modrinth_search",
            tools=[],
            async_tools=[(self.search_modrinth_projects, "search_modrinth_projects")],
            instructions=instructions,
            # Agno's Toolkit defaults add_instructions to False; make the usage guidance
            # part of Rem's system context explicitly.
            add_instructions=True,
        )

    async def search_modrinth_projects(
        self,
        query: str,
        project_type: str = "mod",
        game_version: str = "",
        loader: str = "",
        max_results: int = 10,
        run_context: RunContext | None = None,
    ) -> dict[str, Any]:
        """Search Modrinth for mods, resource packs, data packs, shaders, or modpacks.

        Args:
            query: The project name or Minecraft feature to find.
            project_type: Category to search: ``mod`` (default), ``resourcepack``,
                ``datapack``, ``shader``, ``modpack``, or ``all``. ``all`` searches only
                those five supported categories. Plugins and servers are excluded.
                Common plurals such as ``shaders`` and ``modpacks`` are accepted too.
            game_version: Optional exact Minecraft version, for example ``1.21.1`` or
                ``26.1``. Results are filtered to projects matching that version.
            loader: Optional loader filter: ``fabric``, ``forge``, ``neoforge``, ``quilt``,
                or ``quilted_fabric``. Use only with mods/modpacks or a broad ``all`` search.
            max_results: Number of results to return, from 1 to 10. Defaults to 10.

        Returns:
            A structured result containing supported project metadata and canonical URLs.
            Search failures return ``ok=False`` and an empty result list.
        """
        query = " ".join((query or "").split())
        if len(query) < 2:
            return {
                "ok": False,
                "error": "Provide a project name or feature with at least 2 characters.",
                "results": [],
            }

        normalized_type = _normalize_project_type(project_type)
        if normalized_type is None:
            return {
                "ok": False,
                "error": (
                    "Unsupported project_type. Use mod, resourcepack, datapack, shader, "
                    "modpack, or all. Plugins and servers are not supported by this tool."
                ),
                "results": [],
            }

        try:
            limit = max(1, min(int(max_results), 10))
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "max_results must be an integer from 1 to 10.", "results": []}

        version_filter = " ".join((game_version or "").split())
        normalized_loader = "".join(char for char in (loader or "").strip().casefold() if char.isalnum() or char == "_")
        if normalized_loader and normalized_loader not in _ALLOWED_LOADERS:
            return {
                "ok": False,
                "error": "Unsupported loader. Use Fabric, Forge, NeoForge, Quilt, or Quilted Fabric.",
                "results": [],
            }
        if normalized_loader and normalized_type not in {"mod", "modpack", "all"}:
            return {
                "ok": False,
                "error": "Loader filtering applies to mods and modpacks, not resource packs, data packs, or shaders.",
                "results": [],
            }

        facets = _facets_for(normalized_type, version_filter, normalized_loader)
        # A broad search may include mixed-type results despite facets being OR-ed.
        # Ask for a larger candidate pool then return at most the user's requested cap.
        api_limit = min(100, limit * 4) if normalized_type == "all" else limit
        params = {"query": query, "limit": api_limit, "facets": json.dumps(facets)}

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": _MODRINTH_USER_AGENT},
                follow_redirects=True,
            ) as client:
                response = await client.get(_MODRINTH_SEARCH_URL, params=params)
                response.raise_for_status()
                payload = response.json()

            if not isinstance(payload, dict) or not isinstance(payload.get("hits", []), list):
                logger.warning("Modrinth search returned an unexpected response shape | query=%r", query)
                return {"ok": False, "error": "Modrinth returned an unexpected response format.", "results": []}

            results: list[dict[str, Any]] = []
            for hit in payload.get("hits", []):
                if not isinstance(hit, dict):
                    continue
                slug = str(hit.get("slug") or "").strip()
                if not slug:
                    continue

                primary_type = str(hit.get("project_type") or "").strip().casefold()
                all_types_value = hit.get("all_project_types") or []
                all_types = [str(value).casefold() for value in all_types_value if isinstance(value, str)] if isinstance(all_types_value, list) else []

                # Hard filter unsupported top-level types even if the API responds with
                # unexpected results or ignores a facet. Never surface server/plugin items.
                if primary_type in {"plugin", "server"}:
                    continue
                if normalized_type == "all":
                    if primary_type not in _PRIMARY_TYPES and "datapack" not in all_types:
                        continue
                elif normalized_type == "datapack":
                    if "datapack" not in all_types and primary_type != "datapack":
                        continue
                elif primary_type and primary_type != normalized_type:
                    continue

                if normalized_type == "datapack":
                    link_type = "datapack"
                elif primary_type in _PRIMARY_TYPES:
                    link_type = primary_type
                else:
                    # Some search responses expose datapacks only through all_project_types.
                    link_type = "datapack" if "datapack" in all_types else normalized_type

                results.append({
                    "title": hit.get("title") or slug,
                    "summary": hit.get("description") or "",
                    "project_type": link_type,
                    "primary_project_type": primary_type or None,
                    "all_project_types": all_types,
                    "url": f"https://modrinth.com/{link_type}/{slug}",
                    "downloads": hit.get("downloads"),
                    "followers": hit.get("follows", hit.get("followers")),
                    "latest_indexed_update": hit.get("date_modified"),
                    "client_side": hit.get("client_side"),
                    "server_side": hit.get("server_side"),
                    "environment": hit.get("environment", []),
                    "categories": hit.get("categories") or hit.get("display_categories", []),
                    "game_versions": hit.get("versions", []),
                })
                if len(results) >= limit:
                    break

            return {
                "ok": True,
                "query": query,
                "requested_project_type": normalized_type,
                "results": results,
                "source": "Modrinth public API",
            }
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.exception("Modrinth search failed | query=%r | project_type=%s", query, normalized_type)
            return {
                "ok": False,
                "error": f"Modrinth search failed: {type(exc).__name__}: {exc}",
                "results": [],
            }
        except Exception as exc:
            logger.exception("Unexpected Modrinth search failure | query=%r | project_type=%s", query, normalized_type)
            return {
                "ok": False,
                "error": f"Modrinth search failed: {type(exc).__name__}: {exc}",
                "results": [],
            }
