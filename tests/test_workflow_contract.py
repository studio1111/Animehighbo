import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):

    def test_channel_rules_and_manual_exclusion_trigger_are_enforced(self):
        rules = (ROOT / "CHANNEL_RULES.md").read_text(encoding="utf-8")
        dispatcher = (ROOT / ".github/workflows/prepare-episodes.yml").read_text(encoding="utf-8")
        self.assertIn("No per-anime approval gate", rules)
        self.assertIn("data/archive_exclusions.json", dispatcher)
        self.assertNotIn("data/anime_publication_approvals.json", dispatcher)
        self.assertFalse((ROOT / ".github/workflows/manage-anime-publication-approval.yml").exists())
        self.assertTrue((ROOT / "docs/operations/reliability-runbook.md").exists())

    def test_dispatcher_runs_five_independent_episode_workers_without_pat(self):
        dispatcher = (ROOT / ".github/workflows/prepare-episodes.yml").read_text(encoding="utf-8")
        worker = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        self.assertIn("max-parallel: 5", dispatcher)
        self.assertIn("fail-fast: false", dispatcher)
        self.assertIn("gh workflow run prepare-one-episode.yml --ref main", dispatcher)
        self.assertIn("matrix: ${{ fromJSON(needs.queue.outputs.matrix) }}", dispatcher)
        self.assertIn("workflow_call:", worker)
        self.assertIn("ANIMEHIGHBO_PREPARE_ONLY: 'true'", worker)
        self.assertIn("ANIMEHIGHBO_PUBLISH_PREPARED: 'true'", worker)
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", worker)
        self.assertNotIn("GH_WORKFLOW_TOKEN", dispatcher + worker)
        self.assertNotIn("createDispatchEvent", dispatcher + worker)
        for name in ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
            self.assertNotIn(name, worker)
        self.assertIn("requirements-subtitles.txt", worker)
        self.assertIn("Install offline subtitle translator fallback", worker)
        self.assertIn("local Argos", worker)

    def test_publisher_workflow_has_no_lock_or_permission_gate(self):
        workflow = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        publish = workflow.index("Publish the episode poster and three reply videos")
        persist = workflow.index("Persist duplicate-prevention state without overwriting parallel workers")
        self.assertLess(publish, persist)
        self.assertNotIn("publisher_lock", workflow)
        self.assertNotIn("publisher.lock", workflow)
        self.assertFalse((ROOT / "tools/publisher_lock.py").exists())
        self.assertIn("git reset --hard origin/main", workflow)

    def test_dispatcher_launches_independent_workers_and_skips_active_episodes(self):
        dispatcher = (ROOT / ".github/workflows/prepare-episodes.yml").read_text(encoding="utf-8")
        worker = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        queue = (ROOT / "tools/episode_queue.py").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", worker)
        self.assertIn("run-name: Animehighbo worker ${{ inputs.identity }}", worker)
        self.assertIn("gh workflow run prepare-one-episode.yml --ref main", dispatcher)
        self.assertIn('for status in ("queued", "in_progress"):', dispatcher)
        self.assertIn("ANIMEHIGHBO_ACTIVE_IDENTITIES", dispatcher)
        self.assertIn("limit_to_available_slots(entries, active_identities, max_workers=5)", queue)
        self.assertIn("workflow_run:", dispatcher)
        self.assertIn('workflows: ["Animehighbo single-episode worker"]', dispatcher)
        self.assertIn("completed_identity:", dispatcher)
        self.assertIn("limit_to_available_slots(entries, active_identities, max_workers=5)", queue)
        self.assertIn("name: Refill episode queue after this worker finishes", worker)
        self.assertIn('gh workflow run prepare-episodes.yml --ref main', worker)
        self.assertIn("actions: write", worker)
        self.assertIn("max-parallel: 5", dispatcher)

    def test_each_episode_publishes_independently_and_serializes_state_writes(self):
        workflow = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("actions/download-artifact@v4", workflow)
        self.assertNotIn("concurrency:", workflow.split("  publish:", 1)[1].split("  refill:", 1)[0])
        self.assertNotIn("publisher.lock", workflow)
        self.assertNotIn("publisher-series-" + "${{ inputs.series_key }}", workflow)
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

    def test_retention_only_reacts_to_production_branch_workflows(self):
        workflow = (ROOT / ".github/workflows/retention.yml").read_text(encoding="utf-8")
        workflow_run = workflow.split("  workflow_run:", 1)[1].split("\n\npermissions:", 1)[0]
        self.assertIn("branches: [main]", workflow_run)

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

    def test_manual_delivery_workflow_uses_one_gemini_key(self):
        workflow = (ROOT / ".github/workflows/morevna-test-delivery.yml").read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("push:", trigger)
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", workflow)
        for name in ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
            self.assertNotIn(name, workflow)
        self.assertNotIn("requirements-subtitles.txt", workflow)

    def test_video_posts_require_poster_thumbnails_after_media_and_subtitle_preparation(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        group_flow = source.split("# Each anime/episode group is handled independently", 1)[1]
        download = group_flow.index("download_authorized_torrent(")
        subtitle = group_flow.index("process_bilingual_subtitles(")
        encode = group_flow.index("encode_h265(")
        thumbnail = group_flow.index("create_anime_video_thumbnail(")
        upload = group_flow.index("telegram_upload_and_send(")
        self.assertLess(download, subtitle)
        self.assertLess(subtitle, encode)
        self.assertLess(encode, thumbnail)
        self.assertLess(thumbnail, upload)
        poster = group_flow.index("poster_message_id = telegram_send_anime_poster(")
        self.assertLess(thumbnail, poster)
        self.assertLess(poster, upload)

    def test_all_three_qualities_are_encoded_before_video_posts_are_sent(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        group_flow = source.split("# Each anime/episode group is handled independently", 1)[1]
        subtitle = group_flow.index("process_bilingual_subtitles(")
        encode = group_flow.index("encode_h265(")
        thumbnail = group_flow.index("create_anime_video_thumbnail(")
        upload = group_flow.index("telegram_upload_and_send(")
        self.assertLess(subtitle, encode)
        self.assertLess(encode, thumbnail)
        self.assertLess(thumbnail, upload)

    def test_one_anime_failure_does_not_break_processing_of_other_groups(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("stopping before the next anime", source)
        self.assertIn("if group_failed:\n                continue", source)
        self.assertIn("Could not complete anime group", source)

    def test_archive_catalog_workflow_only_writes_unapproved_candidates(self):
        workflow_path = ROOT / ".github/workflows/build-archive-catalog.yml"
        self.assertTrue(workflow_path.exists())
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("data/archive_candidates.json", workflow)
        self.assertNotIn("data/archive_manifest.json", workflow)
        self.assertNotIn("--confirm-redistribution-rights", workflow)
        self.assertIn("review_required", workflow)
        self.assertIn('"authorized_for_redistribution" in row', workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn('git status --porcelain -- data/archive_candidates.json', workflow)
        self.assertNotIn("git diff --quiet -- data/archive_candidates.json", workflow)
        self.assertNotIn("python app.py", workflow)


    def test_media_tool_installation_is_bounded_and_installs_ffmpeg(self):
        workflow = (ROOT / ".github/workflows/prepare-one-episode.yml").read_text(encoding="utf-8")
        self.assertIn("sudo sed -i", workflow)
        self.assertIn("https://archive.ubuntu.com/ubuntu", workflow)
        self.assertIn("sudo timeout 300 apt-get -o Acquire::Retries=3 update", workflow)
        self.assertIn("sudo timeout 600 apt-get -o Acquire::Retries=3 install -y --no-install-recommends ffmpeg aria2 fonts-noto-core", workflow)
        self.assertIn("timeout-minutes: 15", workflow)
        self.assertNotIn("command -v ffmpeg\n          sudo timeout 180 apt-get install", workflow)
        publisher = workflow.split("  publish:", 1)[1]
        self.assertIn("sudo timeout 300 apt-get -o Acquire::Retries=3 update", publisher)
        self.assertIn("sudo timeout 600 apt-get -o Acquire::Retries=3 install -y --no-install-recommends ffmpeg", publisher)
        self.assertIn("command -v ffmpeg", publisher)


if __name__ == "__main__":
    unittest.main()
