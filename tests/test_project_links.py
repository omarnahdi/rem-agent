import json
import unittest
from types import SimpleNamespace

from rem.text_utils import collect_verified_project_links, normalize_project_markdown_links


SODIUM = "https://modrinth.com/mod/sodium"
JOURNEYMAP = "https://modrinth.com/mod/journeymap"


class ProjectLinkNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.verified = {
            "sodium": SODIUM,
            "https://modrinth.com/mod/sodium": SODIUM,
            "journeymap": JOURNEYMAP,
            "https://modrinth.com/mod/journeymap": JOURNEYMAP,
        }

    def test_removes_orphan_markdown_suffix_from_project_link(self):
        source = "**Sodium** — [Sodium](https://modrinth.com/mod/sodium)**"
        self.assertEqual(
            normalize_project_markdown_links(source, self.verified),
            "**Sodium** — [Sodium](https://modrinth.com/mod/sodium)",
        )

    def test_preserves_balanced_bold_around_link(self):
        source = "**[Sodium](https://modrinth.com/mod/sodium)** — renderer optimization."
        self.assertEqual(normalize_project_markdown_links(source, self.verified), source)

    def test_plain_url_with_stray_markdown_suffix_is_cleaned(self):
        source = "Install page: https://modrinth.com/mod/sodium)**"
        self.assertEqual(
            normalize_project_markdown_links(source, self.verified),
            f"Install page: {SODIUM}",
        )

    def test_wrong_href_is_replaced_by_verified_project_for_label(self):
        source = "[JourneyMap](https://modrinth.com/mod/sodium)**"
        self.assertEqual(
            normalize_project_markdown_links(source, self.verified),
            f"[JourneyMap]({JOURNEYMAP})",
        )

    def test_unknown_markdown_project_is_not_left_clickable(self):
        source = "[Unverified Project](https://modrinth.com/mod/not-a-real-project)**"
        self.assertEqual(
            normalize_project_markdown_links(source, self.verified),
            "Unverified Project",
        )

    def test_unknown_plain_url_is_not_left_clickable(self):
        source = "https://modrinth.com/mod/not-a-real-project)**"
        self.assertEqual(
            normalize_project_markdown_links(source, self.verified),
            "[unverified Modrinth project link]",
        )

    def test_collects_urls_from_actual_modrinth_search_tool_output(self):
        result = {
            "ok": True,
            "results": [
                {"title": "Sodium", "slug": "sodium", "url": SODIUM},
                {"title": "JourneyMap", "slug": "journeymap", "url": JOURNEYMAP},
            ],
        }
        tool = SimpleNamespace(name="search_modrinth_projects", result=json.dumps(result))
        output = SimpleNamespace(tools=[tool], events=[])
        links = collect_verified_project_links(output)
        self.assertEqual(links["sodium"], SODIUM)
        self.assertEqual(links["journeymap"], JOURNEYMAP)
        self.assertEqual(
            normalize_project_markdown_links("[JourneyMap](https://modrinth.com/mod/sodium)**", links),
            f"[JourneyMap]({JOURNEYMAP})",
        )

    def test_collects_python_repr_of_modrinth_tool_result(self):
        # Agno may persist a tool payload as Python repr instead of JSON. The
        # project-link parser must recover real URLs from that result shape too.
        result = {
            "ok": True,
            "query": "horror",
            "source": "Modrinth public API",
            "results": [{
                "title": "The Mimicer (horror)",
                "slug": "the-mimicer-horror",
                "url": "https://modrinth.com/mod/the-mimicer-horror",
                "project_type": "mod",
            }],
        }
        tool = SimpleNamespace(name="search_modrinth_projects", result=repr(result))
        output = SimpleNamespace(tools=[tool], events=[], messages=[])
        links = collect_verified_project_links(output)
        self.assertEqual(links["the mimicer (horror)"], "https://modrinth.com/mod/the-mimicer-horror")

    def test_links_plain_verified_titles_and_appends_omitted_search_results(self):
        mimicer = "https://modrinth.com/mod/the-mimicer-horror"
        obsessed = "https://modrinth.com/mod/obsessed"
        verified = {
            "the mimicer (horror)": mimicer,
            "the-mimicer-horror": mimicer,
            "the obsessed": obsessed,
            "obsessed": obsessed,
        }
        source = "Here are two horror mods: **The Mimicer (horror)** is frightening."
        normalized = normalize_project_markdown_links(source, verified)
        self.assertIn("**[The Mimicer (horror)](https://modrinth.com/mod/the-mimicer-horror)**", normalized)
        self.assertIn("[The Obsessed](https://modrinth.com/mod/obsessed)", normalized)
        self.assertIn("Verified project links returned by the search tool:", normalized)

    def test_empty_catalog_does_not_strip_an_existing_canonical_link(self):
        source = "Try [Sodium](https://modrinth.com/mod/sodium) for performance."
        self.assertEqual(normalize_project_markdown_links(source, verified_links={}), source)

    def test_collects_urls_from_tool_messages_when_aggregate_tools_are_empty(self):
        result = {
            "ok": True,
            "query": "sodium",
            "results": [{"title": "Sodium", "slug": "sodium", "url": SODIUM}],
            "source": "Modrinth public API",
        }
        message = SimpleNamespace(role="tool", name="search_modrinth_projects", content=json.dumps(result))
        output = SimpleNamespace(tools=[], events=[], messages=[message])
        links = collect_verified_project_links(output)
        self.assertEqual(links["sodium"], SODIUM)
        self.assertEqual(
            normalize_project_markdown_links("[Sodium](https://modrinth.com/mod/not-sodium)**", links),
            f"[Sodium]({SODIUM})",
        )


if __name__ == "__main__":
    unittest.main()
