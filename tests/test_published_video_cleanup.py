import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.cleanup_published_videos import published_video_paths


class PublishedVideoCleanupTests(unittest.TestCase):
    def test_only_exact_video_hash_with_telegram_message_id_is_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            published_video = b"uploaded video bytes"
            unconfirmed_video = b"not uploaded"
            (root / "sent.mkv").write_bytes(published_video)
            (root / "unsent.mp4").write_bytes(unconfirmed_video)
            (root / "poster.jpg").write_bytes(published_video)
            state = {
                "published": {
                    "episode:1080p": {
                        "file_sha256": hashlib.sha256(published_video).hexdigest(),
                        "message_id": "123",
                        "published_at": 100,
                    },
                    "episode:720p": {
                        "file_sha256": hashlib.sha256(unconfirmed_video).hexdigest(),
                        "published_at": 100,
                    },
                }
            }
            result = published_video_paths(
                root, state, ["sent.mkv", "unsent.mp4", "poster.jpg"]
            )
            self.assertEqual(result, [Path("sent.mkv")])

    def test_malformed_publication_section_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                published_video_paths(Path(directory), {"published": []}, ["x.mkv"])

    def test_missing_files_and_path_traversal_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                published_video_paths(root, {"published": {}}, ["missing.mkv", "../outside.mkv"]),
                [],
            )


if __name__ == "__main__":
    unittest.main()
