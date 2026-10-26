import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class PreparedPublisherIsolationTests(unittest.TestCase):
    def _write_bundle(self, root, name, invalid=False, traversal=False):
        bundle = root / f"episode-{name}"
        bundle.mkdir()
        files = []
        for quality in ("1080p", "720p", "480p"):
            path = bundle / f"{quality}.mkv"
            if traversal and quality == "1080p":
                path = root / "outside.mkv"
                filename = "../outside.mkv"
            else:
                filename = path.name
            path.write_bytes((f"fake-media-{name}-{quality}".encode("utf-8") * 300))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if invalid and quality == "1080p":
                digest = "0" * 64
            files.append({
                "quality": quality,
                "filename": filename,
                "sha256": digest,
                "output_identity": f"{name}:{quality}",
            })
        manifest = {
            "schema_version": 1,
            "identity": name,
            "series_key": f"series {name}::season-1",
            "group_id": f"group-{name}",
            "poster_identity": f"poster-{name}",
            "title": f"Series {name}",
            "episode": "1",
            "release_date": "2026-10-20",
            "authorized_for_redistribution": True,
            "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
            "authorization_reviewed_at": "2026-10-24",
            "files": files,
        }
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_manifest_path_traversal_is_rejected_without_blocking_valid_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_bundle(root, "escape", traversal=True)
            self._write_bundle(root, "good")
            config = {
                "max_file_size_mb": 10,
                "state_file": str(root / "state.json"),
                "bilingual_subtitles": True,
            }
            state = {"published": {}, "posters": {}, "seen": {}}

            with patch.dict(os.environ, {
                "ANIMEHIGHBO_PREPARED_DIR": str(root),
                "ANIMEHIGHBO_RELEASE_DATE": "",
            }), patch.object(app, "create_anime_video_thumbnail", return_value=root / "thumbnail.jpg"), patch.object(app, "telegram_send_anime_poster", return_value="poster-message") as poster, patch.object(
                app, "telegram_upload_and_send",
                side_effect=["1080-message", "720-message", "480-message"],
            ) as upload, patch.object(app, "anime_catalog_metadata", return_value={}):
                result = app.publish_prepared_episodes(config, state, False, "token", "chat")

            self.assertEqual(result, 1)  # Valid episode posts, but invalid bundle makes the run fail.
            poster.assert_called_once_with("token", "chat", "Series good", "1", {})
            self.assertIn("good", state["published"])
            self.assertNotIn("escape", state["published"])
            self.assertEqual(upload.call_count, 3)

    def test_telegram_failure_for_one_episode_does_not_block_another(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_bundle(root, "bad")
            self._write_bundle(root, "good")
            state_path = root / "state.json"
            config = {
                "max_file_size_mb": 10,
                "state_file": str(state_path),
                "bilingual_subtitles": True,
            }
            state = {"published": {}, "posters": {}, "seen": {}}

            with patch.dict(os.environ, {
                "ANIMEHIGHBO_PREPARED_DIR": str(root),
                "ANIMEHIGHBO_RELEASE_DATE": "",
                "ANIMEHIGHBO_PUBLISH_EVENTS_FILE": str(root / "publish-events.json"),
            }), patch.object(app, "create_anime_video_thumbnail", return_value=root / "thumbnail.jpg"), patch.object(
                app, "telegram_send_anime_poster", return_value="poster-message"
            ) as poster, patch.object(
                app, "telegram_upload_and_send",
                side_effect=[RuntimeError("temporary Telegram error"), "1080-message", "720-message", "480-message"],
            ) as upload, patch.object(app, "anime_catalog_metadata", return_value={}):
                result = app.publish_prepared_episodes(config, state, False, "token", "chat")

            # Successful unrelated delivery is retained, but the run must be red
            # so the failed episode is visible and eligible for retry.
            self.assertEqual(result, 1)
            self.assertEqual(poster.call_count, 2)
            self.assertEqual(upload.call_count, 4)
            self.assertIn("good", state["published"])
            self.assertNotIn("bad", state["published"])

    def test_invalid_bundle_does_not_block_a_different_valid_episode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_bundle(root, "bad", invalid=True)
            self._write_bundle(root, "good")
            state_path = root / "state.json"
            config = {
                "max_file_size_mb": 10,
                "state_file": str(state_path),
                "bilingual_subtitles": True,
            }
            state = {"published": {}, "posters": {}, "seen": {}}

            with patch.dict(os.environ, {
                "ANIMEHIGHBO_PREPARED_DIR": str(root),
                "ANIMEHIGHBO_RELEASE_DATE": "",
                "ANIMEHIGHBO_PUBLISH_EVENTS_FILE": str(root / "publish-events.json"),
            }), patch.object(app, "create_anime_video_thumbnail", return_value=root / "thumbnail.jpg"), patch.object(app, "telegram_send_anime_poster", return_value="poster-message") as poster, patch.object(
                app, "telegram_upload_and_send", side_effect=["1080-message", "720-message", "480-message"]
            ) as upload, patch.object(app, "anime_catalog_metadata", return_value={}):
                result = app.publish_prepared_episodes(config, state, False, "token", "chat")

            self.assertEqual(result, 1)  # Valid episode posts, but invalid bundle makes the run fail.
            poster.assert_called_once_with("token", "chat", "Series good", "1", {})
            self.assertEqual(upload.call_count, 3)
            self.assertIn("good", state["published"])
            self.assertNotIn("bad", state["published"])
            events = json.loads((root / "publish-events.json").read_text(encoding="utf-8"))
            self.assertEqual(events, ["series good::season-1"])


if __name__ == "__main__":
    unittest.main()
