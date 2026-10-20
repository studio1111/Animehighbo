import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.torrent_downloader import download_authorized_torrent, validate_torrent_source


class TorrentSourceValidationTests(unittest.TestCase):
    def test_accepts_magnet_with_info_hash(self):
        uri = "magnet:?xt=urn:btih:0123456789012345678901234567890123456789"
        self.assertEqual(validate_torrent_source(uri, []), uri)

    def test_accepts_allowlisted_https_torrent(self):
        uri = "https://torrents.example.org/legal-media.torrent"
        self.assertEqual(validate_torrent_source(uri, ["torrents.example.org"]), uri)

    def test_rejects_unallowlisted_torrent_or_web_page(self):
        for uri in (
            "http://torrents.example.org/legal-media.torrent",
            "https://evil.example/legal-media.torrent",
            "https://torrents.example.org/watch/123",
            "magnet:?dn=missing-info-hash",
        ):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                validate_torrent_source(uri, ["torrents.example.org"])

    def test_aria2_output_does_not_use_an_unread_pipe(self):
        uri = "magnet:?xt=urn:btih:0123456789012345678901234567890123456789"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            captured = {}

            def fake_popen(command, stdout=None, stderr=None):
                captured["stdout"] = stdout
                download_dir = Path(command[command.index("--dir") + 1])
                video = download_dir / "sample.mkv"
                video.write_bytes(bytes.fromhex("1A45DFA3") + b"x" * 2048)
                return SimpleNamespace(poll=lambda: 0, returncode=0)

            with patch("tools.torrent_downloader.shutil.which", return_value="/usr/bin/aria2c"), \
                 patch("tools.torrent_downloader.subprocess.Popen", side_effect=fake_popen), \
                 patch("tools.torrent_downloader.time.sleep"):
                result = download_authorized_torrent(
                    uri,
                    "Licensed Sample 01 1080p",
                    {"download_dir": str(root / "downloads"), "allowed_torrent_hosts": [],
                     "max_file_size_mb": 5, "torrent_timeout_seconds": 10},
                    authorized_for_redistribution=True,
                )

            self.assertNotEqual(captured["stdout"], __import__("subprocess").PIPE)
            self.assertTrue(hasattr(captured["stdout"], "write"))
            self.assertEqual(result.read_bytes()[:4], bytes.fromhex("1A45DFA3"))

    def test_requires_explicit_redistribution_authorization(self):
        with patch("tools.torrent_downloader.shutil.which") as which:
            with self.assertRaises(PermissionError):
                download_authorized_torrent(
                    "magnet:?xt=urn:btih:0123456789012345678901234567890123456789",
                    "Legal Episode 01 1080p",
                    {"download_dir": "downloads", "allowed_torrent_hosts": []},
                    authorized_for_redistribution=False,
                )
            which.assert_not_called()


if __name__ == "__main__":
    unittest.main()
