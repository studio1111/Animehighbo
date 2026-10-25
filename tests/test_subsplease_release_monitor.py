import unittest
from datetime import date

from tools.subsplease_release_monitor import (
    build_report,
    parse_feed,
    release_key,
)

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>SubsPlease RSS</title>
<item><title>[SubsPlease] Kashita Maryoku - 04 (1080p) [ABC12345].mkv</title>
<link>magnet:?xt=urn:btih:SECRET_HASH</link>
<pubDate>Sat, 24 Oct 2026 18:31:45 +0000</pubDate>
<guid>magnet:?xt=urn:btih:SECRET_HASH</guid></item>
<item><title>[SubsPlease] Jujutsu Kaisen (25-47) (1080p) [Batch]</title>
<link>magnet:?xt=urn:btih:BATCH_HASH</link>
<pubDate>Sat, 24 Oct 2026 20:05:24 +0000</pubDate>
<guid>magnet:?xt=urn:btih:BATCH_HASH</guid></item>
</channel></rss>"""


class SubsPleaseReleaseMonitorTests(unittest.TestCase):
    def test_parser_keeps_metadata_only_and_excludes_batch_releases(self):
        rows = parse_feed(RSS, "1080p")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Kashita Maryoku")
        self.assertEqual(rows[0]["episode"], 4)
        self.assertEqual(rows[0]["release_date"], "2026-10-24")
        self.assertNotIn("uri", rows[0])
        self.assertNotIn("magnet", str(rows[0]).casefold())
        self.assertNotIn("SECRET_HASH", str(rows))

    def test_report_marks_manifest_coverage_without_granting_rights(self):
        feed_rows = parse_feed(RSS, "1080p")
        manifest = [{
            "title": "Kashita Maryoku - 04 (1080p)",
            "episode": 4,
            "release_date": "2026-10-24",
            "authorized_for_redistribution": True,
        }]
        report = build_report(feed_rows, manifest, date(2026, 10, 25), "2026-10-01")
        self.assertEqual(report["summary"]["feed_releases"], 1)
        self.assertEqual(report["summary"]["matched_in_manifest"], 1)
        self.assertEqual(report["summary"]["review_required"], 0)
        self.assertNotIn("authorized_for_redistribution", report["releases"][0])
        self.assertNotIn("uri", report["releases"][0])

    def test_release_key_uses_series_and_episode_not_quality(self):
        self.assertEqual(
            release_key({"title": "Kashita Maryoku - 04 (1080p)", "episode": 4}),
            release_key({"title": "Kashita Maryoku - 04 (720p)", "episode": 4}),
        )


if __name__ == "__main__":
    unittest.main()
