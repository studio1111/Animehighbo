import unittest
from tools.refresh_anime_metadata import merge_nonempty, refresh_records

class RefreshAnimeMetadataTests(unittest.TestCase):
    def test_merge_preserves_old_values_and_adds_synopsis(self):
        merged = merge_nonempty({"title": {"english": "Example"}, "genres": ["Fantasy"]},
            {"title": {"romaji": "Example Romaji"}, "genres": ["Action"], "description": "A verified synopsis."})
        self.assertEqual(merged["title"], {"english": "Example", "romaji": "Example Romaji"})
        self.assertEqual(merged["genres"], ["Action"])
        self.assertEqual(merged["description"], "A verified synopsis.")

    def test_curated_exact_season_records_skip_slow_fuzzy_lookups(self):
        records = {
            "curated": {
                "title": "Verified Season 2",
                "poster_url": "https://cdn.example/season2.jpg",
                "metadata": {
                    "metadata_source": "MyAnimeList exact-season record",
                    "description": "Verified season-specific synopsis.",
                    "seasonYear": 2026,
                    "genres": ["Fantasy"],
                    "status": "RELEASING",
                    "studios": {"nodes": [{"name": "Studio Example"}]},
                    "coverImage": {"extraLarge": "https://cdn.example/season2.jpg"},
                },
            }
        }
        calls = []
        refreshed, with_synopsis = refresh_records(records, lambda title: calls.append(title) or {})
        self.assertEqual(calls, [])
        self.assertEqual(refreshed, 0)
        self.assertEqual(with_synopsis, 1)

    def test_one_lookup_failure_does_not_stop_other_series(self):
        records = {"failed": {"title": "Unavailable Anime", "poster_url": "https://example.org/poster.jpg"},
                   "good": {"title": "Known Anime", "poster_url": "https://example.org/known.jpg"}}
        def lookup(title):
            if title == "Unavailable Anime": raise RuntimeError("catalog offline")
            return {"description": "A real synopsis.", "seasonYear": 2026}
        refreshed, with_synopsis = refresh_records(records, lookup)
        self.assertEqual(refreshed, 1); self.assertEqual(with_synopsis, 1)
        self.assertNotIn("metadata", records["failed"])
        self.assertEqual(records["good"]["metadata"]["description"], "A real synopsis.")
        self.assertEqual(records["good"]["poster_url"], "https://example.org/known.jpg")


    def test_verified_season_specific_metadata_is_not_overwritten_by_fuzzy_lookup(self):
        from tools.refresh_anime_metadata import merge_nonempty
        old = {
            "metadata_source": "MyAnimeList exact-season record",
            "description": "Verified season-two synopsis.",
            "seasonYear": 2026,
            "coverImage": {"extraLarge": "https://cdn.example/season2.jpg"},
        }
        fresh = {
            "description": "Wrong previous-season synopsis.",
            "seasonYear": 2023,
            "coverImage": {"extraLarge": "https://cdn.example/season1.jpg"},
        }
        merged = merge_nonempty(old, fresh)
        self.assertEqual(merged["description"], "Verified season-two synopsis.")
        self.assertEqual(merged["seasonYear"], 2026)
        self.assertEqual(merged["coverImage"]["extraLarge"], "https://cdn.example/season2.jpg")


    def test_official_season_metadata_with_source_url_is_not_overwritten(self):
        old = {
            "metadata_source": "Official Ao Ashi Season 2 website",
            "metadata_source_url": "https://aoashi-pr.com/",
            "description": "Official season-two synopsis.",
            "seasonYear": 2026,
        }
        fresh = {
            "description": "Generic synopsis for the first season.",
            "seasonYear": 2022,
        }
        merged = merge_nonempty(old, fresh)
        self.assertEqual(merged["description"], "Official season-two synopsis.")
        self.assertEqual(merged["seasonYear"], 2026)

if __name__ == "__main__": unittest.main()
