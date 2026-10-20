import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_publisher_secrets_override_stale_repository_variables(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn(
            "ANIMEHIGHBO_DRY_RUN: ${{ secrets.ANIMEHIGHBO_DRY_RUN || vars.ANIMEHIGHBO_DRY_RUN || 'true' }}",
            workflow,
        )
        self.assertIn(
            "TELEGRAM_LOCAL_API: ${{ secrets.TELEGRAM_LOCAL_API || vars.TELEGRAM_LOCAL_API }}",
            workflow,
        )
        self.assertIn(
            "ANIMEHIGHBO_MAX_FILE_SIZE_MB: ${{ secrets.ANIMEHIGHBO_MAX_FILE_SIZE_MB || vars.ANIMEHIGHBO_MAX_FILE_SIZE_MB }}",
            workflow,
        )

    def test_media_dependencies_are_gated_by_actual_authorized_media_sources(self):
        workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("from app import has_file_media_source", workflow)
        self.assertIn("file_pipeline = config.get(\"mode\") != \"links\" and has_file_media_source(config)", workflow)
        self.assertIn('if [ "$ANIMEHIGHBO_FILE_PIPELINE" = "true" ]', workflow)

    def test_unverified_torrent_is_not_enabled_in_default_config(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config.get("torrent_sources", []), [])

    def test_retention_runs_daily_and_keeps_only_latest_thirty_runs(self):
        workflow = (ROOT / ".github/workflows/retention.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '43 4 * * *'", workflow)
        self.assertIn("runs.slice(30)", workflow)
        self.assertIn("actions: write", workflow)


if __name__ == "__main__":
    unittest.main()
