import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_publisher_uses_only_one_gemini_secret_and_no_offline_fallback(self):
        prepare = (ROOT / ".github/workflows/prepare-episodes.yml").read_text(encoding="utf-8")
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", prepare)
        for name in ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
            self.assertNotIn(name, prepare)
        self.assertNotIn("requirements-subtitles.txt", prepare)
        self.assertNotIn("Install offline subtitle fallback", prepare)
        self.assertIn("ANIMEHIGHBO_PREPARE_ONLY: 'true'", prepare)
        self.assertIn("fail-fast: false", prepare)
        self.assertIn("actions/upload-artifact@v4", prepare)

    def test_publisher_is_a_separate_workflow_and_runs_after_failed_prepare_workers(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_run:", trigger)
        self.assertIn("Animehighbo episode preparation", trigger)
        self.assertIn("types: [completed]", trigger)
        self.assertIn("github.event.workflow_run.conclusion != 'cancelled'", workflow)
        self.assertIn("github.event.workflow_run.id", workflow)
        self.assertIn("actions/download-artifact@v4", workflow)
        self.assertNotIn("needs.prepare.result == 'success'", workflow)
        self.assertIn("group: animehighbo-publisher", workflow)
        self.assertIn("git fetch origin main", workflow)
        self.assertIn("git rebase origin/main", workflow)
        self.assertIn("git push origin HEAD:main", workflow)

    def test_publisher_resolves_local_telegram_api_and_size_limit(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_LOCAL_API_SECRET: ${{ secrets.TELEGRAM_LOCAL_API }}", workflow)
        self.assertIn("if [ -n \"$TELEGRAM_LOCAL_API_SECRET\" ]; then", workflow)
        self.assertIn("ANIMEHIGHBO_MAX_FILE_SIZE_MB_SECRET: ${{ secrets.ANIMEHIGHBO_MAX_FILE_SIZE_MB }}", workflow)
        self.assertIn('export ANIMEHIGHBO_MAX_FILE_SIZE_MB="$ANIMEHIGHBO_MAX_FILE_SIZE_MB_SECRET"', workflow)

    def test_poster_synopsis_uses_expandable_blockquote_not_spoiler_mask(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        caption_builder = source.split("def build_episode_caption(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("<blockquote expandable>", caption_builder)
        self.assertNotIn("<tg-spoiler>", caption_builder)
        self.assertIn("</blockquote>", caption_builder)

    def test_unverified_torrent_is_not_enabled_in_default_config(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config.get("torrent_sources", []), [])

    def test_current_publish_config_keeps_bilingual_subtitles_soft(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertTrue(config.get("bilingual_subtitles"))
        self.assertTrue(config.get("encode_h265"))
        self.assertFalse(config.get("burn_persian_subtitles"))

    def test_standalone_poster_only_monitor_is_removed(self):
        self.assertFalse((ROOT / ".github/workflows/subsplease-monitor.yml").exists())

    def test_retention_runs_daily_and_keeps_only_latest_thirty_completed_runs(self):
        workflow = (ROOT / ".github/workflows/retention.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '43 4 * * *'", workflow)
        self.assertIn("completed.slice(30)", workflow)
        self.assertIn('filter(run => run.status === "completed")', workflow)
        self.assertIn("Active runs are never deleted.", workflow)
        self.assertIn("actions: write", workflow)
        self.assertIn("workflow_run:", workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("group: animehighbo-run-retention", workflow)

    def test_archive_poster_is_not_published_before_media_and_subtitle_preparation(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        group_flow = source.split("# Each anime/episode group is handled independently", 1)[1]
        download = group_flow.index("download_authorized_torrent(")
        subtitle = group_flow.index("process_bilingual_subtitles(")
        poster = group_flow.index("poster_message_id = telegram_send_anime_poster(")
        self.assertLess(download, subtitle)
        self.assertLess(subtitle, poster)

    def test_all_three_qualities_are_encoded_before_first_poster_is_sent(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        group_flow = source.split("# Each anime/episode group is handled independently", 1)[1]
        subtitle = group_flow.index("process_bilingual_subtitles(")
        encode = group_flow.index("encode_h265(")
        poster = group_flow.index("poster_message_id = telegram_send_anime_poster(")
        upload = group_flow.index("telegram_upload_and_send(")
        self.assertLess(subtitle, encode)
        self.assertLess(encode, poster)
        self.assertLess(poster, upload)

    def test_archive_catalog_workflow_requires_rights_confirmation_and_does_not_publish(self):
        workflow_path = ROOT / ".github/workflows/build-archive-catalog.yml"
        self.assertTrue(workflow_path.exists())
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("--confirm-redistribution-rights", workflow)
        self.assertIn("data/archive_manifest.json", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertNotIn("python app.py", workflow)


if __name__ == "__main__":
    unittest.main()
