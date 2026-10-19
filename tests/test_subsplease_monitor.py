import unittest

from tools.subsplease_monitor import merge_queue, parse_releases


class SubsPleaseMonitorTests(unittest.TestCase):
    def test_extracts_titles_and_ids_but_not_release_links(self):
        xml = """<rss><channel>
        <item><title>[Group] Anime Name - 03 (1080p)</title><guid>release-3</guid><link>magnet:?xt=urn:btih:secret</link></item>
        <item><title>[Group] Another Anime - 01 (1080p)</title><guid>release-1</guid><link>https://example.org/torrent</link></item>
        </channel></rss>"""
        releases = parse_releases(xml)
        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0]["title"], "[Group] Anime Name - 03 (1080p)")
        self.assertEqual(set(releases[0]), {"title", "id", "quality"})
        self.assertEqual(releases[0]["quality"], "1080p")
        self.assertNotIn("magnet", str(releases))
        self.assertNotIn("torrent", str(releases))

    def test_queue_deduplicates_and_keeps_only_unseen_releases(self):
        first = {"id": "1", "title": "A", "quality": "720p"}
        second = {"id": "2", "title": "B", "quality": "1080p"}
        queue = [{"id": "queued", "title": "Queued", "quality": "720p"}]
        merged = merge_queue(queue, [first, second, first], {"2"})
        self.assertEqual([item["id"] for item in merged], ["queued", "1"])


if __name__ == "__main__":
    unittest.main()
