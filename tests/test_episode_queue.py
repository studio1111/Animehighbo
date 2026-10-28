import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timezone

from tools.episode_queue import active_quota_cooldown, filter_by_manual_exclusions, select_next_upload_date


def row(title, episode, day, uri=None):
    return {
        "title": title,
        "episode": episode,
        "release_date": day,
        "uri": uri or f"magnet:?xt=urn:btih:{title.replace(' ', '')}{episode}",
        "authorized_for_redistribution": True,
        "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
        "authorization_reviewed_at": date.today().isoformat(),
    }


class ManualExclusionTests(unittest.TestCase):
    def test_all_series_are_allowed_except_explicit_manual_exclusions(self):
        manifest = [
            {"title": "Approved Saga - 01 (1080p)", "episode": 1},
            {"title": "Pending Saga - 01 (1080p)", "episode": 1},
            {"title": "Denied Saga - 01 (1080p)", "episode": 1},
        ]
        allowed, counts = filter_by_manual_exclusions(manifest, ["Denied Saga"])
        self.assertEqual(
            [row["title"] for row in allowed],
            ["Approved Saga - 01 (1080p)", "Pending Saga - 01 (1080p)"],
        )
        self.assertEqual(counts, {"eligible": 2, "excluded": 1, "invalid": 0})

    def test_ambiguous_titles_are_allowed_by_default_and_can_be_denied_manually(self):
        manifest = [
            {"title": "No Episode Pattern"},
            {"title": "Not Approved - 01 (1080p)", "episode": 1},
        ]
        allowed, counts = filter_by_manual_exclusions(manifest, [])
        self.assertEqual([row["title"] for row in allowed], [
            "No Episode Pattern",
            "Not Approved - 01 (1080p)",
        ])
        self.assertEqual(counts, {"eligible": 2, "excluded": 0, "invalid": 0})

        denied, counts = filter_by_manual_exclusions(manifest, ["No Episode Pattern"])
        self.assertEqual([row["title"] for row in denied], ["Not Approved - 01 (1080p)"])
        self.assertEqual(counts["excluded"], 1)

    def test_ambiguous_release_title_gets_fallback_series_identity_for_queueing(self):
        manifest = [row("Unparsed Release Name", 1, "2026-10-05")]
        upload_date, selected = select_next_upload_date(manifest, {"published": {}})
        self.assertEqual(upload_date, "2026-10-05")
        self.assertEqual([item["title"] for item in selected], ["Unparsed Release Name"])

    def test_invalid_manual_exclusions_shape_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "JSON list of strings"):
            filter_by_manual_exclusions([{"title": "A - 01"}], ["A", 42])


class NoAutomaticAuthorizationGateTests(unittest.TestCase):
    def test_archive_episode_without_rights_metadata_is_still_queueable(self):
        episode = row("Queueable Saga - 01", 1, "2026-10-05")
        episode.pop("authorization_evidence", None)
        episode.pop("authorization_reviewed_at", None)
        episode["authorized_for_redistribution"] = False

        upload_date, selected = select_next_upload_date([episode], {"published": {}})

        self.assertEqual(upload_date, "2026-10-05")
        self.assertEqual([item["title"] for item in selected], ["Queueable Saga - 01"])

    def test_prepared_episode_without_rights_metadata_is_still_eligible(self):
        from tools.episode_queue import eligible_prepared_episodes

        episode = row("Queueable Saga - 01", 1, "2026-10-05")
        episode.pop("authorization_evidence", None)
        episode.pop("authorization_reviewed_at", None)
        episode["authorized_for_redistribution"] = False

        selected = eligible_prepared_episodes([episode], {"published": {}})

        self.assertEqual([item["title"] for item in selected], ["Queueable Saga - 01"])


class QuotaCooldownTests(unittest.TestCase):
    def test_future_cooldown_blocks_dispatch_until_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/cooldown.json"
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"cooldown_until": "2026-10-22T07:00:00Z"}, stream)
            now = datetime(2026, 10, 21, 13, 10, tzinfo=timezone.utc)
            self.assertEqual(active_quota_cooldown(path, now), "2026-10-22T07:00:00Z")

    def test_expired_cooldown_allows_dispatch_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/cooldown.json"
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"cooldown_until": "2026-10-22T07:00:00Z"}, stream)
            now = datetime(2026, 10, 22, 7, 1, tzinfo=timezone.utc)
            self.assertIsNone(active_quota_cooldown(path, now))


    def test_expired_cooldown_is_cleared_from_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/cooldown.json"
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"cooldown_until": "2026-10-22T07:00:00Z"}, stream)
            now = datetime(2026, 10, 22, 7, 1, tzinfo=timezone.utc)

            self.assertIsNone(active_quota_cooldown(path, now))
            self.assertFalse(__import__("pathlib").Path(path).exists())


