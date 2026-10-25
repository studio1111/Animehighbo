import unittest

from tools.publish_anime_catalog import caption, series_title, unique_series


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

    def test_caption_includes_native_title_and_fallbacks(self):
        text = caption({"query_title": "Example", "title": "Example", "native": "",
                        "romaji": "Example", "year": None, "episodes": None})
        self.assertIn("🇯🇵 —", text)
        self.assertIn("Episodes: —", text)
        self.assertIn("poster only", text)


if __name__ == "__main__":
    unittest.main()
