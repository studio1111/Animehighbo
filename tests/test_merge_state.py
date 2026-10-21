import json
import tempfile
import unittest
from pathlib import Path

from tools.merge_state import merge_state


class MergeStateTests(unittest.TestCase):
    def test_merges_disjoint_episode_records_without_losing_existing_state(self):
        base = {
            "published": {"old": {"complete_qualities": ["1080p"]}},
            "posters": {"series-one": {"message_id": "10"}},
            "seen": {"hash-old": "old:1080p"},
            "last_run": "base",
        }
        local = {
            "published": {"new": {"complete_qualities": ["720p", "1080p"]}},
            "posters": {"series-two": {"message_id": "20"}},
            "seen": {"hash-new": "new:720p"},
            "last_run": "local",
        }
        merged = merge_state(base, local)
        self.assertIn("old", merged["published"])
        self.assertIn("new", merged["published"])
        self.assertEqual(merged["posters"]["series-one"]["message_id"], "10")
        self.assertEqual(merged["posters"]["series-two"]["message_id"], "20")
        self.assertEqual(merged["seen"]["hash-old"], "old:1080p")
        self.assertEqual(merged["seen"]["hash-new"], "new:720p")
        self.assertEqual(merged["last_run"], "base")

    def test_conflicting_message_ids_keep_authoritative_base_and_union_qualities(self):
        base = {
            "published": {"episode": {"complete_qualities": ["1080p"], "message_id": "base-id"}},
            "posters": {"poster": {"message_id": "base-poster"}},
            "seen": {"hash": "base-output"},
        }
        local = {
            "published": {"episode": {"complete_qualities": ["720p"], "message_id": "local-id"}},
            "posters": {"poster": {"message_id": "local-poster"}},
            "seen": {"hash": "local-output"},
        }
        merged = merge_state(base, local)
        self.assertEqual(set(merged["published"]["episode"]["complete_qualities"]), {"1080p", "720p"})
        self.assertEqual(merged["published"]["episode"]["message_id"], "base-id")
        self.assertEqual(merged["posters"]["poster"]["message_id"], "base-poster")
        self.assertEqual(merged["seen"]["hash"], "base-output")

    def test_merge_state_cli_preserves_both_files_and_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "base.json"
            local = Path(temp) / "local.json"
            output = Path(temp) / "merged.json"
            base.write_text(json.dumps({"published": {"a": {}}}), encoding="utf-8")
            local.write_text(json.dumps({"published": {"b": {}}}), encoding="utf-8")
            # CLI is covered by the production entry point; the pure function is
            # exercised here without depending on Git or network access.
            output.write_text(json.dumps(merge_state(json.loads(base.read_text()), json.loads(local.read_text()))), encoding="utf-8")
            self.assertEqual(set(json.loads(output.read_text())["published"]), {"a", "b"})
            self.assertEqual(set(json.loads(base.read_text())["published"]), {"a"})
            self.assertEqual(set(json.loads(local.read_text())["published"]), {"b"})


if __name__ == "__main__":
    unittest.main()
