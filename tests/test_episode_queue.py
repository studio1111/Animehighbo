import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone

from tools.episode_queue import active_quota_cooldown, select_next_upload_date, select_upload_date_group


def row(title, episode, day, uri=None):
    return {
        "title": title,
        "episode": episode,
        "release_date": day,
        "uri": uri or f"magnet:?xt=urn:btih:{title.replace(' ', '')}{episode}",
        "authorized_for_redistribution": True,
    }


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

    def test_series_started_before_cutoff_is_skipped_whole_without_blocking_others(self):
        manifest = [
            row("Summer show - 01", 1, "2026-09-27"),
            row("Summer show - 02", 2, "2026-10-04"),
            row("Fall show - 01", 1, "2026-10-03"),
        ]

        upload_date, selected = select_next_upload_date(
            manifest, {"published": {}}, cutoff_date="2026-10-01"
        )

        self.assertEqual(upload_date, "2026-10-03")
        self.assertEqual([item["title"] for item in selected], ["Fall show - 01"])

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


    def test_retained_incomplete_publisher_lock_is_prioritized_before_first_five(self):
        from tools.episode_queue import prioritize_locked_episode

        entries = [{"identity": f"episode-{index}"} for index in range(1, 8)]
        with tempfile.TemporaryDirectory() as directory:
            lock_path = f"{directory}/publisher.lock"
            with open(lock_path, "w", encoding="utf-8") as stream:
                json.dump({"identity": "episode-7", "run_id": "cancelled-run"}, stream)

            prioritized = prioritize_locked_episode(entries, {"published": {}}, lock_path)

        self.assertEqual(prioritized[0]["identity"], "episode-7")
        self.assertEqual({item["identity"] for item in prioritized[:5]},
                         {"episode-1", "episode-2", "episode-3", "episode-4", "episode-7"})

    def test_incomplete_lock_missing_from_eligible_queue_fails_closed(self):
        from tools.episode_queue import prioritize_locked_episode

        with tempfile.TemporaryDirectory() as directory:
            lock_path = f"{directory}/publisher.lock"
            with open(lock_path, "w", encoding="utf-8") as stream:
                json.dump({"identity": "missing-episode"}, stream)

            with self.assertRaisesRegex(RuntimeError, "not eligible"):
                prioritize_locked_episode([{"identity": "other-episode"}], {"published": {}}, lock_path)

    def test_completed_episode_lock_does_not_block_queue(self):
        from tools.episode_queue import prioritize_locked_episode

        entries = [{"identity": "next-episode"}]
        with tempfile.TemporaryDirectory() as directory:
            lock_path = f"{directory}/publisher.lock"
            with open(lock_path, "w", encoding="utf-8") as stream:
                json.dump({"identity": "finished-episode"}, stream)

            result = prioritize_locked_episode(entries, {
                "published": {
                    "finished-episode": {
                        "complete_qualities": ["1080p", "720p", "480p"]
                    }
                }
            }, lock_path)

        self.assertEqual(result, entries)


    def test_recent_failure_backoff_skips_only_the_failed_series(self):
        from tools.episode_queue import exclude_recently_failed_series

        entries = [
            {"identity": "failed-episode", "series_key": "series-a"},
            {"identity": "later-episode", "series_key": "series-a"},
            {"identity": "healthy-episode", "series_key": "series-b"},
        ]

        selected = exclude_recently_failed_series(entries, {"failed-episode"})

        self.assertEqual([entry["identity"] for entry in selected], ["healthy-episode"])

    def test_recent_failure_backoff_never_drops_global_lock_owner(self):
        from tools.episode_queue import exclude_recently_failed_series

        entries = [
            {"identity": "locked-episode", "series_key": "series-a"},
            {"identity": "later-episode", "series_key": "series-a"},
            {"identity": "healthy-episode", "series_key": "series-b"},
        ]

        selected = exclude_recently_failed_series(
            entries, {"locked-episode"}, locked_identity="locked-episode"
        )

        self.assertEqual(
            [entry["identity"] for entry in selected],
            ["locked-episode", "healthy-episode"],
        )

    def test_dispatch_group_contains_only_the_oldest_upload_date(self):
        entries = [
            {"identity": "old-a", "title": "Old A", "release_date": "2026-10-05"},
            {"identity": "old-b", "title": "Old B", "release_date": "2026-10-05"},
            {"identity": "new-c", "title": "New C", "release_date": "2026-10-06"},
        ]

        with tempfile.TemporaryDirectory() as directory:
            selected = select_upload_date_group(
                entries, "2026-10-05", {"published": {}}, f"{directory}/publisher.lock"
            )

        self.assertEqual([item["identity"] for item in selected], ["old-a", "old-b"])

    def test_incomplete_publisher_lock_overrides_date_group_and_runs_alone(self):
        entries = [
            {"identity": "older-date", "title": "Older date", "release_date": "2026-10-01"},
            {"identity": "locked-date", "title": "Interrupted episode", "release_date": "2026-10-03"},
            {"identity": "same-later-date", "title": "Other later episode", "release_date": "2026-10-03"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            lock_path = f"{directory}/publisher.lock"
            with open(lock_path, "w", encoding="utf-8") as stream:
                json.dump({"identity": "locked-date", "run_id": "cancelled-run"}, stream)

            selected = select_upload_date_group(entries, "2026-10-01", {"published": {}}, lock_path)

        self.assertEqual([item["identity"] for item in selected], ["locked-date"])

    def test_completed_publisher_lock_does_not_override_oldest_date_group(self):
        entries = [
            {"identity": "old-date", "title": "Old date", "release_date": "2026-10-01"},
            {"identity": "new-date", "title": "New date", "release_date": "2026-10-02"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            lock_path = f"{directory}/publisher.lock"
            with open(lock_path, "w", encoding="utf-8") as stream:
                json.dump({"identity": "finished"}, stream)

            selected = select_upload_date_group(entries, "2026-10-01", {
                "published": {
                    "finished": {"complete_qualities": ["1080p", "720p", "480p"]}
                }
            }, lock_path)

        self.assertEqual([item["identity"] for item in selected], ["old-date"])


if __name__ == "__main__":
    unittest.main()
