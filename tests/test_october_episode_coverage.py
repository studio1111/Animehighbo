import unittest
from datetime import date

from tools.october_episode_coverage import build_coverage_report


def seasonal_row():
    return {
        "anilist_id": 1,
        "title_english": "Chii Fuyo",
        "title_romaji": "Chii Fuyo",
        "title_native": "チイふよ",
        "synonyms": [],
        "season": "FALL",
        "season_year": 2026,
        "format": "TV",
        "status": "RELEASING",
        "anilist_url": "https://anilist.co/anime/1",
    }


class OctoberEpisodeCoverageTests(unittest.TestCase):
    def build(self, manifest, releases, seasonal=None):
        return build_coverage_report(
            {"titles": [seasonal or seasonal_row()]},
            manifest,
            [],
            {"releases": releases},
            date(2026, 10, 29),
        )

    def test_rss_episode_already_in_manifest_is_not_flagged_missing(self):
        report = self.build(
            [{
                "title": "Chii Fuyo - 04 (1080p)",
                "episode": 4,
                "release_date": "2026-10-27",
                "quality": "1080p",
            }],
            [{
                "title": "Chii Fuyo",
                "episode": 4,
                "release_date": "2026-10-27",
                "in_archive_manifest": True,
            }],
        )
        row = next(item for item in report["titles"] if item["title_english"] == "Chii Fuyo")
        self.assertFalse(row["review_required"])
        self.assertEqual(row["source_coverage"]["rss"]["unmatched_episodes"], [])
        self.assertEqual(report["summary"]["titles_needing_review"], 0)
        self.assertEqual(report["summary"]["distinct_unmatched_rss_episodes"], 0)

    def test_rss_episode_not_in_manifest_is_flagged_for_reconciliation(self):
        report = self.build(
            [],
            [{
                "title": "Chii Fuyo",
                "episode": 4,
                "release_date": "2026-10-27",
                "in_archive_manifest": False,
            }],
        )
        row = next(item for item in report["titles"] if item["title_english"] == "Chii Fuyo")
        self.assertTrue(row["review_required"])
        self.assertEqual(len(row["source_coverage"]["rss"]["unmatched_episodes"]), 1)
        self.assertEqual(report["summary"]["distinct_unmatched_rss_episodes"], 1)

    def test_seasonal_title_without_episode_metadata_is_not_assumed_missing(self):
        report = self.build([], [])
        row = next(item for item in report["titles"] if item["title_english"] == "Chii Fuyo")
        self.assertFalse(row["review_required"])
        self.assertEqual(report["summary"]["titles_needing_review"], 0)


if __name__ == "__main__":
    unittest.main()
