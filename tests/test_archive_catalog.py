import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from tools.archive_catalog import REQUIRED_SHOW_SLUGS, _show_page_url, _show_title_from_html, build_archive_manifest, merge_existing_manifest, select_show_episodes


class ArchiveCatalogSelectionTests(unittest.TestCase):
    def test_required_long_running_shows_are_always_checked(self):
        self.assertIn("one-piece", REQUIRED_SHOW_SLUGS)
        self.assertIn("detective-conan", REQUIRED_SHOW_SLUGS)
        self.assertIn("kusuriya-no-hitorigoto", REQUIRED_SHOW_SLUGS)
        self.assertIn("tensei-shitara-ken-deshita-s2", REQUIRED_SHOW_SLUGS)
        self.assertIn("tokyo-revengers", REQUIRED_SHOW_SLUGS)

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
        self.assertTrue(all("uri" not in row for row in selected))
        self.assertTrue(all("magnet" not in json.dumps(row).lower() for row in selected))

    def test_preserves_curated_authorized_entries_missing_from_catalog_api(self):
        generated = [{
            "title": "New Saga - 01 (1080p)",
            "episode": 1,
            "quality": "1080p",
            "uri": "magnet:?xt=urn:btih:new",
            "release_date": "2026-10-10",
            "authorized_for_redistribution": True,
            "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
            "authorization_reviewed_at": "2026-10-24",
        }]
        existing = [
            {
                "title": "Curated Saga - 01 (1080p)",
                "episode": 1,
                "quality": "1080p",
                "uri": "magnet:?xt=urn:btih:curated",
                "release_date": "2026-10-07",
                "authorized_for_redistribution": True,
                "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                "authorization_reviewed_at": "2026-10-24",
            },
            {
                "title": "Old Saga - 01 (1080p)",
                "episode": 1,
                "quality": "1080p",
                "uri": "magnet:?xt=urn:btih:old",
                "release_date": "2026-08-07",
                "authorized_for_redistribution": True,
                "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                "authorization_reviewed_at": "2026-10-24",
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
            {"title": f"Long Runner - {number}", "episode": str(number),
             "release_date": f"2026-08-{24 + number - 97:02d}T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": f"magnet:?xt=urn:btih:old{number}"}]}
            for number in (97, 98, 99, 100)
        ] + [
            {"title": "Long Runner - 101", "episode": "101", "release_date": "2026-10-04T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:bbbb"}]},
            {"title": "Long Runner - 102", "episode": "102", "release_date": "2026-10-11T10:00:00Z",
             "downloads": [{"res": "720", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        selected = select_show_episodes("Long Runner", rows, date(2026, 10, 1))
        self.assertEqual([row["episode"] for row in selected], [101, 102])
        self.assertEqual([row["release_date"] for row in selected], ["2026-10-04", "2026-10-11"])

    def test_backfills_one_to_three_pre_cutoff_episodes(self):
        rows = [
            {"title": f"Short Season - {number:02d}", "episode": str(number),
             "release_date": f"2026-09-{20 + number:02d}T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": f"magnet:?xt=urn:btih:old{number}"}]}
            for number in (1, 2, 3)
        ] + [
            {"title": "Short Season - 04", "episode": "4", "release_date": "2026-10-02T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:new4"}]}
        ]
        selected = select_show_episodes("Short Season", rows, date(2026, 10, 1))
        self.assertEqual([row["episode"] for row in selected], [1, 2, 3, 4])

    def test_does_not_backfill_pre_cutoff_episodes_when_more_than_three_exist(self):
        rows = [
            {"title": f"Long Season - {number:02d}", "episode": str(number),
             "release_date": f"2026-09-{10 + number:02d}T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": f"magnet:?xt=urn:btih:old{number}"}]}
            for number in (1, 2, 3, 4)
        ] + [
            {"title": "Long Season - 05", "episode": "5", "release_date": "2026-10-02T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:new5"}]}
        ]
        selected = select_show_episodes("Long Season", rows, date(2026, 10, 1))
        self.assertEqual([row["episode"] for row in selected], [5])

    def test_season_started_before_cutoff_includes_its_single_prior_episode(self):
        rows = [
            {"title": "Current Season - 01", "episode": "1", "release_date": "2026-09-28T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:aaaa"}]},
            {"title": "Current Season - 02", "episode": "2", "release_date": "2026-10-05T10:00:00Z",
             "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:bbbb"}]},
            {"title": "Current Season - 03", "episode": "3", "release_date": "2026-10-12T10:00:00Z",
             "downloads": [{"res": "720", "magnet": "magnet:?xt=urn:btih:cccc"}]},
        ]
        selected = select_show_episodes("Current Season", rows, date(2026, 10, 1))
        self.assertEqual([row["episode"] for row in selected], [1, 2, 3])

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


    def test_oversized_1080p_source_falls_back_to_720p_within_limit(self):
        rows = [{
            "title": "Sasaki to Pii-chan S2 - 01",
            "episode": "1",
            "release_date": "2026-10-07T10:00:00Z",
            "downloads": [
                {"res": "1080", "magnet": "magnet:?xt=urn:btih:large1080&xl=2887386822"},
                {"res": "720", "magnet": "magnet:?xt=urn:btih:small720&xl=1200000000"},
            ],
        }]
        selected = select_show_episodes("Sasaki to Pii-chan S2", rows, date(2026, 10, 1), 1800)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["quality"], "720p")
        self.assertNotIn("uri", selected[0])

    def test_oversized_sources_are_skipped_when_no_lower_quality_fits(self):
        rows = [{
            "title": "Huge Saga - 01",
            "episode": "1",
            "release_date": "2026-10-07T10:00:00Z",
            "downloads": [
                {"res": "1080", "magnet": "magnet:?xt=urn:btih:large1080&xl=2887386822"},
                {"res": "720", "magnet": "magnet:?xt=urn:btih:large720&xl=2200000000"},
            ],
        }]
        self.assertEqual(select_show_episodes("Huge Saga", rows, date(2026, 10, 1), 1800), [])

    def test_generated_lower_quality_replaces_stale_oversized_source_for_same_episode(self):
        generated = [{
            "title": "Sasaki to Pii-chan S2 - 01 (720p)",
            "episode": 1,
            "quality": "720p",
            "uri": "magnet:?xt=urn:btih:small720",
            "release_date": "2026-10-07",
            "authorized_for_redistribution": True,
            "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
            "authorization_reviewed_at": "2026-10-24",
        }]
        existing = [{
            "title": "Sasaki to Pii-chan S2 - 01 (1080p)",
            "episode": 1,
            "quality": "1080p",
            "uri": "magnet:?xt=urn:btih:large1080",
            "release_date": "2026-10-07",
            "authorized_for_redistribution": True,
            "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
            "authorization_reviewed_at": "2026-10-24",
        }]
        merged = merge_existing_manifest(generated, existing, date(2026, 10, 1))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["quality"], "720p")


    def test_authorized_catalog_entry_requires_evidence_and_review_date(self):
        rows = [{
            "title": "Licensed Candidate - 01",
            "episode": "1",
            "release_date": "2026-10-07T10:00:00Z",
            "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:authorizedcandidate"}],
        }]
        with self.assertRaisesRegex(ValueError, "authorization_evidence"):
            select_show_episodes(
                "Licensed Candidate", rows, date(2026, 10, 1),
                authorized_for_redistribution=True,
            )

        selected = select_show_episodes(
            "Licensed Candidate", rows, date(2026, 10, 1),
            authorized_for_redistribution=True,
            authorization_evidence="https://rights.example.org/licenses/animehighbo",
            authorization_reviewed_at="2026-10-24",
        )
        self.assertTrue(selected[0]["authorized_for_redistribution"])
        self.assertEqual(selected[0]["authorization_reviewed_at"], "2026-10-24")
        self.assertTrue(selected[0]["authorization_evidence"])
        self.assertIn("authorizedcandidate", selected[0]["uri"])

    def test_builder_returns_metadata_only_candidates_without_source_uris(self):
        session = Mock()
        session.headers = {}
        latest_page = {
            "one": {"release_date": "2026-10-07T10:00:00Z", "page": "candidate-saga"}
        }
        episode_rows = [{
            "title": "Candidate Saga - 01",
            "episode": "1",
            "release_date": "2026-10-07T10:00:00Z",
            "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:secret"}],
        }]
        with patch("tools.archive_catalog._request_json", side_effect=[latest_page, {}]), \\
             patch("tools.archive_catalog._fetch_show_episodes", return_value=("Candidate Saga", episode_rows)), \\
             patch("tools.archive_catalog.time.sleep"):
            candidates = build_archive_manifest(
                date(2026, 10, 1), max_pages=5, session=session
            )
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["authorized_for_redistribution"])
        self.assertTrue(candidates[0]["review_required"])
        self.assertNotIn("uri", candidates[0])
        self.assertNotIn("magnet", json.dumps(candidates).lower())

    def test_discovered_episodes_are_unapproved_candidates_by_default(self):
        rows = [{
            "title": "Candidate Saga - 01",
            "episode": "1",
            "release_date": "2026-10-07T10:00:00Z",
            "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:candidate"}],
        }]

        selected = select_show_episodes("Candidate Saga", rows, date(2026, 10, 1))

        self.assertEqual(len(selected), 1)
        self.assertIs(selected[0].get("authorized_for_redistribution"), False)
        self.assertTrue(selected[0].get("review_required"))

if __name__ == "__main__":
    unittest.main()
