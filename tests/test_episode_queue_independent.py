import unittest

from tools.episode_queue import eligible_prepared_episodes, series_identity


def episode(title, number, uri, *, season=1, ready=True):
    return {
        "title": title,
        "series_title": title,
        "episode": number,
        "season": season,
        "uri": uri,
        "release_date": "2026-10-10",
        "prepared": ready,
    }


def published(*uris):
    import hashlib
    return {"published": {
        hashlib.sha256(("archive:" + uri).encode()).hexdigest(): {
            "complete_qualities": ["1080p", "720p", "480p"]
        }
        for uri in uris
    }}


class IndependentEpisodeQueueTests(unittest.TestCase):
    def test_later_episode_waits_for_prior_episode_in_same_series(self):
        rows = [
            episode("Show", 1, "https://cdn.example/show-1.mkv", ready=False),
            episode("Show", 2, "https://cdn.example/show-2.mkv"),
            episode("Other", 1, "https://cdn.example/other-1.mkv"),
        ]
        eligible = eligible_prepared_episodes(rows, {"published": {}})
        self.assertEqual([row["uri"] for row in eligible], ["https://cdn.example/other-1.mkv"])

    def test_other_series_remains_eligible_when_first_series_is_blocked(self):
        rows = [
            episode("A", 1, "https://cdn.example/a-1.mkv", ready=False),
            episode("A", 2, "https://cdn.example/a-2.mkv"),
            episode("B", 1, "https://cdn.example/b-1.mkv"),
        ]
        eligible = eligible_prepared_episodes(rows, {"published": {}})
        self.assertEqual([row["uri"] for row in eligible], ["https://cdn.example/b-1.mkv"])

    def test_next_episode_becomes_eligible_after_predecessor_is_published(self):
        first = "https://cdn.example/a-1.mkv"
        second = "https://cdn.example/a-2.mkv"
        rows = [episode("A", 1, first), episode("A", 2, second)]
        eligible = eligible_prepared_episodes(rows, published(first))
        self.assertEqual([row["uri"] for row in eligible], [second])

    def test_seasons_have_independent_episode_sequences(self):
        first = episode("Show", 1, "https://cdn.example/s1e1.mkv", season=1)
        second = episode("Show", 1, "https://cdn.example/s2e1.mkv", season=2)
        self.assertNotEqual(series_identity(first), series_identity(second))
        eligible = eligible_prepared_episodes([first, second], {"published": {}})
        self.assertEqual({row["uri"] for row in eligible}, {first["uri"], second["uri"]})

    def test_unprepared_episode_is_never_eligible(self):
        row = episode("Show", 1, "https://cdn.example/e1.mkv", ready=False)
        self.assertEqual(eligible_prepared_episodes([row], {"published": {}}), [])

    def test_duplicate_series_episode_identity_is_held(self):
        rows = [
            episode("Show", 1, "https://cdn.example/one.mkv"),
            episode("Show", 1, "https://cdn.example/duplicate.mkv"),
        ]
        self.assertEqual(eligible_prepared_episodes(rows, {"published": {}}), [])

    def test_missing_series_or_episode_identity_is_held(self):
        rows = [
            {"title": "unknown", "uri": "https://cdn.example/unknown.mkv", "prepared": True},
            episode("Known", 1, "https://cdn.example/known.mkv"),
        ]
        rows[0]["series_title"] = ""
        self.assertEqual(
            [row["uri"] for row in eligible_prepared_episodes(rows, {"published": {}})],
            ["https://cdn.example/known.mkv"],
        )


if __name__ == "__main__":
    unittest.main()
