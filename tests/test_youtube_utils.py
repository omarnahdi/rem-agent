import unittest

from rem.youtube_utils import (
    extract_piped_items,
    format_duration,
    format_published,
    normalize_piped_results,
)


class PipedResponseShapeTests(unittest.TestCase):
    def test_legacy_object_with_items_is_supported(self):
        payload = {"items": [{"type": "stream", "title": "Oozing farm", "url": "/watch?v=abc12345678"}]}
        self.assertEqual(extract_piped_items(payload), payload["items"])

    def test_list_response_variant_is_supported(self):
        payload = [{"type": "stream", "title": "Tutorial", "id": "abc12345678"}]
        self.assertEqual(extract_piped_items(payload), payload)

    def test_unexpected_response_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_piped_items({"items": {"not": "a list"}})

    def test_relative_watch_path_is_normalized_to_valid_youtube_url(self):
        result = normalize_piped_results([{
            "type": "stream",
            "title": "Minecraft Oozing slime farm",
            "uploaderName": "Example Channel",
            "url": "/watch?v=abc12345678",
            "duration": 3723,
            "views": 1024,
            "uploadDate": "2026-10-01T00:00:00Z",
            "shortDescription": "A test description",
        }])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://www.youtube.com/watch?v=abc12345678")
        self.assertEqual(result[0]["duration"], "1:02:03")
        self.assertEqual(result[0]["channel"], "Example Channel")
        self.assertEqual(result[0]["published"], "2026-10-01")

    def test_youtu_be_links_are_normalized(self):
        result = normalize_piped_results([{
            "title": "Video", "type": "stream", "url": "https://youtu.be/abc12345678"
        }])
        self.assertEqual(result[0]["url"], "https://www.youtube.com/watch?v=abc12345678")

    def test_unrelated_links_and_duplicates_are_ignored(self):
        result = normalize_piped_results([
            {"title": "Not YouTube", "url": "https://example.org/video"},
            {"type": "channel", "title": "Channel", "url": "/channel/abc"},
            {"title": "Video", "type": "stream", "url": "/watch?v=abc12345678"},
            {"title": "Same video", "type": "stream", "url": "/watch?v=abc12345678"},
        ])
        self.assertEqual(len(result), 1)

    def test_duration_and_published_formatting(self):
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3600), "1:00:00")
        self.assertEqual(format_published("2026-10-02T12:30:00Z"), "2026-10-02")
        self.assertEqual(format_published("3 months ago"), "3 months ago")


if __name__ == "__main__":
    unittest.main()
