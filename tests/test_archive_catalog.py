import unittest
from datetime import date

from tools.archive_catalog import REQUIRED_SHOW_SLUGS, _show_page_url, _show_title_from_html, select_show_episodes


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

    def test_old_series_without_episode_one_is_excluded_from_fresh_archive(self):
        rows = [
            {"title": "Long Runner - 100", "episode": "100", "release_date": "2026-08-28T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:aaaa"}]},
            {"title": "Long Runner - 101", "episode": "101", "release_date": "2026-09-04T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:bbbb"}]},
            {"title": "Long Runner - 102", "episode": "102", "release_date": "2026-09-11T10:00:00Z",
             "downloads": [{"res": "720", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        selected = select_show_episodes("Long Runner", rows, date(2026, 9, 1))
        self.assertEqual(selected, [], "Do not start an archive at an arbitrary late-season episode.")


    def test_current_season_with_episode_one_before_cutoff_still_starts_at_one(self):
        rows = [
            {"title": "Current Season - 01", "episode": "1", "release_date": "2026-08-28T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:aaaa"}]},
            {"title": "Current Season - 02", "episode": "2", "release_date": "2026-09-04T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:bbbb"}]},
            {"title": "Current Season - 03", "episode": "3", "release_date": "2026-09-11T10:00:00Z",
             "downloads": [{"res": "720", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        selected = select_show_episodes("Current Season", rows, date(2026, 9, 1))
        self.assertEqual([row["episode"] for row in selected], [1, 2, 3])

    def test_missing_episode_number_or_missing_1080p_and_720p_source_is_not_queued(self):
        rows = [
            {"title": "Gap Show - 01", "episode": "1", "release_date": "2026-09-05T10:00:00Z",
             "downloads": [{"res": "480", "magnet": "magnet:?xt=urn:btih:aaaa"}]},
            {"title": "Gap Show - 03", "episode": "3", "release_date": "2026-09-19T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        self.assertEqual(select_show_episodes("Gap Show", rows, date(2026, 9, 1)), [])


if __name__ == "__main__":
    unittest.main()
