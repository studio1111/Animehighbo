import unittest
from unittest.mock import Mock, patch

from tools.publish_anime_catalog import caption, find_poster_kitsu, series_title, unique_series


class AnimePosterCatalogTests(unittest.TestCase):
    def test_series_title_strips_release_episode_and_quality(self):
        self.assertEqual(series_title("Example Anime - 03 (1080p)"), "Example Anime")
        self.assertEqual(series_title("Example Anime – 1215 [720p]"), "Example Anime")
        self.assertEqual(series_title("Example Anime - 4"), "Example Anime")

    def test_unique_series_deduplicates_different_episodes(self):
        rows = [
            {"title": "Example Anime - 01 (1080p)"},
            {"title": "Example Anime - 02 (720p)"},
            {"title": "Other Show - 01 (480p)"},
        ]
        self.assertEqual(unique_series(rows), ["Example Anime", "Other Show"])

    def test_kitsu_fallback_returns_poster_and_metadata(self):
        response = Mock()
        response.json.return_value = {
            "data": [{
                "id": "123",
                "attributes": {
                    "slug": "example-anime",
                    "canonicalTitle": "Example Anime",
                    "titles": {"en": "Example Anime", "ja_jp": "例アニメ"},
                    "posterImage": {"original": "https://media.example/poster.jpg"},
                    "startDate": "2026-10-01",
                    "episodeCount": 12,
                    "synopsis": "An example synopsis.",
                },
            }]
        }
        with patch("tools.publish_anime_catalog.SESSION.get", return_value=response):
            record = find_poster_kitsu("Example Anime")
        self.assertEqual(record["title"], "Example Anime")
        self.assertEqual(record["native"], "例アニメ")
        self.assertEqual(record["year"], 2026)
        self.assertEqual(record["episodes"], 12)
        self.assertEqual(record["poster_url"], "https://media.example/poster.jpg")

    def test_caption_includes_native_title_and_fallbacks(self):
        text = caption({"query_title": "Example", "title": "Example", "native": "",
                        "romaji": "Example", "year": None, "episodes": None})
        self.assertIn("🇯🇵 —", text)
        self.assertIn("Episodes: —", text)
        self.assertIn("poster only", text)


if __name__ == "__main__":
    unittest.main()
