import app
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import (allowed_media_url, anime_japanese_title, build_episode_caption, episode_details, has_authorized_torrent_source, has_file_media_source, load_archive_manifest, load_config, load_state, parse_episode_feed, parse_feed, processed_filename, safe_filename, telegram_api_endpoint, telegram_method_for_file, torrent_source_uri, telegram_upload_and_send, run as run_app, _episode_group, _episode_record_complete, _poster_identity, _quality_rank, _preferred_source)
from tools.subsplease_monitor import parse_releases




class AnimeCatalogFallbackTests(unittest.TestCase):
    def test_fills_missing_anilist_fields_from_jikan_and_kitsu(self):
        import app
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "empty-cache.json"
            cache.write_text('{"published": {}}', encoding="utf-8")
            with patch.dict(os.environ, {"ANIMEHIGHBO_POSTER_CACHE": str(cache)}):
                with patch.object(app, "_anilist_metadata", return_value={
                    "title": {"english": "Keroro Gunsou", "romaji": "Keroro Gunsou"},
                    "coverImage": {},
                    "genres": [],
                    "description": "",
                }), patch.object(app, "_jikan_metadata", return_value={
                    "title": {"english": "Keroro Gunsou", "romaji": "Keroro Gunsou"},
                    "coverImage": {"extraLarge": "https://cdn.example/poster.jpg"},
                    "seasonYear": 2026,
                    "genres": ["Comedy", "Sci-Fi"],
                    "format": "TV",
                    "studios": {"nodes": [{"name": "Sunrise"}]},
                    "description": "A verified synopsis.",
                }), patch.object(app, "_kitsu_metadata", return_value={
                    "title": {"english": "Keroro Gunsou"},
                    "coverImage": {"extraLarge": "https://kitsu.example/poster.jpg"},
                    "status": "RELEASING",
                    "averageScore": 78,
                }):
                    metadata = app.anime_catalog_metadata("Keroro Gunsou (2026)")
        self.assertEqual(metadata["coverImage"]["extraLarge"], "https://cdn.example/poster.jpg")
        self.assertEqual(metadata["genres"], ["Comedy", "Sci-Fi"])
        self.assertEqual(metadata["seasonYear"], 2026)
        self.assertEqual(metadata["status"], "RELEASING")
        self.assertEqual(metadata["averageScore"], 78)
        self.assertEqual(metadata["studios"]["nodes"][0]["name"], "Sunrise")
        self.assertEqual(metadata["description"], "A verified synopsis.")

    def test_metadata_catalog_failure_does_not_prevent_other_sources(self):
        import app
        with patch.object(app, "_anilist_metadata", side_effect=RuntimeError("AniList unavailable")), \
             patch.object(app, "_jikan_metadata", return_value={}), \
             patch.object(app, "_kitsu_metadata", return_value={
                 "title": {"english": "Sample"},
                 "coverImage": {"extraLarge": "https://kitsu.example/poster.jpg"},
                 "description": "Sample synopsis",
             }):
            metadata = app.anime_catalog_metadata("Sample")
        self.assertEqual(metadata["coverImage"]["extraLarge"], "https://kitsu.example/poster.jpg")
        self.assertEqual(metadata["description"], "Sample synopsis")


    def test_cached_poster_is_used_when_live_catalogs_fail(self):
        import app
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "anime_poster_state.json"
            cache.write_text(json.dumps({"published": {
                "dark machine – the animation": {
                    "title": "Dark Machine – The Animation",
                    "poster_url": "https://cdn.example/official-poster.jpg",
                    "anilist_id": 179876,
                    "metadata": {
                        "description": "A verified cached synopsis.",
                        "genres": ["Fantasy", "Action"],
                        "seasonYear": 2026,
                    },
                }
            }}), encoding="utf-8")
            with patch.dict(os.environ, {"ANIMEHIGHBO_POSTER_CACHE": str(cache)}):
                with patch.object(app, "_anilist_metadata", side_effect=RuntimeError("offline")), patch.object(app, "_jikan_metadata", return_value={}), patch.object(app, "_kitsu_metadata", return_value={}):
                    metadata = app.anime_catalog_metadata("Dark Machine - The Animation")
        self.assertEqual(metadata["coverImage"]["extraLarge"], "https://cdn.example/official-poster.jpg")
        self.assertEqual(metadata["anilist_id"], 179876)
        self.assertEqual(metadata["description"], "A verified cached synopsis.")
        self.assertEqual(metadata["genres"], ["Fantasy", "Action"])
        self.assertEqual(metadata["seasonYear"], 2026)

class JapaneseRomajiTitleTests(unittest.TestCase):
    def test_prefers_anilist_romaji_over_japanese_script(self):
        self.assertEqual(
            anime_japanese_title("Demon Slayer", {
                "title": {
                    "english": "Demon Slayer",
                    "romaji": "Kimetsu no Yaiba",
                    "native": "鬼滅の刃",
                }
            }),
            "Kimetsu no Yaiba",
        )

    @patch("app.requests.get")
    def test_jikan_fallback_returns_latin_romaji_title(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [{
                "title_english": "Demon Slayer",
                "title": "Kimetsu no Yaiba",
                "titles": [{"title": "鬼滅の刃"}],
            }]
        }
        get.return_value = response
        self.assertEqual(anime_japanese_title("Demon Slayer", {"title": {"english": "Demon Slayer"}}),
                         "Kimetsu no Yaiba")

    def test_caption_uses_japanese_romaji_not_native_script(self):
        metadata = {"title": {"english": "Demon Slayer", "romaji": "Kimetsu no Yaiba", "native": "鬼滅の刃"}}
        with patch("app.translate_title_to_persian", return_value="شیطان‌کش"), patch(
            "app.translate_anime_synopsis", return_value="خلاصهٔ آزمایشی"
        ):
            caption = build_episode_caption("Demon Slayer - 01 (1080p)", "01", metadata=metadata)
        self.assertIn("English:", caption)
        self.assertIn("فارسی:", caption)
        self.assertIn("日本語（ローマ字）:", caption)
        self.assertIn("Kimetsu no Yaiba", caption)
        self.assertNotIn("鬼滅の刃", caption)


