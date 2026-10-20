import unittest
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
