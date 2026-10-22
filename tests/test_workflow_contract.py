import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_dispatcher_runs_five_independent_episode_workers_without_pat(self):
        dispatcher = (ROOT / ".github/workflows/prepare-episodes.yml").read_text(encoding="utf-8")
        worker = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        self.assertIn("max-parallel: 5", dispatcher)
        self.assertIn("fail-fast: false", dispatcher)
        self.assertIn("uses: ./.github/workflows/prepare-one-episode.yml", dispatcher)
        self.assertIn("matrix: ${{ fromJSON(needs.queue.outputs.matrix) }}", dispatcher)
        self.assertIn("workflow_call:", worker)
        self.assertIn("ANIMEHIGHBO_PREPARE_ONLY: 'true'", worker)
        self.assertIn("ANIMEHIGHBO_PUBLISH_PREPARED: 'true'", worker)
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", worker)
        self.assertNotIn("GH_WORKFLOW_TOKEN", dispatcher + worker)
        self.assertNotIn("createDispatchEvent", dispatcher + worker)
        for name in ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
            self.assertNotIn(name, worker)
        self.assertNotIn("requirements-subtitles.txt", worker)
        self.assertNotIn("Install offline subtitle fallback", worker)

    def test_global_publisher_lock_keeps_each_poster_and_three_files_contiguous(self):
        workflow = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        acquire = workflow.index("Acquire global Telegram publisher lock")
        refresh = workflow.index("Refresh publication state after acquiring the global lock")
        publish = workflow.index("Publish the episode poster and three reply videos")
        persist = workflow.index("Persist duplicate-prevention state without overwriting parallel workers")
        release = workflow.index("Release global Telegram publisher lock")
        self.assertLess(acquire, refresh)
        self.assertLess(refresh, publish)
        self.assertLess(publish, persist)
        self.assertLess(persist, release)
        lock_helper = (ROOT / "tools/publisher_lock.py").read_text(encoding="utf-8")
        self.assertIn("data/publisher.lock", lock_helper)
        self.assertIn("python tools/publisher_lock.py acquire", workflow)
        self.assertIn("python tools/publisher_lock.py release", workflow)
        release_step = workflow.split("Release global Telegram publisher lock", 1)[1]
        self.assertIn("if: always()", release_step)
        self.assertIn("git reset --hard origin/main", workflow)

    def test_each_episode_publishes_independently_and_serializes_state_writes(self):
        workflow = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("actions/download-artifact@v4", workflow)
        self.assertIn("group: animehighbo-publisher-${{ inputs.identity }}", workflow)
        self.assertIn("git fetch origin main", workflow)
        self.assertIn("if key not in current_section:", workflow)
        self.assertIn("git push origin HEAD:main", workflow)
        self.assertNotIn("createDispatchEvent", workflow)
        self.assertFalse((ROOT / ".github/workflows/publish.yml").exists())

    def test_publisher_resolves_local_telegram_api_and_size_limit(self):
        workflow = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
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
        self.assertIn("- Animehighbo episode dispatcher", workflow)

    def test_manual_delivery_workflow_uses_one_gemini_key_without_offline_fallback(self):
        workflow = (ROOT / ".github/workflows/morevna-test-delivery.yml").read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("push:", trigger)
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", workflow)
        for name in ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
            self.assertNotIn(name, workflow)
        self.assertNotIn("requirements-subtitles.txt", workflow)

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

    def test_one_anime_failure_does_not_break_processing_of_other_groups(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("stopping before the next anime", source)
        self.assertIn("if group_failed:\n                continue", source)
        self.assertIn("Could not complete anime group", source)

    def test_archive_catalog_workflow_requires_rights_confirmation_and_does_not_publish(self):
        workflow_path = ROOT / ".github/workflows/build-archive-catalog.yml"
        self.assertTrue(workflow_path.exists())
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("--confirm-redistribution-rights", workflow)
        self.assertIn("data/archive_manifest.json", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertNotIn("python app.py", workflow)


    def test_media_tool_installation_is_bounded_and_installs_ffmpeg(self):
        workflow = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        self.assertIn("sudo sed -i", workflow)
        self.assertIn("https://archive.ubuntu.com/ubuntu", workflow)
        self.assertIn("sudo timeout 120 apt-get update", workflow)
        self.assertIn("sudo timeout 120 apt-get install -y --no-install-recommends ffmpeg aria2 fonts-noto-core", workflow)
        self.assertIn("timeout-minutes: 5", workflow)
        self.assertNotIn("command -v ffmpeg\n          sudo timeout 180 apt-get install", workflow)
        self.assertIn("sudo timeout 120 apt-get install -y ffmpeg fonts-noto-core", workflow)


if __name__ == "__main__":
    unittest.main()