class PreferredSourceTests(unittest.TestCase):
    def test_episode_is_complete_only_after_all_three_quality_files(self):
        self.assertFalse(_episode_record_complete({}))
        self.assertFalse(_episode_record_complete({"complete_qualities": ["720p"]}))
        self.assertFalse(_episode_record_complete({"complete_qualities": ["1080p", "720p"]}))
        self.assertTrue(_episode_record_complete({
            "complete_qualities": ["1080p", "720p", "480p"],
        }))

    def test_prefers_1080p_even_when_lower_quality_entries_come_first(self):
        entries = [
            {"title": "Sample - 01 [480p]"},
            {"title": "Sample - 01 [720p]"},
            {"title": "Sample - 01 [1080p]"},
        ]
        self.assertEqual(_preferred_source(entries)["title"], "Sample - 01 [1080p]")

    def test_falls_back_to_720p_when_1080p_is_missing(self):
        entries = [
            {"title": "Sample - 01 [480p]"},
            {"title": "Sample - 01 [720p]"},
        ]
        self.assertEqual(_preferred_source(entries)["title"], "Sample - 01 [720p]")

    def test_never_selects_480p_or_unknown_quality(self):
        entries = [
            {"title": "Sample - 01 [480p]"},
            {"title": "Sample - 01"},
        ]
        self.assertIsNone(_preferred_source(entries))


class ArchivePosterIdentityTests(unittest.TestCase):
    def test_archive_mode_uses_fresh_poster_identity_after_channel_cleanup(self):
        self.assertNotEqual(_poster_identity("series:01", False), _poster_identity("series:01", True))
        self.assertEqual(_poster_identity("series:01", True), "archive:series:01")


