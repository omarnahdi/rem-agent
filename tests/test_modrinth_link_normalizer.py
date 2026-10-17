import unittest

from rem.text_utils import normalize_modrinth_markdown_links


class NormalizeModrinthMarkdownLinksTests(unittest.TestCase):
    def test_repairs_url_label_and_stray_markdown_suffix(self):
        source = (
            r"Try [https://modrinth.com/mod/hollowsteve)\*\*]"
            r"(https://modrinth.com/mod/hollowsteve\)**)) for details."
        )
        expected = (
            "Try [https://modrinth.com/mod/hollowsteve]"
            "(https://modrinth.com/mod/hollowsteve) for details."
        )
        self.assertEqual(normalize_modrinth_markdown_links(source), expected)

    def test_repairs_bad_href_but_preserves_project_name_label(self):
        source = r"Try [Hollow Steve [HORROR]](https://modrinth.com/mod/hollowsteve\)**) now."
        expected = "Try [Hollow Steve [HORROR]](https://modrinth.com/mod/hollowsteve) now."
        self.assertEqual(normalize_modrinth_markdown_links(source), expected)

    def test_leaves_correct_modrinth_link_unchanged(self):
        source = "Try [Hollow Steve](https://modrinth.com/mod/hollowsteve) for details."
        self.assertEqual(normalize_modrinth_markdown_links(source), source)

    def test_leaves_non_modrinth_links_unchanged(self):
        source = r"Try [example](https://example.com/page\)**) now."
        self.assertEqual(normalize_modrinth_markdown_links(source), source)

    def test_repairs_multiple_bad_modrinth_links(self):
        source = (
            r"[https://modrinth.com/mod/alpha)\*\*]"
            r"(https://modrinth.com/mod/alpha\)**) and "
            r"[https://modrinth.com/mod/beta)\*\*]"
            r"(https://modrinth.com/mod/beta\)**)"
        )
        expected = (
            "[https://modrinth.com/mod/alpha](https://modrinth.com/mod/alpha) and "
            "[https://modrinth.com/mod/beta](https://modrinth.com/mod/beta)"
        )
        self.assertEqual(normalize_modrinth_markdown_links(source), expected)

    def test_plain_modrinth_url_is_not_rewritten(self):
        source = "https://modrinth.com/mod/hollowsteve"
        self.assertEqual(normalize_modrinth_markdown_links(source), source)


if __name__ == "__main__":
    unittest.main()
