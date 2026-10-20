import json
import tempfile
import unittest
from pathlib import Path

from tools.sync_rss_input import parse_rss_input, sync_rss_input


class RSSInputTests(unittest.TestCase):
    def test_parses_https_urls_ignores_comments_and_deduplicates(self):
        feeds = parse_rss_input(
            "# comment\n"
            "\n"
            "https://feeds.example.org/anime.xml\n"
            "https://feeds.example.org/anime.xml\n"
            "https://cdn.example.net/rss?type=video\n"
        )
        self.assertEqual(
            feeds,
            [
                {"name": "RSS 1: feeds.example.org", "url": "https://feeds.example.org/anime.xml"},
                {"name": "RSS 2: cdn.example.net", "url": "https://cdn.example.net/rss?type=video"},
            ],
        )

    def test_rejects_non_https_or_malformed_urls(self):
        for value in ("http://feeds.example.org/rss", "magnet:?xt=urn:btih:abc", "not a url"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_rss_input(value)

    def test_sync_replaces_only_feeds_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_path = root / "RSS input.txt"
            config_path = root / "config.json"
            input_path.write_text("https://feeds.example.org/rss.xml\n", encoding="utf-8")
            original = {
                "mode": "files",
                "feeds": [],
                "allowed_download_hosts": ["media.example.org"],
                "dry_run": True,
                "encode_h265": True,
            }
            config_path.write_text(json.dumps(original), encoding="utf-8")

            self.assertTrue(sync_rss_input(input_path, config_path))
            result = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(result["feeds"], [
                {"name": "RSS 1: feeds.example.org", "url": "https://feeds.example.org/rss.xml"}
            ])
            for key in ("mode", "allowed_download_hosts", "dry_run", "encode_h265"):
                self.assertEqual(result[key], original[key])
            self.assertFalse(sync_rss_input(input_path, config_path))


if __name__ == "__main__":
    unittest.main()