class UploadDateQueueTests(unittest.TestCase):
    def test_oldest_upload_date_wins_only_after_episode_order_is_respected(self):
        manifest = [
            row("Delayed show - 01", 1, "2026-10-12"),
            row("Older show - 08", 8, "2026-10-05"),
            row("New show - 01", 1, "2026-10-10"),
            row("Older show - 09", 9, "2026-10-05"),
        ]

        upload_date, selected = select_next_upload_date(manifest, {"published": {}})

        self.assertEqual(upload_date, "2026-10-05")
        self.assertEqual(
            [item["title"] for item in selected],
            ["Older show - 08", "New show - 01", "Delayed show - 01"],
        )

    def test_all_independent_eligible_episodes_are_selected_across_upload_dates(self):
        manifest = [
            row("Series Z - 02", 2, "2026-10-05"),
            row("Series A - 07", 7, "2026-10-05"),
            row("Series B - 01", 1, "2026-10-06"),
        ]

        upload_date, selected = select_next_upload_date(manifest, {"published": {}})

        self.assertEqual(upload_date, "2026-10-05")
        self.assertEqual(
            [item["title"] for item in selected],
            ["Series A - 07", "Series Z - 02", "Series B - 01"],
        )

    def test_completed_oldest_entries_are_skipped_and_next_upload_date_is_selected(self):
        finished = row("Oldest show - 05", 5, "2026-10-05")
        finished_id = hashlib.sha256(("archive:" + finished["uri"]).encode()).hexdigest()
        manifest = [
            finished,
            row("Next date show - 01", 1, "2026-10-06"),
            row("Same next date show - 12", 12, "2026-10-06"),
        ]
        state = {"published": {
            finished_id: {"complete_qualities": ["1080p", "720p", "480p"]}
        }}

        upload_date, selected = select_next_upload_date(manifest, state)

        self.assertEqual(upload_date, "2026-10-06")
        self.assertEqual(
            [item["title"] for item in selected],
            ["Next date show - 01", "Same next date show - 12"],
        )

    def test_returns_empty_when_every_episode_is_complete(self):
        item = row("Series - 01", 1, "2026-10-05")
        identity = hashlib.sha256(("archive:" + item["uri"]).encode()).hexdigest()

        upload_date, selected = select_next_upload_date(
            [item],
            {"published": {identity: {"complete_qualities": ["1080p", "720p", "480p"]}}},
        )

        self.assertIsNone(upload_date)
        self.assertEqual(selected, [])

    def test_curated_pre_cutoff_backfill_is_not_blocked_by_queue_cutoff(self):
        manifest = [
            row("Summer show - 01", 1, "2026-09-27"),
            row("Summer show - 02", 2, "2026-10-04"),
            row("Fall show - 01", 1, "2026-10-03"),
        ]

        upload_date, selected = select_next_upload_date(
            manifest, {"published": {}}, cutoff_date="2026-10-01"
        )

        self.assertEqual(upload_date, "2026-09-27")
        self.assertEqual(
            {item["title"] for item in selected},
            {"Summer show - 01", "Fall show - 01"},
        )

        first_identity = hashlib.sha256(
            ("archive:" + manifest[0]["uri"]).encode()
        ).hexdigest()
        state = {"published": {
            first_identity: {"complete_qualities": ["1080p", "720p", "480p"]}
        }}
        upload_date, selected = select_next_upload_date(
            manifest, state, cutoff_date="2026-10-01"
        )
        self.assertEqual(upload_date, "2026-10-03")
        self.assertEqual(
            {item["title"] for item in selected},
            {"Summer show - 02", "Fall show - 01"},
        )

    def test_later_episode_cannot_jump_ahead_even_if_its_upload_date_is_older(self):
        manifest = [
            row("Series A - 02", 2, "2026-10-01"),
            row("Series A - 01", 1, "2026-10-10"),
            row("Series B - 01", 1, "2026-10-02"),
        ]

        upload_date, selected = select_next_upload_date(manifest, {"published": {}})

        self.assertEqual(upload_date, "2026-10-02")
        self.assertEqual(
            [item["title"] for item in selected],
            ["Series B - 01", "Series A - 01"],
        )

    def test_target_series_filter_only_queues_that_series_next_episode(self):
        manifest = [
            row("Series A - 01", 1, "2026-10-10"),
            row("Series A - 02", 2, "2026-10-11"),
            row("Series B - 01", 1, "2026-10-12"),
        ]

        upload_date, selected = select_next_upload_date(
            manifest, {"published": {}}, target_series="series a::season-1"
        )

        self.assertEqual(upload_date, "2026-10-10")
        self.assertEqual([item["title"] for item in selected], ["Series A - 01"])

    def test_episode_two_is_released_after_episode_one_is_complete(self):
        first = row("Series A - 01", 1, "2026-10-10")
        second = row("Series A - 02", 2, "2026-10-11")
        first_id = hashlib.sha256(("archive:" + first["uri"]).encode()).hexdigest()
        manifest = [first, second]
        state = {"published": {
            first_id: {"complete_qualities": ["1080p", "720p", "480p"]}
        }}

        upload_date, selected = select_next_upload_date(manifest, state)

        self.assertEqual(upload_date, "2026-10-11")
        self.assertEqual([item["title"] for item in selected], ["Series A - 02"])

    def test_fails_closed_when_upload_date_is_missing(self):
        with self.assertRaisesRegex(ValueError, "release_date"):
            select_next_upload_date(
                [{"title": "Series", "uri": "magnet:?xt=urn:btih:abc"}],
                {},
            )


    def test_recent_failure_backoff_skips_only_the_failed_series(self):
        from tools.episode_queue import exclude_recently_failed_series

        entries = [
            {"identity": "failed-episode", "series_key": "series-a"},
            {"identity": "later-episode", "series_key": "series-a"},
            {"identity": "healthy-episode", "series_key": "series-b"},
        ]

        selected = exclude_recently_failed_series(entries, {"failed-episode"})

        self.assertEqual([entry["identity"] for entry in selected], ["healthy-episode"])


if __name__ == "__main__":
    unittest.main()
