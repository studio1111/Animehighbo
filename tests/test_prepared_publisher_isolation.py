import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class PreparedPublisherIsolationTests(unittest.TestCase):
    def _write_bundle(self, root, name, invalid=False):
        bundle = root / f"episode-{name}"
        bundle.mkdir()
        files = []
        for quality in ("1080p", "720p", "480p"):
            path = bundle / f"{quality}.mkv"
            path.write_bytes((f"fake-media-{name}-{quality}".encode("utf-8") * 300))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if invalid and quality == "1080p":
                digest = "0" * 64
            files.append({
                "quality": quality,
                "filename": path.name,
                "sha256": digest,
                "output_identity": f"{name}:{quality}",
            })
        manifest = {
            "identity": name,
            "group_id": f"group-{name}",
            "poster_identity": f"poster-{name}",
            "title": f"Series {name}",
            "episode": "1",
            "release_date": "2026-10-20",
            "files": files,
        }
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

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
            }), patch.object(app, "telegram_send_anime_poster", return_value="poster-message") as poster, patch.object(
                app, "telegram_upload_and_send", side_effect=["1080-message", "720-message", "480-message"]
            ) as upload, patch.object(app, "anime_catalog_metadata", return_value={}):
                result = app.publish_prepared_episodes(config, state, False, "token", "chat")

            self.assertEqual(result, 3)
            poster.assert_called_once()
            self.assertIn("good", str(poster.call_args))
            self.assertEqual(upload.call_count, 3)
            self.assertIn("good", state["published"])
            self.assertNotIn("bad", state["published"])


if __name__ == "__main__":
    unittest.main()