class PreparedEpisodePublisherTests(unittest.TestCase):
    def _write_episode_artifact(self, root, identity, title, day, qualities=("1080p", "720p", "480p")):
        import hashlib
        import json
        from pathlib import Path

        folder = Path(root) / f"episode-{identity}"
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for quality in qualities:
            filename = f"{title.replace(' ', '_')}_{quality}.mkv"
            path = folder / filename
            path.write_bytes((f"{title}-{quality}-test-data".encode("utf-8") * 200))
            files.append({
                "quality": quality,
                "filename": filename,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "output_identity": f"{identity}:{quality}",
            })
        (folder / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "identity": identity,
            "series_key": f"{title.casefold()}::season-1",
            "group_id": f"group-{identity}",
            "poster_identity": f"archive:group-{identity}",
            "title": title,
            "episode": "01",
            "release_date": day,
            "source_quality": "1080p",
            "authorized_for_redistribution": True,
            "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
            "authorization_reviewed_at": "2026-10-24",
            "files": files,
        }), encoding="utf-8")

    def test_publishes_poster_then_reply_videos_with_metadata(self):
        import os
        import tempfile
        from unittest.mock import patch
        import app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "prepared-artifacts"
            root.mkdir()
            self._write_episode_artifact(root, "episode-b", "Series B", "2026-10-05")
            self._write_episode_artifact(root, "episode-a", "Series A", "2026-10-05")
            state_path = Path(temp) / "state.json"
            state = {"published": {}, "seen": {}, "posters": {}}
            events = []

            thumbnail = Path(temp) / "poster.jpg"
            thumbnail.write_bytes(b"compressed-jpeg")

            def send_file(_token, _chat, path, caption, thumbnail_path=None, reply_to_message_id=None):
                events.append(("file", caption, thumbnail_path, reply_to_message_id))
                return str(len(events))

            def send_poster(_token, _chat, title, episode, _metadata, fallback_image_path=None):
                message_id = f"poster-{title}"
                events.append(("poster", title, message_id, fallback_image_path))
                return message_id

            with patch.dict(os.environ, {
                "ANIMEHIGHBO_PREPARED_DIR": str(root),
                "ANIMEHIGHBO_RELEASE_DATE": "2026-10-05",
            }), patch.object(app, "anime_catalog_metadata", return_value={}), \
                 patch.object(app, "create_anime_video_thumbnail", return_value=thumbnail), \
                 patch.object(app, "telegram_send_anime_poster", side_effect=send_poster), \
                 patch.object(app, "telegram_upload_and_send", side_effect=send_file):
                count = app.publish_prepared_episodes(
                    {"state_file": str(state_path), "max_file_size_mb": 1800,
                     "bilingual_subtitles": True},
                    state, False, "token", "@channel",
                )

        self.assertEqual(count, 0)  # Successful publication returns a shell-safe exit status.
        self.assertEqual([event[0] for event in events],
                         ["poster", "file", "file", "file", "poster", "file", "file", "file"])
        self.assertEqual([event[1] for event in events if event[0] == "poster"],
                         ["Series A", "Series B"])
        file_events = [event for event in events if event[0] == "file"]
        self.assertEqual([event[3] for event in file_events], ["poster-Series A"] * 3 + ["poster-Series B"] * 3)
        self.assertTrue(all(event[2] == thumbnail for event in file_events))
        self.assertTrue(all("EP " in event[1]
                            and "@Animehighbo" not in event[1]
                            and "English:" not in event[1]
                            and "فارسی:" not in event[1]
                            and "کیفیت:" not in event[1] for event in file_events))
        self.assertEqual(
            [next(q for q in ("1080p", "720p", "480p") if q in event[1])
             for event in file_events],
            ["1080p", "720p", "480p"] * 2,
        )
        self.assertEqual(state["published"]["episode-a"]["complete_qualities"],
                         ["1080p", "720p", "480p"])
        self.assertEqual(state["published"]["episode-b"]["complete_qualities"],
                         ["1080p", "720p", "480p"])
        self.assertIn("archive:group-episode-a", state["posters"])
        self.assertIn("archive:group-episode-b", state["posters"])

    def test_replaces_deleted_poster_anchor_and_retries_video_reply(self):
        import os
        import tempfile
        from unittest.mock import patch
        import app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "prepared-artifacts"
            root.mkdir()
            self._write_episode_artifact(root, "episode-a", "Series A", "2026-10-05")
            state_path = Path(temp) / "state.json"
            state = {
                "published": {}, "seen": {},
                "posters": {"archive:group-episode-a": {"message_id": "deleted-poster"}},
            }
            thumbnail = Path(temp) / "poster.jpg"
            thumbnail.write_bytes(b"compressed-jpeg")
            attempts = []
            poster_ids = iter(["replacement-poster"])

            def send_file(_token, _chat, path, caption, thumbnail_path=None, reply_to_message_id=None):
                attempts.append(reply_to_message_id)
                if len(attempts) == 1:
                    raise RuntimeError(
                        "Telegram sendDocument failed (HTTP 400): Bad Request: "
                        "message to be replied not found"
                    )
                return f"video-{len(attempts)}"

            def send_poster(_token, _chat, title, episode, _metadata, fallback_image_path=None):
                return next(poster_ids)

            with patch.dict(os.environ, {
                "ANIMEHIGHBO_PREPARED_DIR": str(root),
                "ANIMEHIGHBO_RELEASE_DATE": "2026-10-05",
            }), patch.object(app, "anime_catalog_metadata", return_value={}), \
                 patch.object(app, "create_anime_video_thumbnail", return_value=thumbnail), \
                 patch.object(app, "telegram_send_anime_poster", side_effect=send_poster) as poster, \
                 patch.object(app, "telegram_upload_and_send", side_effect=send_file):
                app.publish_prepared_episodes(
                    {"state_file": str(state_path), "max_file_size_mb": 1800,
                     "bilingual_subtitles": True},
                    state, False, "token", "@channel",
                )

        self.assertEqual(poster.call_count, 1)
        self.assertEqual(attempts[0], "deleted-poster")
        self.assertEqual(attempts[1:], ["replacement-poster"] * 3)
        self.assertEqual(
            state["posters"]["archive:group-episode-a"]["message_id"],
            "replacement-poster",
        )
        self.assertEqual(state["published"]["episode-a"]["complete_qualities"],
                         ["1080p", "720p", "480p"])

    def test_invalid_manifest_returns_failure_instead_of_green_success(self):
        import os
        import tempfile
        from unittest.mock import patch
        import app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "prepared-artifacts"
            root.mkdir()
            folder = root / "episode-broken"
            folder.mkdir()
            (folder / "manifest.json").write_text('{"schema_version": 1}\\n', encoding="utf-8")
            with patch.dict(os.environ, {
                "ANIMEHIGHBO_PREPARED_DIR": str(root),
                "ANIMEHIGHBO_RELEASE_DATE": "2026-10-05",
            }), patch.object(app, "telegram_send_anime_poster") as poster:
                result = app.publish_prepared_episodes(
                    {"state_file": str(Path(temp) / "state.json"), "max_file_size_mb": 1800},
                    {"published": {}, "seen": {}, "posters": {}},
                    False, "token", "@channel",
                )
            self.assertEqual(result, 1)
            poster.assert_not_called()

    def test_missing_quality_prevents_any_poster_from_being_sent(self):
        import os
        import tempfile
        from unittest.mock import patch
        import app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "prepared-artifacts"
            root.mkdir()
            self._write_episode_artifact(root, "episode-a", "Series A", "2026-10-05",
                                         qualities=("1080p", "720p"))
            with patch.dict(os.environ, {
                "ANIMEHIGHBO_PREPARED_DIR": str(root),
                "ANIMEHIGHBO_RELEASE_DATE": "2026-10-05",
            }), patch.object(app, "telegram_send_anime_poster") as poster:
                count = app.publish_prepared_episodes(
                    {"state_file": str(Path(temp) / "state.json"),
                     "max_file_size_mb": 1800, "bilingual_subtitles": True},
                    {"published": {}, "seen": {}, "posters": {}},
                    False, "token", "@channel",
                )
                self.assertEqual(count, 1)  # Invalid bundle must make the workflow fail.
                poster.assert_not_called()


