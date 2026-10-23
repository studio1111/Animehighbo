import unittest
from datetime import date

from tools.archive_catalog import REQUIRED_SHOW_SLUGS, _show_page_url, _show_title_from_html, merge_existing_manifest, select_show_episodes


class ArchiveCatalogSelectionTests(unittest.TestCase):
    def test_required_long_running_shows_are_always_checked(self):
        self.assertIn("one-piece", REQUIRED_SHOW_SLUGS)
        self.assertIn("detective-conan", REQUIRED_SHOW_SLUGS)

    def test_subsplease_show_slug_is_resolved_under_shows_path(self):
        self.assertEqual(
            _show_page_url({"page": "mairimashita-iruma-kun-s4"}),
            "https://subsplease.org/shows/mairimashita-iruma-kun-s4/",
        )

    def test_external_show_page_urls_are_rejected(self):
        self.assertEqual(_show_page_url({"page": "https://evil.example/show"}), "")

    def test_show_title_uses_clean_entry_heading_not_page_suffix(self):
        page = '<title>Example S4 downloads – SubsPlease</title><h1 class="entry-title">Example S4</h1>'
        self.assertEqual(_show_title_from_html(page, "fallback"), "Example S4")

    def test_new_series_since_cutoff_includes_episode_one_through_latest(self):
        rows = [
            {"title": "New Saga - 01", "episode": "1", "release_date": "2026-09-05T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:aaaa"}]},
            {"title": "New Saga - 02", "episode": "2", "release_date": "2026-09-12T10:00:00Z",
             "downloads": [{"res": "720", "magnet": "magnet:?xt=urn:btih:bbbb"}]},
            {"title": "New Saga - 03", "episode": "3", "release_date": "2026-09-19T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        selected = select_show_episodes("New Saga", rows, date(2026, 9, 1))
        self.assertEqual([row["episode"] for row in selected], [1, 2, 3])
        self.assertEqual([row["quality"] for row in selected], ["1080p", "720p", "1080p"])
        self.assertEqual(
            [row["release_date"] for row in selected],
            ["2026-09-05", "2026-09-12", "2026-09-19"],
        )

    def test_preserves_curated_authorized_entries_missing_from_catalog_api(self):
        generated = [{
            "title": "New Saga - 01 (1080p)",
            "episode": 1,
            "quality": "1080p",
            "uri": "magnet:?xt=urn:btih:new",
            "release_date": "2026-10-10",
            "authorized_for_redistribution": True,
        }]
        existing = [
            {
                "title": "Curated Saga - 01 (1080p)",
                "episode": 1,
                "quality": "1080p",
                "uri": "magnet:?xt=urn:btih:curated",
                "release_date": "2026-10-07",
                "authorized_for_redistribution": True,
            },
            {
                "title": "Old Saga - 01 (1080p)",
                "episode": 1,
                "quality": "1080p",
                "uri": "magnet:?xt=urn:btih:old",
                "release_date": "2026-08-07",
                "authorized_for_redistribution": True,
            },
            {
                "title": "Unapproved Saga - 01 (1080p)",
                "episode": 1,
                "quality": "1080p",
                "uri": "magnet:?xt=urn:btih:unapproved",
                "release_date": "2026-10-08",
                "authorized_for_redistribution": False,
            },
        ]

        merged = merge_existing_manifest(generated, existing, date(2026, 9, 1))

        self.assertEqual(
            [row["title"] for row in merged],
            ["Curated Saga - 01 (1080p)", "New Saga - 01 (1080p)"],
        )
        self.assertEqual([row["release_date"] for row in merged], ["2026-10-07", "2026-10-10"])

    def test_ongoing_series_keeps_all_episodes_released_since_cutoff(self):
        rows = [
            {"title": "Long Runner - 100", "episode": "100", "release_date": "2026-08-28T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:aaaa"}]},
            {"title": "Long Runner - 101", "episode": "101", "release_date": "2026-10-04T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:bbbb"}]},
            {"title": "Long Runner - 102", "episode": "102", "release_date": "2026-10-11T10:00:00Z",
             "downloads": [{"res": "720", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        selected = select_show_episodes("Long Runner", rows, date(2026, 10, 1))
        self.assertEqual([row["episode"] for row in selected], [101, 102])
        self.assertEqual([row["release_date"] for row in selected], ["2026-10-04", "2026-10-11"])

    def test_season_started_before_cutoff_includes_only_post_cutoff_episodes(self):
        rows = [
            {"title": "Current Season - 01", "episode": "1", "release_date": "2026-09-28T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:aaaa"}]},
            {"title": "Current Season - 02", "episode": "2", "release_date": "2026-10-05T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:bbbb"}]},
            {"title": "Current Season - 03", "episode": "3", "release_date": "2026-10-12T10:00:00Z",
             "downloads": [{"res": "720", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        selected = select_show_episodes("Current Season", rows, date(2026, 10, 1))
        self.assertEqual([row["episode"] for row in selected], [2, 3])

    def test_missing_source_skips_only_that_episode_and_keeps_later_releases(self):
        rows = [
            {"title": "Gap Show - 01", "episode": "1", "release_date": "2026-10-05T10:00:00Z",
             "downloads": [{"res": "480", "magnet": "magnet:?xt=urn:btih:aaaa"}]},
            {"title": "Gap Show - 03", "episode": "3", "release_date": "2026-10-19T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        selected = select_show_episodes("Gap Show", rows, date(2026, 10, 1))
        self.assertEqual([row["episode"] for row in selected], [3])
        self.assertEqual(selected[0]["release_date"], "2026-10-19")


if __name__ == "__main__":
    unittest.main()
