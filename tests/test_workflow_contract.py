import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_publisher_selects_secrets_before_stale_repository_variables(self):
        workflow = (ROOT / ".github/workflows/publish-prepared-episode.yml").read_text(encoding="utf-8")
        self.assertIn(
            "ANIMEHIGHBO_DRY_RUN_SECRET: ${{ secrets.ANIMEHIGHBO_DRY_RUN }}",
            workflow,
        )
        self.assertIn(
            "ANIMEHIGHBO_DRY_RUN_VAR: ${{ vars.ANIMEHIGHBO_DRY_RUN }}",
            workflow,
        )
        self.assertIn('if [ -n "$ANIMEHIGHBO_DRY_RUN_SECRET" ]; then', workflow)
        self.assertIn('export ANIMEHIGHBO_DRY_RUN="$ANIMEHIGHBO_DRY_RUN_SECRET"', workflow)
        self.assertIn('dry_run_source="config.json"', workflow)
        self.assertIn('get("dry_run", True)', workflow)
        self.assertIn('dry_run_state="disabled"', workflow)
        self.assertIn(
            "TELEGRAM_LOCAL_API_SECRET: ${{ secrets.TELEGRAM_LOCAL_API }}",
            workflow,
        )
        self.assertIn('if [ -n "$TELEGRAM_LOCAL_API_SECRET" ]; then', workflow)
        self.assertIn(
            "ANIMEHIGHBO_MAX_FILE_SIZE_MB_SECRET: ${{ secrets.ANIMEHIGHBO_MAX_FILE_SIZE_MB }}",
            workflow,
        )
        self.assertIn('export ANIMEHIGHBO_MAX_FILE_SIZE_MB="$ANIMEHIGHBO_MAX_FILE_SIZE_MB_SECRET"', workflow)

    def test_episode_matrix_is_parallel_and_dispatches_after_each_success(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("Select independently eligible episodes", workflow)
        self.assertIn("max-parallel: 5", workflow)
        self.assertIn("ANIMEHIGHBO_PREPARE_ONLY: 'true'", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("gh workflow run publish-prepared-episode.yml", workflow)
        self.assertIn("fail-fast: false", workflow)
        self.assertNotIn("needs.prepare.result", workflow)
        self.assertNotIn("  publish:\\n", workflow)

    def test_each_prepared_episode_dispatches_an_independent_publisher(self):
        prepare = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("actions: write", prepare)
        self.assertIn("gh workflow run publish-prepared-episode.yml", prepare)
        self.assertIn("source_run_id", prepare)
        self.assertIn("series_key", prepare)
        publisher_path = ROOT / ".github/workflows/publish-prepared-episode.yml"
        self.assertTrue(publisher_path.exists())
        publisher = publisher_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", publisher)
        self.assertIn("run-id: ${{ inputs.source_run_id }}", publisher)
        self.assertIn("group: animehighbo-publish-${{ inputs.series_key }}", publisher)
        self.assertIn("tools/merge_state.py", publisher)
        self.assertNotIn("needs.prepare.result == 'success'", prepare)

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

    def test_retention_runs_daily_and_keeps_only_latest_thirty_runs(self):
        workflow = (ROOT / ".github/workflows/retention.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '43 4 * * *'", workflow)
        self.assertIn("completed.slice(30)", workflow)
        self.assertIn('filter(run => run.status === "completed")', workflow)
        self.assertIn("Active runs are never deleted.", workflow)
        self.assertIn("actions: write", workflow)
        self.assertIn("workflow_run:", workflow)
        self.assertIn("types: [completed]", workflow)
        workflow_run_block = workflow.split("workflow_run:", 1)[1].split("\n\npermissions:", 1)[0]
        self.assertIn("workflows:", workflow_run_block)
        for name in ("CI", "Animehighbo publisher", "Animehighbo single episode publisher", "One-time licensed Morevna Telegram test", "Sync RSS input to config", "Build authorized episode archive catalog"):
            self.assertIn(f"- {name}", workflow_run_block)
        self.assertIn("group: animehighbo-run-retention", workflow)
        self.assertIn("github.event.workflow_run.name != 'Retain latest 30 workflow runs'", workflow)


    def test_publisher_state_persistence_merges_concurrent_episode_updates(self):
        workflow = (ROOT / ".github/workflows/publish-prepared-episode.yml").read_text(encoding="utf-8")
        self.assertIn("git fetch origin main", workflow)
        self.assertIn("git reset --mixed origin/main", workflow)
        self.assertIn("python tools/merge_state.py", workflow)
        self.assertIn("git push origin HEAD:main", workflow)

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

    def test_publisher_passes_only_primary_gemini_key(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("GEMINI_API_KEY: " + "$" + "{{ secrets.GEMINI_API_KEY }}", workflow)
        for name in ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
            self.assertNotIn(f"{name}:", workflow)
        self.assertNotIn("offline subtitle fallback", workflow)


    def test_one_time_delivery_is_manual_only_and_uses_one_gemini_key(self):
        workflow = (ROOT / ".github/workflows/morevna-test-delivery.yml").read_text(encoding="utf-8")
        trigger_block = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger_block)
        self.assertNotIn("push:", trigger_block)
        self.assertIn("GEMINI_API_KEY: " + "$" + "{{ secrets.GEMINI_API_KEY }}", workflow)
        for name in ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
            self.assertNotIn(f"{name}:", workflow)
        self.assertNotIn("requirements-subtitles.txt", workflow)


if __name__ == "__main__":
    unittest.main()
