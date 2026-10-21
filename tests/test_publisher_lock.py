import base64
import importlib
import importlib.util
import json
import unittest
from unittest.mock import Mock, patch


def load_publisher_lock(testcase):
    spec = importlib.util.find_spec("tools.publisher_lock")
    testcase.assertIsNotNone(spec, "tools.publisher_lock must implement the global publisher lock")
    return importlib.import_module("tools.publisher_lock")


def github_lock_response(identity, sha="lock-sha"):
    encoded = base64.b64encode(
        json.dumps({"identity": identity, "run_id": 123}).encode("utf-8")
    ).decode("ascii")
    response = Mock(status_code=200)
    response.json.return_value = {"sha": sha, "content": encoded}
    return response


class PublisherLockTests(unittest.TestCase):
    def test_acquire_creates_lock_when_no_episode_owns_it(self):
        lock = load_publisher_lock(self)
        missing = Mock(status_code=404)
        created = Mock(status_code=201)
        created.json.return_value = {"content": {"sha": "created-sha"}}

        with patch.object(lock.requests, "get", return_value=missing), patch.object(
            lock.requests, "put", return_value=created
        ) as put:
            result = lock.acquire_lock(
                "episode-a", token="secret-token", repository="owner/repo",
                timeout_seconds=10, poll_seconds=1,
            )

        self.assertEqual(result["identity"], "episode-a")
        self.assertEqual(result["sha"], "created-sha")
        payload = put.call_args.kwargs["json"]
        stored = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
        self.assertEqual(stored["identity"], "episode-a")
        self.assertNotIn("secret-token", json.dumps(payload))

    def test_acquire_waits_for_another_episode_then_claims_released_lock(self):
        lock = load_publisher_lock(self)
        held = github_lock_response("episode-b", "other-sha")
        missing = Mock(status_code=404)
        created = Mock(status_code=201)
        created.json.return_value = {"content": {"sha": "created-sha"}}

        with patch.object(lock.requests, "get", side_effect=[held, missing]), patch.object(
            lock.requests, "put", return_value=created
        ), patch.object(lock.time, "monotonic", side_effect=[0.0, 1.0]), patch.object(
            lock.time, "sleep"
        ) as sleep:
            result = lock.acquire_lock(
                "episode-a", token="token", repository="owner/repo",
                timeout_seconds=10, poll_seconds=2,
            )

        self.assertEqual(result["identity"], "episode-a")
        sleep.assert_called_once_with(2)

    def test_retry_of_same_episode_reuses_its_existing_lock(self):
        lock = load_publisher_lock(self)
        current = github_lock_response("episode-a", "same-sha")

        with patch.object(lock.requests, "get", return_value=current), patch.object(
            lock.requests, "put"
        ) as put:
            result = lock.acquire_lock(
                "episode-a", token="token", repository="owner/repo",
                timeout_seconds=10, poll_seconds=1,
            )

        self.assertEqual(result["sha"], "same-sha")
        put.assert_not_called()

    def test_release_deletes_only_the_owned_lock(self):
        lock = load_publisher_lock(self)
        current = github_lock_response("episode-a", "owned-sha")
        deleted = Mock(status_code=200)

        with patch.object(lock.requests, "get", return_value=current), patch.object(
            lock.requests, "delete", return_value=deleted
        ) as delete:
            released = lock.release_lock(
                "episode-a", token="token", repository="owner/repo"
            )

        self.assertTrue(released)
        self.assertEqual(delete.call_args.kwargs["json"]["sha"], "owned-sha")

    def test_release_refuses_to_delete_another_episode_lock(self):
        lock = load_publisher_lock(self)
        current = github_lock_response("episode-b", "other-sha")

        with patch.object(lock.requests, "get", return_value=current), patch.object(
            lock.requests, "delete"
        ) as delete:
            with self.assertRaisesRegex(RuntimeError, "owned by episode-b"):
                lock.release_lock(
                    "episode-a", token="token", repository="owner/repo"
                )

        delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
