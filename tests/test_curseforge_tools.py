from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from rem.curseforge_tools import CurseForgeSearchTools


class CurseForgeSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_api_key_returns_clear_error_without_request(self):
        tool = CurseForgeSearchTools(api_key=None)
        with patch("rem.curseforge_tools.httpx.AsyncClient") as client_factory:
            result = await tool.search_curseforge_projects("map mod")
        self.assertFalse(result["ok"])
        self.assertIn("CURSEFORGE_API_KEY", result["error"])
        client_factory.assert_not_called()

    async def test_rejects_loader_filter_without_game_version(self):
        tool = CurseForgeSearchTools(api_key="test-key")
        with patch("rem.curseforge_tools.httpx.AsyncClient") as client_factory:
            result = await tool.search_curseforge_projects("map mod", loader="fabric")
        self.assertFalse(result["ok"])
        self.assertIn("game_version", result["error"])
        client_factory.assert_not_called()

    async def test_search_normalizes_response_and_sends_expected_filters(self):
        tool = CurseForgeSearchTools(api_key="test-key")
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [{
                "id": 123,
                "slug": "example-mod",
                "name": "Example Mod",
                "summary": "A test mod",
                "links": {"websiteUrl": "https://www.curseforge.com/minecraft/mc-mods/example-mod"},
                "downloadCount": 900,
                "dateModified": "2026-10-01T00:00:00Z",
                "categories": [{"name": "Adventure"}],
                "authors": [{"name": "Example Author"}],
                "latestFilesIndexes": [
                    {"gameVersion": "1.21.1", "modLoader": 4},
                    {"gameVersion": "1.21.1", "modLoader": 1},
                ],
                "logo": {"url": "https://example.com/logo.png"},
            }]
        }
        client = AsyncMock()
        client.get.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False

        with patch("rem.curseforge_tools.httpx.AsyncClient", return_value=context):
            result = await tool.search_curseforge_projects(
                " minimap ", game_version="1.21.1", loader="Fabric", max_results=50
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["title"], "Example Mod")
        self.assertEqual(result["results"][0]["loaders"], ["fabric", "forge"])
        self.assertEqual(result["results"][0]["game_versions"], ["1.21.1"])
        self.assertEqual(result["results"][0]["url"], "https://www.curseforge.com/minecraft/mc-mods/example-mod")
        call_args = client.get.call_args
        self.assertEqual(call_args.args[0], "https://api.curseforge.com/v1/mods/search")
        self.assertEqual(call_args.kwargs["params"]["gameId"], 432)
        self.assertEqual(call_args.kwargs["params"]["classId"], 6)
        self.assertEqual(call_args.kwargs["params"]["modLoaderType"], 4)
        self.assertEqual(call_args.kwargs["params"]["pageSize"], 10)

    async def test_rejects_unknown_loader(self):
        tool = CurseForgeSearchTools(api_key="test-key")
        result = await tool.search_curseforge_projects("map mod", game_version="1.21.1", loader="unknown")
        self.assertFalse(result["ok"])
        self.assertIn("Unsupported CurseForge loader", result["error"])


if __name__ == "__main__":
    unittest.main()
