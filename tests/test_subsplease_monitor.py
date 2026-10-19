import unittest

from tools.subsplease_monitor import parse_releases


class SubsPleaseMonitorTests(unittest.TestCase):
    def test_extracts_titles_and_ids_but_not_release_links(self):
        xml = """<rss><channel>
        <item><title>[Group] Anime Name - 03 (1080p)</title><guid>release-3</guid><link>magnet:?xt=urn:btih:secret</link></item>
        <item><title>[Group] Another Anime - 01 (1080p)</title><guid>release-1</guid><link>https://example.org/torrent</link></item>
        </channel></rss>"""
        releases = parse_releases(xml)
        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0]["title"], "[Group] Anime Name - 03 (1080p)")
        self.assertEqual(set(releases[0]), {"title", "id"})
        self.assertNotIn("magnet", str(releases))
        self.assertNotIn("torrent", str(releases))


if __name__ == "__main__":
    unittest.main()
