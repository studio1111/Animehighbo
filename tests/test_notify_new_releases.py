import unittest
from unittest.mock import Mock

from tools.notify_new_releases import format_messages, new_releases, notify, release_key


class NewReleaseNotificationTests(unittest.TestCase):
    def test_release_key_uses_title_and_episode(self):
        self.assertEqual(release_key({"title": "Blue Box", "episode": 3}), "blue box::episode-3")

    def test_only_new_episode_keys_are_returned(self):
        previous = {"releases": [{"title": "Blue Box", "episode": 2}]}
        current = {"releases": [
            {"title": "Blue Box", "episode": 2, "release_date": "2026-10-24"},
            {"title": "Blue Box", "episode": 3, "release_date": "2026-10-25"},
            {"title": "New Show", "episode": 1, "release_date": "2026-10-25"},
        ]}
        rows = new_releases(previous, current)
        self.assertEqual([(row["title"], row["episode"]) for row in rows], [("Blue Box", 3), ("New Show", 1)])

    def test_duplicates_in_current_report_are_deduplicated(self):
        rows = new_releases({}, {"releases": [
            {"title": "Blue Box", "episode": 2},
            {"title": "Blue Box", "episode": 2},
        ]})
        self.assertEqual(len(rows), 1)

    def test_notification_requires_separate_alert_destination(self):
        with self.assertRaisesRegex(RuntimeError, "TELEGRAM_ALERT_CHAT_ID"):
            notify([{"title": "Blue Box", "episode": 3}], "token", "")

    def test_notification_sends_to_alert_destination(self):
        response = Mock()
        response.json.return_value = {"ok": True}
        post = Mock(return_value=response)
        sent = notify([{"title": "Blue Box", "episode": 3}], "token", "-100123", post=post)
        self.assertEqual(sent, 1)
        self.assertEqual(post.call_args.kwargs["json"]["chat_id"], "-100123")

    def test_message_discloses_metadata_only(self):
        message = format_messages([{"title": "Blue Box", "episode": 3, "release_date": "2026-10-25"}])[0]
        self.assertIn("Blue Box", message)
        self.assertIn("Metadata notification only", message)


if __name__ == "__main__":
    unittest.main()
