import unittest

from tools.episode_queue import eligible_prepared_episodes, episode_identity, series_identity


def episode(title, number, identity, season=1, status="prepared"):
    return {
        "title": title,
        "episode": number,
        "season": season,
        "series_title": title.split(" - ")[0],
        "identity": identity,
        "status": status,
    }


class IndependentEpisodeQueueTests(unittest.TestCase):
    def test_episode_two_waits_until_episode_one_is_fully_published(self):
        first = episode("A - 01", 1, "a1")
        second = episode("A - 02", 2, "a2")
        self.assertEqual(eligible_prepared_episodes([first, second], {"published": {}}), [first])

    def test_failed_anime_does_not_block_another_anime(self):
        a1 = episode("A - 01", 1, "a1", status="failed")
        b1 = episode("B - 01", 1, "b1")
        self.assertEqual(eligible_prepared_episodes([a1, b1], {"published": {}}), [b1])

    def test_episode_two_becomes_eligible_after_episode_one_has_all_qualities(self):
        first = episode("A - 01", 1, "a1")
        second = episode("A - 02", 2, "a2")
        state = {"published": {"a1": {"complete_qualities": ["1080p", "720p", "480p"]}}}
        self.assertEqual(eligible_prepared_episodes([first, second], state), [second])

    def test_season_is_part_of_series_identity(self):
        s1 = episode("A - 01", 1, "a-s1", season=1)
        s2 = episode("A - 01", 1, "a-s2", season=2)
        self.assertNotEqual(series_identity(s1), series_identity(s2))

    def test_missing_series_or_episode_identity_is_held_not_guessed(self):
        ambiguous = {"title": "Unparsed title", "identity": "unknown", "status": "prepared"}
        self.assertIsNone(series_identity(ambiguous))
        self.assertEqual(eligible_prepared_episodes([ambiguous], {"published": {}}), [])

    def test_identity_is_stable_for_same_source_uri(self):
        a = {"uri": "https://media.example/episode-1.mkv", "episode": 1, "series_title": "A", "season": 1}
        b = dict(a)
        self.assertEqual(episode_identity(a), episode_identity(b))


if __name__ == "__main__":
    unittest.main()
