import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_publisher_selects_secrets_before_stale_repository_variables(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
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

    def test_media_dependencies_are_gated_by_actual_authorized_media_sources(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("from app import has_file_media_source", workflow)
        self.assertIn("file_pipeline = config.get(\"mode\") != \"links\" and has_file_media_source(config)", workflow)
        self.assertIn('if [ "$ANIMEHIGHBO_FILE_PIPELINE" = "true" ]', workflow)

    def test_unverified_torrent_is_not_enabled_in_default_config(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config.get("torrent_sources", []), [])

    def test_standalone_poster_only_monitor_is_removed(self):
        self.assertFalse((ROOT / ".github/workflows/subsplease-monitor.yml").exists())

    def test_retention_runs_daily_and_keeps_only_latest_thirty_runs(self):
        workflow = (ROOT / ".github/workflows/retention.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '43 4 * * *'", workflow)
        self.assertIn("runs.slice(30)", workflow)
        self.assertIn("actions: write", workflow)


if __name__ == "__main__":
    unittest.main()
