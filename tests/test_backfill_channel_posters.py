import unittest
from tools.backfill_channel_posters import build_poster_index, normalize_title, select_anchors

class ChannelPosterBackfillTests(unittest.TestCase):
    def test_title_normalization_matches_dash_variants(self):
        self.assertEqual(normalize_title("Dark Machine – The Animation"), normalize_title("Dark Machine - The Animation"))

    def test_index_only_accepts_https_poster_urls(self):
        index = build_poster_index({"published": {
            "valid anime": {"title": "Valid Anime", "poster_url": "https://cdn.example/poster.jpg"},
            "bad anime": {"title": "Bad Anime", "poster_url": "http://cdn.example/poster.jpg"},
        }})
        self.assertIn("valid anime", index)
        self.assertNotIn("bad anime", index)

    def test_only_state_records_with_message_id_and_title_are_selected(self):
        rows = select_anchors({"posters": {
            "one": {"title": "Anime A", "message_id": "22"},
            "two": {"title": "Anime B", "message_id": "21"},
            "bad": {"title": "Anime C", "message_id": "x"},
            "empty": {"message_id": "23"},
        }})
        self.assertEqual([row["message_id"] for row in rows], ["21", "22"])


    def test_curated_cached_metadata_overrides_mismatched_catalog_result(self):
        from tools.backfill_channel_posters import merge_cached_metadata
        catalog = {
            "title": {"english": "Wrong Previous Season"},
            "description": "Synopsis for a different season.",
            "seasonYear": 2023,
            "coverImage": {"extraLarge": "https://cdn.example/old.jpg"},
        }
        cached = {
            "title": {"english": "Correct Season 2", "romaji": "Correct Season II"},
            "description": "Verified second-season synopsis.",
            "seasonYear": 2026,
            "coverImage": {"extraLarge": "https://cdn.example/season2.jpg"},
        }
        merged = merge_cached_metadata(catalog, cached)
        self.assertEqual(merged["title"]["english"], "Correct Season 2")
        self.assertEqual(merged["description"], "Verified second-season synopsis.")
        self.assertEqual(merged["seasonYear"], 2026)
        self.assertEqual(merged["coverImage"]["extraLarge"], "https://cdn.example/season2.jpg")

    def test_empty_cached_metadata_does_not_erase_catalog_values(self):
        from tools.backfill_channel_posters import merge_cached_metadata
        catalog = {"description": "Verified catalog synopsis.", "genres": ["Sports"]}
        self.assertEqual(merge_cached_metadata(catalog, {}), catalog)

if __name__ == "__main__": unittest.main()