class ArchiveManifestTests(unittest.TestCase):
    def test_manual_exclusion_skips_every_episode_of_selected_series_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_path = root / "archive.json"
            manifest_path.write_text(json.dumps([
                {"title": "Moonlit Saga - 01 (1080p)", "url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                {"title": "Moonlit Saga - 02 (1080p)", "url": "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
                {"title": "Other Series - 01 (1080p)", "url": "magnet:?xt=urn:btih:cccccccccccccccccccccccccccccccccccccccc"},
            ]), encoding="utf-8")
            (root / "archive_exclusions.json").write_text(
                json.dumps(["Moonlit Saga"]), encoding="utf-8"
            )
            entries = load_archive_manifest(str(manifest_path))
        self.assertEqual([item["title"] for item in entries], ["Other Series - 01 (1080p)"])

    def test_invalid_exclusion_file_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "archive.json").write_text("[]", encoding="utf-8")
            (root / "archive_exclusions.json").write_text('{"title": "Moonlit Saga"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "list of series-title strings"):
                load_archive_manifest(str(root / "archive.json"))

    def test_manifest_preserves_episode_order_and_requires_explicit_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "archive.json"
            path.write_text(json.dumps([
                {
                    "title": "Moonlit Saga - 01 (1080p)",
                    "url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "authorized_for_redistribution": True,
                    "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                    "authorization_reviewed_at": "2026-10-24",
                },
                {
                    "title": "Moonlit Saga - 02 (1080p)",
                    "url": "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "authorized_for_redistribution": True,
                    "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                    "authorization_reviewed_at": "2026-10-24",
                },
            ]), encoding="utf-8")
            entries = load_archive_manifest(str(path))
        self.assertEqual([item["title"] for item in entries], [
            "Moonlit Saga - 01 (1080p)", "Moonlit Saga - 02 (1080p)"
        ])
        self.assertEqual([item["archive_order"] for item in entries], [0, 1])
        self.assertTrue(all(item["torrent_source"] for item in entries))

    def test_manifest_marks_direct_video_urls_as_media_sources_not_torrents(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "archive.json"
            path.write_text(json.dumps([{
                "title": "Moonlit Saga - 01 (1080p)",
                "url": "https://media.example.org/moonlit-01.mkv",
                "authorized_for_redistribution": True,
                "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                "authorization_reviewed_at": "2026-10-24",
            }]), encoding="utf-8")
            entries = load_archive_manifest(str(path))
        self.assertFalse(entries[0]["torrent_source"])
        self.assertEqual(entries[0]["url"], "https://media.example.org/moonlit-01.mkv")

    def test_manifest_accepts_entries_without_automatic_authorization_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "archive.json"
            path.write_text(json.dumps([{
                "title": "Moonlit Saga - 01 (1080p)",
                "url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            }]), encoding="utf-8")
            entries = load_archive_manifest(str(path))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Moonlit Saga - 01 (1080p)")

    def test_manifest_rejects_batches_missing_episode_numbers_and_duplicate_sources(self):
        cases = [
            ([{"title": "[Batch] Moonlit Saga (1080p)", "url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "authorized_for_redistribution": True, "authorization_evidence": "https://rights.example.org/licenses/animehighbo", "authorization_reviewed_at": "2026-10-24"}], "single episode"),
            ([{"title": "Moonlit Saga Complete", "url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "authorized_for_redistribution": True, "authorization_evidence": "https://rights.example.org/licenses/animehighbo", "authorization_reviewed_at": "2026-10-24"}], "no detectable episode"),
            ([
                {"title": "Moonlit Saga - 01 (1080p)", "url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "authorized_for_redistribution": True, "authorization_evidence": "https://rights.example.org/licenses/animehighbo", "authorization_reviewed_at": "2026-10-24"},
                {"title": "Moonlit Saga - 02 (1080p)", "url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "authorized_for_redistribution": True, "authorization_evidence": "https://rights.example.org/licenses/animehighbo", "authorization_reviewed_at": "2026-10-24"},
            ], "Duplicate archive source"),
        ]
        for entries, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "archive.json"
                path.write_text(json.dumps(entries), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    load_archive_manifest(str(path))


class TorrentSourceConfigTests(unittest.TestCase):
    def test_telegram_upload_uses_long_timeout_and_poster_thumbnail(self):
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp) / "sample.mkv"
            media.write_bytes(b"sample media")
            thumbnail = Path(temp) / "poster.jpg"
            thumbnail.write_bytes(b"compressed-jpeg")
            response = Mock()
            response.json.return_value = {"ok": True, "result": {"message_id": 77}}
            with patch("app.requests.post", return_value=response) as post:
                message_id = telegram_upload_and_send(
                    "test-token", "@test-channel", media, "caption", thumbnail_path=thumbnail,
                    reply_to_message_id="55",
                )
        self.assertEqual(message_id, "77")
        self.assertEqual(post.call_args.kwargs["timeout"], (30, 1800))
        self.assertEqual(post.call_args.kwargs["data"]["reply_to_message_id"], "55")
        self.assertIn("thumbnail", post.call_args.kwargs["files"])
        self.assertEqual(post.call_args.kwargs["files"]["thumbnail"][2], "image/jpeg")

    def test_accepts_both_uri_and_url_source_keys(self):
        magnet = "magnet:?xt=urn:btih:0123456789012345678901234567890123456789"
        self.assertEqual(torrent_source_uri({"uri": magnet}), magnet)
        self.assertEqual(torrent_source_uri({"url": magnet}), magnet)

    def test_rejects_conflicting_uri_and_url_values(self):
        with self.assertRaisesRegex(ValueError, "conflicting"):
            torrent_source_uri({
                "uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "url": "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            })

    def test_file_pipeline_requires_direct_media_allowlist_or_authorized_torrent(self):
        self.assertFalse(has_file_media_source({
            "feeds": [{"url": "https://subsplease.org/rss/?r=1080"}],
            "allowed_download_hosts": [],
            "torrent_sources": [],
        }))
        self.assertTrue(has_file_media_source({
            "feeds": [{"url": "https://media.example.org/feed.xml"}],
            "allowed_download_hosts": ["media.example.org"],
            "torrent_sources": [],
        }))
        self.assertTrue(has_file_media_source({
            "feeds": [{
                "url": "https://media.example.org/feed.xml",
                "authorized_for_redistribution": True,
                "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                "authorization_reviewed_at": "2026-10-24",
            }],
            "allowed_download_hosts": ["media.example.org"],
            "torrent_sources": [],
        }))
        self.assertTrue(has_file_media_source({
            "feeds": [{"url": "https://subsplease.org/rss/?r=1080"}],
            "allowed_download_hosts": [],
            "allowed_torrent_hosts": [],
            "torrent_feed_hosts": ["subsplease.org"],
            "torrent_sources": [],
        }))
        self.assertTrue(has_file_media_source({
            "feeds": [{
                "url": "https://subsplease.org/rss/?r=1080",
                "authorized_for_redistribution": True,
                "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                "authorization_reviewed_at": "2026-10-24",
            }],
            "allowed_download_hosts": [],
            "allowed_torrent_hosts": [],
            "torrent_feed_hosts": ["subsplease.org"],
            "torrent_sources": [],
        }))
        self.assertTrue(has_file_media_source({
            "archive_mode": True,
            "archive_manifest": "data/archive_manifest.json",
        }))

    def test_configured_torrent_source_needs_no_automatic_authorization_metadata(self):
        source = {
            "title": "Open Sample - 01 (1080p)",
            "url": "magnet:?xt=urn:btih:0123456789012345678901234567890123456789",
            "authorized_for_redistribution": True,
            "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
            "authorization_reviewed_at": "2026-10-24",
        }
        self.assertTrue(has_authorized_torrent_source({"feeds": [], "torrent_sources": [source]}))
        source.pop("authorization_evidence")
        source.pop("authorization_reviewed_at")
        source["authorized_for_redistribution"] = False
        self.assertTrue(has_authorized_torrent_source({"feeds": [], "torrent_sources": [source]}))

    def test_link_poster_rejects_success_response_without_message_id(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "result": {}}

        with patch("app.anime_catalog_metadata", return_value={}), \
             patch("app.build_episode_caption", return_value="caption"), \
             patch("app.requests.post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "message ID"):
                app.telegram_send_episode(
                    "token", "@channel", b"image bytes", "image/jpeg",
                    "Example Anime - 01", "01", "https://media.example.org/episode/1",
                )

    def test_link_mode_does_not_duplicate_post_after_ambiguous_photo_timeout(self):
        config = {
            "mode": "links",
            "feeds": [{"name": "Sample", "url": "https://media.example.org/feed.xml"}],
            "dry_run": False,
            "state_file": "unused-state.json",
            "request_timeout_seconds": 30,
            "poll_limit_per_feed": 30,
        }
        state = {"published": {}, "seen": {}, "posters": {}}
        feed_response = Mock()
        feed_response.text = "<rss/>"

        with patch("app.requests.Session") as session_factory, \
             patch("app.parse_episode_feed", return_value=[{
                 "title": "Example Anime - 01",
                 "url": "https://media.example.org/episode/1",
                 "image_url": "https://media.example.org/poster.jpg",
             }]), \
             patch("app.download_poster", return_value=(b"image bytes", "image/jpeg")), \
             patch("app.telegram_send_episode", side_effect=app.requests.Timeout("response lost")), \
             patch("app.requests.post") as text_post:
            session_factory.return_value.get.return_value = feed_response
            with self.assertRaisesRegex(RuntimeError, "failure"):
                app.run_link_mode(config, state, False, "token", "@channel")
            text_post.assert_not_called()

    def test_link_mode_feed_failure_returns_nonzero_for_actions(self):
        config = {
            "mode": "links",
            "feeds": [{"name": "Sample", "url": "https://media.example.org/feed.xml"}],
            "dry_run": False,
            "state_file": "unused-state.json",
            "request_timeout_seconds": 30,
            "poll_limit_per_feed": 30,
        }
        state = {"published": {}, "seen": {}, "posters": {}}
        env = {
            "ANIMEHIGHBO_DRY_RUN": "false",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "@test-channel",
        }
        with patch("app.load_config", return_value=config), \
             patch("app.load_state", return_value=state), \
             patch("app.save_state"), \
             patch("app.requests.Session") as session_factory, \
             patch.dict(os.environ, env, clear=False):
            session_factory.return_value.get.side_effect = RuntimeError("simulated link feed failure")
            self.assertEqual(run_app(), 1)

    def test_rss_feed_failure_returns_nonzero_for_actions(self):
        config = {
            "mode": "files",
            "feeds": [{
                "name": "Authorized sample",
                "url": "https://media.example.org/feed.xml",
                "authorized_for_redistribution": True,
                "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                "authorization_reviewed_at": "2026-10-24",
            }],
            "dry_run": False,
            "state_file": "unused-state.json",
            "request_timeout_seconds": 30,
            "poll_limit_per_feed": 30,
            "allowed_download_hosts": ["cdn.example.org"],
            "torrent_sources": [],
        }
        state = {"published": {}, "seen": {}, "posters": {}}
        env = {
            "ANIMEHIGHBO_DRY_RUN": "false",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "@test-channel",
        }
        with patch("app.load_config", return_value=config), \
             patch("app.load_state", return_value=state), \
             patch("app.save_state"), \
             patch("app.requests.Session") as session_factory, \
             patch.dict(os.environ, env, clear=False):
            session_factory.return_value.get.side_effect = RuntimeError("simulated feed failure")
            self.assertEqual(run_app(), 1)

    def test_empty_file_pipeline_does_not_require_telegram_secrets(self):
        config = {
            "mode": "files",
            "feeds": [{"url": "https://subsplease.org/rss/?r=1080"}],
            "dry_run": False,
            "state_file": "unused-state.json",
            "allowed_download_hosts": [],
            "torrent_sources": [],
        }
        state = {"published": {}, "seen": {}, "posters": {}}
        with patch("app.load_config", return_value=config), \
             patch("app.load_state", return_value=state), \
             patch.dict(os.environ, {}, clear=True):
            self.assertEqual(run_app(), 0)

    def test_media_processing_failure_returns_nonzero_for_actions(self):
        config = {
            "mode": "files",
            "feeds": [],
            "dry_run": False,
            "state_file": "unused-state.json",
            "request_timeout_seconds": 30,
            "poll_limit_per_feed": 30,
            "allowed_download_hosts": [],
            "max_file_size_mb": 1800,
            "torrent_timeout_seconds": 10,
            "torrent_sources": [{
                "title": "Licensed Sample - 01 (1080p)",
                "url": "magnet:?xt=urn:btih:0123456789012345678901234567890123456789",
                "authorized_for_redistribution": True,
                "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                "authorization_reviewed_at": "2026-10-24",
            }],
        }
        state = {"published": {}, "seen": {}, "posters": {}}
        env = {
            "ANIMEHIGHBO_DRY_RUN": "false",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "@test-channel",
        }
        with patch("app.load_config", return_value=config), \
             patch("app.load_state", return_value=state), \
             patch("app.save_state"), \
             patch("app.anime_catalog_metadata", return_value={}), \
             patch("app.telegram_send_anime_poster", return_value="poster-1"), \
             patch("tools.torrent_downloader.download_authorized_torrent", side_effect=RuntimeError("simulated download failure")), \
             patch.dict(os.environ, env, clear=False):
            self.assertEqual(run_app(), 1)

    def test_torrent_only_configuration_reaches_processing_without_rss_feeds(self):
        config = {
            "mode": "files",
            "feeds": [],
            "dry_run": True,
            "state_file": "unused-state.json",
            "request_timeout_seconds": 30,
            "poll_limit_per_feed": 30,
            "allowed_download_hosts": [],
            "torrent_sources": [{
                "title": "Open Sample - 01 (1080p)",
                "url": "magnet:?xt=urn:btih:0123456789012345678901234567890123456789",
                "authorized_for_redistribution": True,
                "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
                "authorization_reviewed_at": "2026-10-24",
            }],
        }
        state = {"published": {}, "seen": {}, "posters": {}}
        with patch("app.load_config", return_value=config), \
             patch("app.load_state", return_value=state), \
             patch("app.save_state"), \
             patch("app.anime_catalog_metadata", return_value={}) as metadata, \
             patch.dict(os.environ, {"ANIMEHIGHBO_DRY_RUN": "true"}):
            self.assertEqual(run_app(), 0)
        # Dry-run must not fetch metadata or leave a poster-only record before media preparation.
        metadata.assert_not_called()


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
        self.assertEqual(episode_details("Keroro Gunsou (2026)"), ("Keroro Gunsou (2026)", ""))
        self.assertEqual(episode_details("Keroro Gunsou (2026) - 01 (1080p)"), ("Keroro Gunsou (2026)", "01"))

    def test_parses_rss_enclosure(self):
        xml = '''<?xml version="1.0"?><rss><channel><item><title>Episode 01</title><enclosure url="https://media.example.org/ep01.mkv" type="video/x-matroska"/><link>https://example.org/post</link></item></channel></rss>'''
        self.assertEqual(parse_feed(xml), [{"title": "Episode 01", "url": "https://media.example.org/ep01.mkv"}])

    def test_parses_subsplease_rss_magnet_enclosure_as_source_url(self):
        xml = '''<rss><channel><item><title>[SubsPlease] Test Anime - 03 (1080p) [ABC123].mkv</title><link>magnet:?xt=urn:btih:0123456789012345678901234567890123456789&amp;dn=Test</link><guid isPermaLink="false">ABC123</guid></item></channel></rss>'''
        entries = parse_feed(xml)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "[SubsPlease] Test Anime - 03 (1080p) [ABC123].mkv")
        self.assertTrue(entries[0]["url"].startswith("magnet:?xt=urn:btih:"))

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
            "status": "RELEASING",
            "averageScore": 87,
            "studios": {"nodes": [{"name": "Moonlight Studio"}]},
            "isAdult": False,
            "description": "A young traveler discovers a hidden world beneath the city.",
        }
        from unittest.mock import patch
        with patch("app.translate_title_to_persian", return_value="حماسه مهتاب"), patch(
            "app.translate_anime_synopsis", return_value="یه مسافر جوان دنیای پنهانی زیر شهر رو کشف می‌کنه."
        ), patch("app.anime_japanese_title", return_value="Moonlit Saga"):
            caption = build_episode_caption(
                "Moonlit Saga Episode 07 1080p", "07", metadata=metadata
            )
        self.assertIn("Moonlit Saga", caption)
        self.assertIn("حماسه مهتاب", caption)
        self.assertIn("قسمت 07", caption)
        self.assertIn("ژانر:</b> Action، Fantasy", caption)
        self.assertIn("سال پخش:</b> 2026", caption)
        self.assertIn("وضعیت پخش:</b> در حال پخش", caption)
        self.assertIn("امتیاز:</b> 8.7/10", caption)
        self.assertIn("استودیو:</b> Moonlight Studio", caption)
        self.assertIn("<blockquote expandable>📝 ", caption)
        self.assertIn("</blockquote>", caption)
        self.assertNotIn("<tg-spoiler>", caption)
        self.assertIn("@Animehighbo", caption)
        self.assertEqual(caption.splitlines()[-1], "@Animehighbo")
        self.assertLess(caption.index("📝 "), caption.rindex("@Animehighbo"))
        self.assertNotIn("1080p", caption)
        self.assertNotIn("تعداد کامل قسمت‌ها", caption)
        self.assertLessEqual(len(caption), 1024)
        self.assertNotIn("لینک تماشا", caption)

    def test_long_metadata_never_truncates_or_removes_synopsis_block(self):
        import app
        metadata = {
            "title": {"english": "A Long Example Anime Title"},
            "seasonYear": 2026,
            "episodes": 24,
            "genres": ["Action", "Adventure", "Fantasy", "Drama", "Mystery", "Sci-Fi"],
            "format": "TV",
            "status": "RELEASING",
            "averageScore": 87,
            "studios": {"nodes": [{"name": "A Very Long Animation Studio Name"}]},
            "description": "An extended catalog synopsis.",
        }
        long_synopsis = "این خلاصهٔ فارسی باید حتی وقتی اطلاعات پست طولانی است در کپشن باقی بماند. " * 30
        with patch("app.translate_title_to_persian", return_value="عنوان فارسی"), patch(
            "app.translate_anime_synopsis", return_value=long_synopsis
        ), patch("app.anime_japanese_title", return_value="Example Anime"):
            caption = app.build_episode_caption("A Long Example Anime Title - 01 (1080p)", "01", metadata=metadata)
        self.assertLessEqual(len(caption), 1000)
        self.assertIn("<blockquote expandable>📝 ", caption)
        self.assertIn("</blockquote>", caption)
        self.assertIn("@Animehighbo", caption)
        self.assertLess(caption.index("📝 "), caption.rindex("@Animehighbo"))
        self.assertGreater(len(caption.split("📝 ", 1)[1].split("</blockquote>", 1)[0]), 40)

    def test_unknown_metadata_is_not_invented(self):
        from unittest.mock import patch
        with patch("app.translate_title_to_persian", return_value="ترجمه فارسی"), patch(
            "app.anime_japanese_title", return_value="Some Anime"
        ):
            caption = build_episode_caption("Some Anime Episode 3", "3", metadata={})
        self.assertIn("<blockquote expandable>📝 —</blockquote>", caption)
        self.assertIn("@Animehighbo", caption)
        self.assertIn("سال پخش:</b> —", caption)
        self.assertIn("ژانر:</b> —", caption)
        self.assertIn("امتیاز:</b> —", caption)
        self.assertIn("رده‌بندی:</b> —", caption)

    def test_sequel_season_is_not_mislabeled_as_first_season(self):
        from unittest.mock import patch
        metadata = {
            "title": {"english": "Attack on Titan: The Final Season"},
            "format": "TV",
            "episodes": 16,
            "isAdult": False,
        }
        with patch("app.translate_title_to_persian", return_value="حمله به تایتان"), patch(
            "app.anime_japanese_title", return_value="Shingeki no Kyojin"
        ):
            caption = build_episode_caption("Attack on Titan Episode 01 720p", "01", metadata=metadata)
        self.assertIn("حمله به تایتان", caption)
        self.assertIn("@Animehighbo", caption)

    def test_processed_filename_has_only_anime_quality_and_channel(self):
        self.assertEqual(
            processed_filename("[SubsPlease] PSYREN - 03 (1080p) [ABC123].mkv", "1080p"),
            "PSYREN EP 03 1080p @Animehighbo.mkv",
        )
        self.assertEqual(
            processed_filename("Ao Ashi S2 EP 02 (1080p).mkv", "720p"),
            "Ao Ashi S2 EP 02 720p @Animehighbo.mkv",
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



class PosterFallbackTests(unittest.TestCase):
    @patch("app.requests.get")
    def test_google_image_search_extracts_original_https_image_urls(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.url = "https://www.google.com/search?tbm=isch&q=Moonlit+Saga"
        response.text = '<div>imgurl=https%3A%2F%2Fcdn.example.org%2Fmoonlit-poster.jpg</div>'
        get.return_value = response
        self.assertEqual(
            app.search_poster_image_urls("Moonlit Saga"),
            ["https://cdn.example.org/moonlit-poster.jpg"],
        )

    def test_thumbnail_never_extracts_episode_frame_when_poster_sources_fail(self):
        import tempfile
        from pathlib import Path
        import app

        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "episode.mkv"
            video.write_bytes(b"video")
            output = Path(temp) / "thumbnail.jpg"
            with patch.object(app, "search_poster_image_urls", return_value=[]), \
                 patch.object(app, "download_poster", side_effect=OSError("image services unavailable")), \
                 patch.object(app.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                 patch.object(app.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "No verified anime poster"):
                    app.create_anime_video_thumbnail(
                        "Moonlit Saga", {}, output, fallback_video_path=video
                    )
            run.assert_not_called()
            self.assertFalse(output.exists())

    def test_poster_sender_never_uses_episode_frame_as_anime_poster(self):
        import tempfile
        from pathlib import Path
        import app

        with tempfile.TemporaryDirectory() as temp:
            fallback = Path(temp) / "thumbnail.jpg"
            fallback.write_bytes(bytes.fromhex("ffd8ff") + b"x" * 1024)
            with patch.object(app, "build_episode_caption", return_value="caption"), \
                 patch.object(app, "search_poster_image_urls", return_value=[]), \
                 patch.object(app.requests, "post") as post:
                with self.assertRaisesRegex(RuntimeError, "No verified poster"):
                    app.telegram_send_anime_poster(
                        "token", "@channel", "Moonlit Saga", "07", {"coverImage": {}},
                        fallback_image_path=fallback,
                    )
        post.assert_not_called()

    def test_cli_main_configures_info_logging_before_running(self):
        with patch.object(app.logging, "basicConfig") as configure_logging, patch.object(
            app, "run", return_value=0
        ) as run:
            self.assertEqual(app.main(), 0)

        configure_logging.assert_called_once()
        self.assertEqual(configure_logging.call_args.kwargs.get("level"), app.logging.INFO)
        run.assert_called_once_with()

    def test_missing_poster_never_publishes_a_text_only_post(self):
        import app

        with patch.object(app, "build_episode_caption", return_value="caption"), \
             patch.object(app, "search_poster_image_urls", return_value=[]), \
             patch.object(app.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "No verified poster"):
                app.telegram_send_anime_poster(
                    "token", "@channel", "Moonlit Saga", "07",
                    {"coverImage": {}, "title": {"english": "Moonlit Saga", "romaji": "Tsuki no Monogatari"}},
                )
        post.assert_not_called()

    def test_unavailable_poster_sources_hold_episode_for_retry(self):
        import app

        with patch.object(app, "build_episode_caption", return_value="caption"), \
             patch.object(app, "download_poster", side_effect=OSError("catalog image unreachable")), \
             patch.object(app, "search_poster_image_urls", return_value=[]), \
             patch.object(app.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "No verified poster"):
                app.telegram_send_anime_poster(
                    "token", "@channel", "Moonlit Saga", "07",
                    {"coverImage": {"extraLarge": "https://cdn.example.org/poster.jpg"}},
                )
        post.assert_not_called()


class NoAutomaticRightsGateTests(unittest.TestCase):
    def test_direct_media_download_reaches_network_without_rights_metadata(self):
        with patch("app.requests.get", side_effect=OSError("network reached")) as get:
            with self.assertRaisesRegex(OSError, "network reached"):
                app.download_media(
                    "https://media.example.org/sample.mkv",
                    "Sample Episode 01",
                    {"allowed_download_hosts": ["media.example.org"], "download_dir": "downloads",
                     "max_file_size_mb": 10, "request_timeout_seconds": 5},
                )
        get.assert_called_once()

    def test_archive_manifest_accepts_entry_without_rights_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.json"
            path.write_text(json.dumps([{
                "title": "Legacy Saga - 01 (1080p)",
                "url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            }]), encoding="utf-8")
            entries = load_archive_manifest(str(path))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Legacy Saga - 01 (1080p)")


    def test_synopsis_translation_is_cached_for_repeated_episode_captions(self):
        from unittest.mock import Mock, patch
        import app
        app.translate_anime_synopsis.cache_clear()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text": "خلاصهٔ ترجمه‌شده"}]}}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "gemini-3.5-flash-lite"}):
            with patch("app.requests.post", return_value=response) as post:
                first = app.translate_anime_synopsis("A hero begins a long journey.")
                second = app.translate_anime_synopsis("A hero begins a long journey.")
        self.assertEqual(first, "خلاصهٔ ترجمه‌شده")
        self.assertEqual(second, first)
        post.assert_called_once()
        app.translate_anime_synopsis.cache_clear()


    def test_curated_season_cache_overrides_fuzzy_live_catalog_metadata(self):
        import app
        live = {
            "title": {"english": "Old Season"},
            "coverImage": {"extraLarge": "https://cdn.example/old.jpg"},
            "description": "Old season synopsis.",
            "seasonYear": 2023,
        }
        cached_metadata = {
            "title": {"english": "Verified Season 2", "romaji": "Verified II"},
            "coverImage": {"extraLarge": "https://cdn.example/season2.jpg"},
            "description": "Verified season-two synopsis.",
            "seasonYear": 2026,
            "metadata_source": "MyAnimeList exact-season record",
        }
        cached = {"title": "Verified Season 2", "poster_url": "https://cdn.example/season2.jpg", "metadata": cached_metadata}
        merged = app.apply_cached_anime_metadata(live, cached)
        self.assertEqual(merged["title"]["english"], "Verified Season 2")
        self.assertEqual(merged["coverImage"]["extraLarge"], "https://cdn.example/season2.jpg")
        self.assertEqual(merged["description"], "Verified season-two synopsis.")
        self.assertEqual(merged["seasonYear"], 2026)
