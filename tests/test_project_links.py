import unittest

from rem.text_utils import normalize_project_markdown_links


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

    def test_does_not_rewrite_agent_authored_destination(self):
        source = "[JourneyMap](https://modrinth.com/mod/sodium)**"
        self.assertEqual(
            normalize_project_markdown_links(source, self.verified),
            f"[JourneyMap]({SODIUM})",
        )

    def test_preserves_agent_authored_project_link_after_suffix_cleanup(self):
        source = "[Unverified Project](https://modrinth.com/mod/not-a-real-project)**"
        self.assertEqual(
            normalize_project_markdown_links(source, self.verified),
            f"[Unverified Project](https://modrinth.com/mod/not-a-real-project)",
        )

    def test_preserves_agent_authored_plain_project_url_after_suffix_cleanup(self):
        source = "https://modrinth.com/mod/not-a-real-project)**"
        self.assertEqual(
            normalize_project_markdown_links(source, self.verified),
            "https://modrinth.com/mod/not-a-real-project",
        )

    def test_does_not_autolink_titles_or_append_omitted_search_results(self):
        mimicer = "https://modrinth.com/mod/the-mimicer-horror"
        obsessed = "https://modrinth.com/mod/obsessed"
        verified = {
            "the mimicer (horror)": mimicer,
            "the-mimicer-horror": mimicer,
            "the obsessed": obsessed,
            "obsessed": obsessed,
        }
        source = "Here are two horror mods: **The Mimicer (horror)** is frightening."
        # Tool results may be used to validate agent-authored URLs, but must not be
        # injected into the conversation by the postprocessor.
        self.assertEqual(normalize_project_markdown_links(source, verified), source)

    def test_empty_catalog_does_not_strip_an_existing_canonical_link(self):
        source = "Try [Sodium](https://modrinth.com/mod/sodium) for performance."
        self.assertEqual(normalize_project_markdown_links(source, verified_links={}), source)



if __name__ == "__main__":
    unittest.main()
