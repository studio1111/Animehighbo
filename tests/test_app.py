import json
import os
import tempfile
import unittest
from pathlib import Path

from app import (allowed_media_url, build_episode_caption, episode_details, load_config, load_state, parse_episode_feed, parse_feed, processed_filename, safe_filename, telegram_api_endpoint, telegram_method_for_file, _episode_group, _quality_rank)
from tools.subsplease_monitor import parse_releases


class FeedParserTests(unittest.TestCase):
    def test_new_state_includes_poster_deduplication_map(self):
        with tempfile.TemporaryDirectory() as temp:
            state = load_state(str(Path(temp) / "state.json"))
        self.assertEqual(state, {"published": {}, "seen": {}, "posters": {}})

    def test_parses_episode_page_and_poster(self):
        xml = '''<rss><channel><item><title>Moonlit Saga Episode 07</title><link>https://anime.example.org/moonlit/episode-7</link><enclosure url="https://cdn.example.org/poster.jpg" type="image/jpeg"/></item></channel></rss>'''
        self.assertEqual(parse_episode_feed(xml), [{
            "title": "Moonlit Saga Episode 07",
            "url": "https://anime.example.org/moonlit/episode-7",
            "image_url": "https://cdn.example.org/poster.jpg",
        }])

    def test_extracts_episode_number_without_guessing(self):
        self.assertEqual(episode_details("Moonlit Saga Episode 07"), ("Moonlit Saga", "07"))
        self.assertEqual(episode_details("青い星 第12話"), ("青い星", "12"))
        self.assertEqual(episode_details("Moonlit Saga latest update"), ("Moonlit Saga latest update", ""))

    def test_parses_rss_enclosure(self):
        xml = '''<?xml version="1.0"?><rss><channel><item><title>Episode 01</title><enclosure url="https://media.example.org/ep01.mkv" type="video/x-matroska"/><link>https://example.org/post</link></item></channel></rss>'''
        self.assertEqual(parse_feed(xml), [{"title": "Episode 01", "url": "https://media.example.org/ep01.mkv"}])

    def test_parses_atom_enclosure(self):
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Episode 02</title><link href="https://example.org/post"/><link rel="enclosure" href="https://media.example.org/ep02.mp4"/></entry></feed>'''
        self.assertEqual(parse_feed(xml)[0]["url"], "https://media.example.org/ep02.mp4")

    def test_rejects_page_torrent_magnet_and_unknown_host(self):
        allowed = ["media.example.org"]
        for url in (
            "https://media.example.org/watch/ep01",
            "https://media.example.org/ep01.torrent",
            "magnet:?xt=urn:btih:abc",
            "https://evil.example/ep01.mkv",
            "http://media.example.org/ep01.mkv",
        ):
            with self.subTest(url=url):
                self.assertFalse(allowed_media_url(url, allowed))

    def test_accepts_direct_media_on_exact_allowlisted_host(self):
        self.assertTrue(allowed_media_url("https://media.example.org/ep01.mkv", ["media.example.org"]))
        self.assertFalse(allowed_media_url("https://sub.media.example.org/ep01.mkv", ["media.example.org"]))

    def test_filename_is_sanitized(self):
        self.assertEqual(safe_filename("Episode / 01?", "https://media.example.org/video.mkv"), "Episode _ 01.mkv")

    def test_telegram_uses_video_for_mp4_and_document_for_mkv(self):
        self.assertEqual(telegram_method_for_file("episode.mp4"), "sendVideo")
        self.assertEqual(telegram_method_for_file("episode.MP4"), "sendVideo")
        self.assertEqual(telegram_method_for_file("episode.mkv"), "sendDocument")
        self.assertEqual(telegram_method_for_file("episode.webm"), "sendDocument")

    def test_telegram_api_endpoint_defaults_to_cloud_api(self):
        import os
        previous = os.environ.pop("TELEGRAM_API_BASE_URL", None)
        try:
            self.assertEqual(
                telegram_api_endpoint("test-token", "sendDocument"),
                "https://api.telegram.org/bottest-token/sendDocument",
            )
        finally:
            if previous is not None:
                os.environ["TELEGRAM_API_BASE_URL"] = previous

    def test_telegram_api_endpoint_accepts_local_loopback_http_only(self):
        import os
        previous = os.environ.get("TELEGRAM_API_BASE_URL")
        try:
            os.environ["TELEGRAM_API_BASE_URL"] = "http://127.0.0.1:8081/"
            self.assertEqual(
                telegram_api_endpoint("test-token", "sendDocument"),
                "http://127.0.0.1:8081/bottest-token/sendDocument",
            )
            os.environ["TELEGRAM_API_BASE_URL"] = "http://example.com"
            with self.assertRaisesRegex(ValueError, "must be HTTPS"):
                telegram_api_endpoint("test-token", "sendDocument")
        finally:
            if previous is None:
                os.environ.pop("TELEGRAM_API_BASE_URL", None)
            else:
                os.environ["TELEGRAM_API_BASE_URL"] = previous

    def test_media_size_override_is_bounded(self):
        previous = os.environ.get("ANIMEHIGHBO_MAX_FILE_SIZE_MB")
        try:
            with tempfile.TemporaryDirectory() as temp:
                config_path = Path(temp) / "config.json"
                config_path.write_text(json.dumps({
                    "feeds": [],
                    "allowed_download_hosts": [],
                    "max_file_size_mb": 45,
                }), encoding="utf-8")
                os.environ["ANIMEHIGHBO_MAX_FILE_SIZE_MB"] = "1800"
                self.assertEqual(load_config(str(config_path))["max_file_size_mb"], 1800)
                os.environ["ANIMEHIGHBO_MAX_FILE_SIZE_MB"] = "2000"
                with self.assertRaisesRegex(ValueError, "between 1 and 1900"):
                    load_config(str(config_path))
        finally:
            if previous is None:
                os.environ.pop("ANIMEHIGHBO_MAX_FILE_SIZE_MB", None)
            else:
                os.environ["ANIMEHIGHBO_MAX_FILE_SIZE_MB"] = previous


    def test_encoder_defaults_preserve_existing_configs(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            config_path.write_text(json.dumps({
                "feeds": [],
                "allowed_download_hosts": [],
            }), encoding="utf-8")
            config = load_config(str(config_path))
        self.assertFalse(config["encode_h265"])
        self.assertEqual(config["h265_crf"], 24)
        self.assertEqual(config["h265_preset"], "slow")
        self.assertFalse(config["burn_persian_subtitles"])

    def test_burn_in_requires_bilingual_subtitles_and_h265(self):
        for settings in (
            {"burn_persian_subtitles": True},
            {"bilingual_subtitles": True, "burn_persian_subtitles": True},
        ):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as temp:
                config_path = Path(temp) / "config.json"
                config_path.write_text(json.dumps({
                    "feeds": [],
                    "allowed_download_hosts": [],
                    **settings,
                }), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "requires"):
                    load_config(str(config_path))


class PersianAnimeCaptionTests(unittest.TestCase):
    def test_caption_contains_requested_fields_and_collapsible_synopsis(self):
        metadata = {
            "title": {"english": "Moonlit Saga"},
            "season": "FALL",
            "seasonYear": 2026,
            "episodes": 12,
            "genres": ["Action", "Fantasy"],
            "format": "TV",
            "isAdult": False,
            "description": "A young traveler discovers a hidden world beneath the city.",
        }
        from unittest.mock import patch
        with patch("app.translate_title_to_persian", return_value="حماسه مهتاب"), patch("app.translate_anime_synopsis", return_value="یه مسافر جوان دنیای پنهانی زیر شهر رو کشف می‌کنه."):
            caption = build_episode_caption(
                "Moonlit Saga Episode 07 1080p", "07", metadata=metadata
            )
        self.assertIn("Moonlit Saga", caption)
        self.assertIn("حماسه مهتاب", caption)
        self.assertIn("قسمت 07", caption)
        self.assertIn("@Animehighbo", caption)
        self.assertEqual(caption.splitlines()[-1], "@Animehighbo")
        self.assertLess(caption.index("📝 "), caption.rindex("@Animehighbo"))
        self.assertNotIn("1080p", caption)
        self.assertNotIn("تعداد کامل قسمت‌ها", caption)
        self.assertLessEqual(len(caption), 1024)
        self.assertNotIn("لینک تماشا", caption)

    def test_unknown_metadata_is_not_invented(self):
        from unittest.mock import patch
        with patch("app.translate_title_to_persian", return_value="ترجمه فارسی"):
            caption = build_episode_caption("Some Anime Episode 3", "3", metadata={})
        self.assertIn("خلاصهٔ این انیمه فعلاً در دسترس نیست.", caption)
        self.assertIn("@Animehighbo", caption)

    def test_sequel_season_is_not_mislabeled_as_first_season(self):
        from unittest.mock import patch
        metadata = {
            "title": {"english": "Attack on Titan: The Final Season"},
            "format": "TV",
            "episodes": 16,
            "isAdult": False,
        }
        with patch("app.translate_title_to_persian", return_value="حمله به تایتان"):
            caption = build_episode_caption("Attack on Titan Episode 01 720p", "01", metadata=metadata)
        self.assertIn("حمله به تایتان", caption)
        self.assertIn("@Animehighbo", caption)

    def test_processed_filename_has_only_anime_quality_and_channel(self):
        self.assertEqual(
            processed_filename("[SubsPlease] PSYREN - 03 (1080p) [ABC123].mkv", "1080p"),
            "PSYREN 1080p @Animehighbo.mkv",
        )

    def test_episode_group_matches_different_quality_releases(self):
        first = _episode_group("[SubsPlease] PSYREN - 03 (1080p) [ABC123].mkv")
        second = _episode_group("[SubsPlease] PSYREN - 03 (720p) [DEF456].mkv")
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1:], second[1:])

    def test_quality_order_is_1080_then_720_then_480(self):
        titles = ["Anime - 03 (480p)", "Anime - 03 (1080p)", "Anime - 03 (720p)"]
        self.assertEqual(sorted(titles, key=_quality_rank), [
            "Anime - 03 (1080p)", "Anime - 03 (720p)", "Anime - 03 (480p)"
        ])

class SubsPleaseConnectionParsingTests(unittest.TestCase):
    def test_parses_release_titles_and_quality_without_retaining_magnet_links(self):
        xml = """<rss><channel>
          <item><title>[SubsPlease] PSYREN - 03 (1080p) [6DC795E8].mkv</title>
            <link>magnet:?xt=urn:btih:SECRET</link><guid>release-1080</guid></item>
        </channel></rss>"""
        releases = parse_releases(xml, "1080p")
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]["quality"], "1080p")
        self.assertEqual(releases[0]["title"], "[SubsPlease] PSYREN - 03 (1080p) [6DC795E8].mkv")
        self.assertNotIn("url", releases[0])
        self.assertNotIn("magnet", str(releases[0]))

    def test_extracts_episode_number_from_subsplease_release_title(self):
        self.assertEqual(
            episode_details("[SubsPlease] PSYREN - 03 (1080p) [6DC795E8].mkv"),
            ("PSYREN", "03"),
        )
        self.assertEqual(
            episode_details("[SubsPlease] Bleach - Sennen Kessen Hen - 49 (1080p) [3DCF4BD3].mkv"),
            ("Bleach - Sennen Kessen Hen", "49"),
        )

