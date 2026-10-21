import hashlib
import unittest

from tools.episode_queue import select_oldest_upload_day


def row(title, episode, day, uri=None):
    return {
        "title": title,
        "episode": episode,
        "release_date": day,
        "uri": uri or f"magnet:?xt=urn:btih:{title.replace(' ', '')}{episode}",
        "authorized_for_redistribution": True,
    }


class EpisodeQueueTests(unittest.TestCase):
    def test_selects_all_episodes_from_oldest_upload_day_before_later_days(self):
        manifest = [
            row("Series A - 02", 2, "2026-10-12"),
            row("Series B - 01", 1, "2026-10-05"),
            row("Series A - 01", 1, "2026-10-05"),
            row("Series C - 01", 1, "2026-10-06"),
            row("Series B - 02", 2, "2026-10-12"),
        ]

        day, selected = select_oldest_upload_day(manifest, {"published": {}})

        self.assertEqual(day, "2026-10-05")
        self.assertEqual(
            [item["title"] for item in selected],
            ["Series A - 01", "Series B - 01"],
        )
        self.assertTrue(all(item["release_date"] == day for item in selected))

    def test_skips_episodes_already_complete_without_skipping_other_items_same_day(self):
        finished = row("Series A - 01", 1, "2026-10-05")
        finished_id = hashlib.sha256(("archive:" + finished["uri"]).encode()).hexdigest()
        manifest = [
            finished,
            row("Series B - 01", 1, "2026-10-05"),
            row("Series C - 01", 1, "2026-10-06"),
        ]
        state = {"published": {finished_id: {"complete_qualities": ["1080p", "720p", "480p"]}}}

        day, selected = select_oldest_upload_day(manifest, state)

        self.assertEqual(day, "2026-10-05")
        self.assertEqual([item["title"] for item in selected], ["Series B - 01"])

    def test_returns_empty_when_every_episode_is_complete(self):
        item = row("Series A - 01", 1, "2026-10-05")
        identity = hashlib.sha256(("archive:" + item["uri"]).encode()).hexdigest()

        day, selected = select_oldest_upload_day(
            [item],
            {"published": {identity: {"complete_qualities": ["1080p", "720p", "480p"]}}},
        )

        self.assertEqual(day, "")
        self.assertEqual(selected, [])

    def test_fails_closed_when_upload_date_is_missing(self):
        with self.assertRaisesRegex(ValueError, "release_date"):
            select_oldest_upload_day([{"title": "Series - 01", "uri": "magnet:?xt=urn:btih:abc"}], {})


if __name__ == "__main__":
    unittest.main()
