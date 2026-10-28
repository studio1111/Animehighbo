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

if __name__ == "__main__": unittest.main()
