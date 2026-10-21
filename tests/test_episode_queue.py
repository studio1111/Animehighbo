import hashlib
import unittest

from tools.episode_queue import select_next_episode_tier


def row(title, episode, day, uri=None):
    return {
        "title": title,
        "episode": episode,
        "release_date": day,
        "uri": uri or f"magnet:?xt=urn:btih:{title.replace(' ', '')}{episode}",
        "authorized_for_redistribution": True,
    }


class EpisodeQueueTests(unittest.TestCase):
    def test_selects_every_episode_one_across_dates_before_any_episode_two(self):
        manifest = [
            row("Series A - 02", 2, "2026-10-12"),
            row("Series B - 01", 1, "2026-10-05"),
            row("Series A - 01", 1, "2026-10-05"),
            row("Series C - 01", 1, "2026-10-06"),
            row("Series B - 02", 2, "2026-10-12"),
        ]

        tier, selected = select_next_episode_tier(manifest, {"published": {}})

        self.assertEqual(tier, 1)
        self.assertEqual(
            [item["title"] for item in selected],
            ["Series A - 01", "Series B - 01"],
        )
        self.assertTrue(all(item["episode"] == 1 for item in selected))

    def test_skips_completed_episode_ones_but_does_not_advance_to_episode_two_while_any_episode_one_remains(self):
        finished = row("Series A - 01", 1, "2026-10-05")
        finished_id = hashlib.sha256(("archive:" + finished["uri"]).encode()).hexdigest()
        manifest = [
            finished,
            row("Series B - 01", 1, "2026-10-05"),
            row("Series C - 01", 1, "2026-10-06"),
        ]
        state = {"published": {finished_id: {"complete_qualities": ["1080p", "720p", "480p"]}}}

        tier, selected = select_next_episode_tier(manifest, state)

        self.assertEqual(tier, 1)
        self.assertEqual([item["title"] for item in selected], ["Series B - 01"])

    def test_returns_empty_when_every_episode_is_complete(self):
        item = row("Series A - 01", 1, "2026-10-05")
        identity = hashlib.sha256(("archive:" + item["uri"]).encode()).hexdigest()

        tier, selected = select_next_episode_tier(
            [item],
            {"published": {identity: {"complete_qualities": ["1080p", "720p", "480p"]}}},
        )

        self.assertEqual(tier, None)
        self.assertEqual(selected, [])

    def test_episode_two_waits_until_all_episode_ones_are_complete(self):
        episode_one = row("Series A - 01", 1, "2026-10-10")
        episode_two = row("Series A - 02", 2, "2026-10-17")
        episode_one_b = row("Series B - 01", 1, "2026-10-18")
        state = {"published": {
            hashlib.sha256(("archive:" + episode_one["uri"]).encode()).hexdigest(): {
                "complete_qualities": ["1080p", "720p", "480p"]
            }
        }}

        tier, selected = select_next_episode_tier([episode_one, episode_two, episode_one_b], state)

        self.assertEqual(tier, 1)
        self.assertEqual([item["title"] for item in selected], ["Series B - 01"])

    def test_fails_closed_when_manifest_contains_pre_cutoff_episodes(self):
        with self.assertRaisesRegex(ValueError, "predates configured archive cutoff"):
            select_next_episode_tier(
                [row("Old Series - 01", 1, "2023-10-07")],
                {"published": {}},
                cutoff_date="2026-09-01",
            )

    def test_fails_closed_when_upload_date_is_missing(self):
        with self.assertRaisesRegex(ValueError, "release_date"):
            select_next_episode_tier([{"title": "Series - 01", "uri": "magnet:?xt=urn:btih:abc"}], {})


if __name__ == "__main__":
    unittest.main()
