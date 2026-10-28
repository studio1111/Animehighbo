import unittest
from datetime import date

from tools.seasonal_discovery import (
    archive_title_keys,
    build_report,
    normalize_title,
    upcoming_seasons,
)


class SeasonalDiscoveryTests(unittest.TestCase):
    def test_upcoming_seasons_roll_over_year(self):
        self.assertEqual(
            upcoming_seasons(date(2026, 10, 25), 4),
            [(2026, "FALL"), (2027, "WINTER"), (2027, "SPRING"), (2027, "SUMMER")],
        )

    def test_invalid_month_is_rejected(self):
        with self.assertRaises(ValueError):
            from tools.seasonal_discovery import season_for_month
            season_for_month(13)

    def test_normalize_episode_title_to_series(self):
        self.assertEqual(
            normalize_title("Blue Box - 02 (1080p)"),
            "blue box",
        )
        self.assertEqual(
            normalize_title("Blue Box - 02"),
            "blue box",
        )

    def test_archive_title_keys_match_series_prefix(self):
        keys = archive_title_keys([{"title": "Blue Box - 02 (1080p)"}])
        self.assertIn("blue box", keys)

    def test_report_flags_unmatched_seasonal_titles_as_coverage_gaps(self):
        report = build_report(
            [{"title": "Blue Box - 02 (1080p)"}],
            [
                {"id": 1, "seasonYear": 2027, "season": "WINTER", "format": "TV",
                 "status": "NOT_YET_RELEASED", "siteUrl": "https://anilist.co/anime/1",
                 "title": {"english": "Blue Box", "romaji": "Ao no Hako", "native": "アオのハコ"},
                 "synonyms": []},
                {"id": 2, "seasonYear": 2027, "season": "WINTER", "format": "TV",
                 "status": "NOT_YET_RELEASED", "siteUrl": "https://anilist.co/anime/2",
                 "title": {"english": "New Show", "romaji": "New Show", "native": None},
                 "synonyms": []},
            ],
            date(2026, 10, 25),
            [(2027, "WINTER")],
        )
        self.assertEqual(report["summary"]["seasonal_titles"], 2)
        self.assertEqual(report["summary"]["matched_in_manifest"], 1)
        self.assertEqual(report["summary"]["review_required"], 1)
        self.assertNotIn("authorized_for_redistribution", report["titles"][1])


if __name__ == "__main__":
    unittest.main()


class OctoberCoverageRegressionTests(unittest.TestCase):
    def test_coverage_report_module_is_available(self):
        from tools.october_episode_coverage import build_coverage_report, normalize_series
        self.assertEqual(normalize_series('Blue Box - 02 (1080p)'), 'blue box')
        report = build_coverage_report(
            {'titles': [{'anilist_id': 1, 'title_english': 'Blue Box', 'title_romaji': 'Ao no Hako', 'title_native': None, 'season': 'FALL', 'season_year': 2026, 'format': 'TV', 'status': 'RELEASING', 'anilist_url': 'https://anilist.co/anime/1'}]},
            [{'title': 'Blue Box - 02 (1080p)', 'episode': 2, 'release_date': '2026-10-10'}, {'title': 'Blue Box - 02 (720p)', 'episode': 2, 'release_date': '2026-10-10'}],
            [{'title': 'Blue Box - 03 (1080p)', 'episode': 3, 'release_date': '2026-10-17'}],
            {'releases': [{'title': 'Blue Box', 'episode': 3, 'release_date': '2026-10-17'}]},
            date(2026, 10, 25),
        )
        self.assertEqual(report['summary']['distinct_manifest_episodes'], 1)
        self.assertEqual(report['summary']['distinct_candidate_episodes'], 1)
        self.assertEqual(report['summary']['distinct_rss_episodes'], 1)
        self.assertIn('limitations', report)
