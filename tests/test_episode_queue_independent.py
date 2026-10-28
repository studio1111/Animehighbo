import unittest
from datetime import date

from tools.episode_queue import eligible_prepared_episodes, episode_identity, series_identity, limit_to_available_slots, empty_queue_reason


def episode(title, number, identity, season=1, status="prepared"):
    return {
        "title": title,
        "episode": number,
        "season": season,
        "series_title": title.split(" - ")[0],
        "identity": identity,
        "status": status,
        "authorized_for_redistribution": True,
        "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
        "authorization_reviewed_at": date.today().isoformat(),
    }



class EmptyQueueReasonTests(unittest.TestCase):
    def test_reports_active_episode_as_reason_when_all_eligible_work_is_running(self):
        self.assertEqual(
            empty_queue_reason(eligible_count=1, active_count=1, max_workers=5),
            "All currently eligible episodes are already queued or running.",
        )

    def test_reports_series_order_or_completion_when_no_episode_is_eligible(self):
        self.assertEqual(
            empty_queue_reason(eligible_count=0, active_count=1, max_workers=5),
            "No incomplete episode is eligible after completion, series-order, and retry-backoff checks.",
        )

    def test_reports_quota_cooldown_before_other_empty_queue_reasons(self):
        self.assertEqual(
            empty_queue_reason(
                eligible_count=0,
                active_count=0,
                max_workers=5,
                cooldown_until="2026-10-26T00:00:00Z",
            ),
            "Automatic dispatch is paused by the recorded translation quota cooldown until 2026-10-26T00:00:00Z.",
        )

    def test_reports_full_worker_capacity(self):
        self.assertEqual(
            empty_queue_reason(eligible_count=2, active_count=5, max_workers=5),
            "Global worker capacity is full (5/5); the scheduled dispatcher will retry.",
        )


class WorkerSlotLimitTests(unittest.TestCase):
    def test_only_fills_remaining_global_worker_slots(self):
        entries = [episode(f"A{i} - 01", 1, f"a{i}") for i in range(1, 6)]
        selected = limit_to_available_slots(entries, {"running-1", "running-2", "running-3"})
        self.assertEqual([row["identity"] for row in selected], ["a1", "a2"])

    def test_does_not_dispatch_more_when_five_workers_are_already_active(self):
        entries = [episode("A - 01", 1, "a1")]
        self.assertEqual(
            limit_to_available_slots(entries, {"one", "two", "three", "four", "five"}),
            [],
        )

    def test_does_not_redispatch_an_active_identity(self):
        entries = [episode("A - 01", 1, "a1"), episode("B - 01", 1, "b1")]
        selected = limit_to_available_slots(entries, {"a1"})
        self.assertEqual([row["identity"] for row in selected], ["b1"])


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
